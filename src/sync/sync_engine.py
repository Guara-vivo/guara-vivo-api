import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
from sqlalchemy import select

from database import OFFLINE_MODE
from database_local import AsyncSessionLocal
from models_local import SyncQueueItem as SyncQueueItemLocal
from models_local import IdMapping as IdMappingLocal


@dataclass
class SyncResult:
    synced_count: int = 0
    failed_count: int = 0
    errors: list[dict] = field(default_factory=list)


ProgressCallback = Callable[[dict[str, Any]], None]


class SyncEngine:
    def __init__(self):
        self.api_base_url: str = os.getenv("REMOTE_API_URL", "")
        self._access_token: str | None = None
        self._refresh_token: str | None = None

    async def _load_tokens(self) -> bool:
        auth_file = os.getenv("LOCAL_DB_PATH", "./data/.auth_tokens")
        auth_path = os.path.join(os.path.dirname(auth_file), ".auth_tokens")
        try:
            with open(auth_path) as f:
                data = json.load(f)
            self._refresh_token = data.get("refresh_token")
            self.api_base_url = data.get("api_base_url", self.api_base_url)
            return bool(self._refresh_token)
        except (FileNotFoundError, json.JSONDecodeError):
            return False

    async def ensure_authenticated(self) -> bool:
        if self._access_token:
            return True
        if not await self._load_tokens():
            return False
        if not self._refresh_token:
            return False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self.api_base_url}/users/refresh",
                    json={"refresh_token": self._refresh_token},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    self._access_token = data["access_token"]
                    self._refresh_token = data["refresh_token"]
                    await self._save_tokens()
                    return True
                return False
        except Exception:
            return False

    async def _save_tokens(self) -> None:
        auth_file = os.getenv("LOCAL_DB_PATH", "./data/.auth_tokens")
        auth_path = os.path.join(os.path.dirname(auth_file), ".auth_tokens")
        os.makedirs(os.path.dirname(auth_path), exist_ok=True)
        with open(auth_path, "w") as f:
            json.dump({
                "refresh_token": self._refresh_token,
                "api_base_url": self.api_base_url,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }, f)

    async def check_remote_health(self) -> bool:
        if not self.api_base_url:
            return False
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.api_base_url}/health")
                return resp.status_code == 200
        except Exception:
            return False

    async def sync_all(self, progress_callback: ProgressCallback | None = None) -> SyncResult:
        result = SyncResult()
        if not await self.ensure_authenticated():
            result.errors.append({"error": "Authentication failed"})
            return result

        async with AsyncSessionLocal() as db:
            db_result = await db.execute(
                select(SyncQueueItemLocal)
                .where(SyncQueueItemLocal.status == "pending")
                .order_by(SyncQueueItemLocal.created_at.asc())
            )
            pending_items = list(db_result.scalars().all())

            for item in pending_items:
                try:
                    success = False
                    if item.operation in ("create_record", "create_record_with_images"):
                        success = await self._sync_create_record(db, item)
                    elif item.operation == "update_record":
                        success = await self._sync_update_record(item)
                    elif item.operation == "delete_record":
                        success = await self._sync_delete_record(item)

                    if success:
                        item.status = "completed"
                        item.synced_at = datetime.now(timezone.utc)
                        result.synced_count += 1
                    else:
                        item.status = "failed"
                        item.retry_count = (item.retry_count or 0) + 1
                        result.failed_count += 1

                    await db.commit()

                    if progress_callback:
                        progress_callback({
                            "synced": result.synced_count,
                            "failed": result.failed_count,
                            "total": len(pending_items),
                            "local_record_id": item.local_record_id,
                            "remote_record_id": item.remote_record_id,
                        })

                except Exception as exc:
                    item.status = "failed"
                    item.error = str(exc)
                    item.retry_count = (item.retry_count or 0) + 1
                    await db.commit()
                    result.failed_count += 1
                    result.errors.append({"local_record_id": item.local_record_id, "error": str(exc)})

        await self._pull_analyses()
        return result

    async def _sync_create_record(self, db, item: SyncQueueItemLocal) -> bool:
        payload = json.loads(item.payload)
        image_urls = list(payload.get("images", []))

        if item.image_local_paths:
            local_paths = json.loads(item.image_local_paths)
            for local_path in local_paths:
                from storage_local import get_image_absolute_path
                abs_path = get_image_absolute_path(local_path)
                if abs_path:
                    files = {"images": (os.path.basename(str(abs_path)), abs_path.read_bytes(), "image/jpeg")}
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        headers = {"Authorization": f"Bearer {self._access_token}"}
                        resp = await client.post(
                            f"{self.api_base_url}/records/upload",
                            headers=headers,
                            data={
                                "latitude_camera": payload["latitude_camera"],
                                "longitude_camera": payload["longitude_camera"],
                                "behavior": json.dumps(payload.get("behavior", [])),
                                "date_time": payload["date_time"],
                            },
                            files=files,
                        )
                        if resp.status_code in (200, 201):
                            remote_record = resp.json()
                            item.remote_record_id = remote_record["id"]
                            mapping = IdMappingLocal(
                                local_id=item.local_record_id,
                                remote_id=remote_record["id"],
                                entity_type="record",
                                synced_at=datetime.now(timezone.utc),
                            )
                            db.add(mapping)
                            return True
                        return False

        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
            }
            resp = await client.post(
                f"{self.api_base_url}/records/",
                headers=headers,
                json=payload,
            )
            if resp.status_code in (200, 201):
                remote_record = resp.json()
                item.remote_record_id = remote_record["id"]
                mapping = IdMappingLocal(
                    local_id=item.local_record_id,
                    remote_id=remote_record["id"],
                    entity_type="record",
                    synced_at=datetime.now(timezone.utc),
                )
                db.add(mapping)
                return True
            return False

    async def _sync_update_record(self, item) -> bool:
        if not item.remote_record_id:
            return False
        payload = json.loads(item.payload)
        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
            }
            resp = await client.put(
                f"{self.api_base_url}/records/{item.remote_record_id}",
                headers=headers,
                json=payload,
            )
            return resp.status_code in (200, 201)

    async def _sync_delete_record(self, item) -> bool:
        if not item.remote_record_id:
            return False
        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {"Authorization": f"Bearer {self._access_token}"}
            resp = await client.delete(
                f"{self.api_base_url}/records/{item.remote_record_id}",
                headers=headers,
            )
            return resp.status_code in (200, 204)

    async def _pull_analyses(self) -> int:
        count = 0
        async with AsyncSessionLocal() as db:
            db_result = await db.execute(
                select(IdMappingLocal).where(IdMappingLocal.entity_type == "record")
            )
            mappings = list(db_result.scalars().all())

            for mapping in mappings:
                try:
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        headers = {"Authorization": f"Bearer {self._access_token}"}
                        resp = await client.get(
                            f"{self.api_base_url}/records/{mapping.remote_id}/detail",
                            headers=headers,
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            if data.get("analysis"):
                                # Update local record with analysis data
                                analysis = data["analysis"]
                                analysis_mapping = IdMappingLocal(
                                    local_id=mapping.local_id,
                                    remote_id=analysis["id"],
                                    entity_type="analysis",
                                    synced_at=datetime.now(timezone.utc),
                                )
                                db.add(analysis_mapping)
                                count += 1
                except Exception:
                    pass

            await db.commit()
        return count