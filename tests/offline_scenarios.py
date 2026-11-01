"""End-to-end scenarios for OFFLINE_MODE.

Run as a script in its own process (see test_offline_mode.py): OFFLINE_MODE is read when
``database`` is first imported, so it cannot share a process with the online tests.
The remote server is faked with httpx.MockTransport.
"""
import asyncio
import json
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp()
os.environ.update(
    OFFLINE_MODE="true",
    LOCAL_DB_PATH=f"{TMP}/db/guaravivo.db",
    LOCAL_STORAGE_PATH=f"{TMP}/storage",
    DATABASE_URL="postgres://user:password@localhost:5432/db",
    JWT_SECRET_KEY="test-secret",
    APP_ENV="test",
    ADMIN_EMAIL="admin@example.com",
    ADMIN_PASSWORD="secret123",
    REMOTE_API_URL="http://remote.test",
)
sys.path.insert(0, str(ROOT / "src"))

magic_stub = types.ModuleType("magic")
magic_stub.from_buffer = lambda _file_bytes, mime=True: "image/jpeg"
sys.modules["magic"] = magic_stub

import httpx  # noqa: E402

import main  # noqa: E402
from schemas import OfflineConfigureRequest  # noqa: E402
from sync import sync_background, sync_engine  # noqa: E402
from sync.sync_engine import SyncEngine, SyncInProgress  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200 + b"\xff\xd9"
REMOTE_USER_ID = 77
REMOTE_USER_B = 88
RECORD = {
    "latitude_camera": 1,
    "longitude_camera": 2,
    "behavior": ["voando"],
    "date_time": "2026-01-01T10:00:00Z",
}


class FakeRemote:
    """Just enough of the online API for the sync engine."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[dict] = []
        self.records: dict[int, dict] = {}
        self.next_id = 500
        self.fail_posts = 0
        self.down = False
        self.refresh_calls = 0
        self.uploaded_image_counts: list[int] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("link is down")
        path, method = request.url.path, request.method
        self.calls.append((method, path))

        if path == "/users/refresh":
            self.refresh_calls += 1
            owner_b = "B" in json.loads(request.content)["refresh_token"]
            return httpx.Response(
                200,
                json={
                    "access_token": "access",
                    "refresh_token": f"refresh-{'B' if owner_b else 'A'}-{self.refresh_calls}",
                    "token_type": "bearer",
                    "user": {"id": REMOTE_USER_B if owner_b else REMOTE_USER_ID},
                },
            )
        if method == "GET" and path.endswith("/detail"):
            return httpx.Response(200, json={"analysis": None})
        if method == "POST" and path in ("/records/upload", "/records/"):
            if self.fail_posts:
                self.fail_posts -= 1
                return httpx.Response(500, text="boom")
            self.next_id += 1
            if path == "/records/upload":
                count = request.content.count(b'name="images"')
                self.uploaded_image_counts.append(count)
                images = [f"https://cdn.test/{self.next_id}/{i}.jpg" for i in range(count)]
            else:
                body = json.loads(request.content)
                self.bodies.append(body)
                images = body["images"]
            self.records[self.next_id] = {"id": self.next_id, "images": images, "status": "completed"}
            return httpx.Response(200, json=self.records[self.next_id])

        record_id = int(path.rsplit("/", 1)[-1])
        if method == "GET":
            return httpx.Response(200, json=self.records[record_id])
        if method == "PUT":
            body = json.loads(request.content)
            self.bodies.append(body)
            return httpx.Response(200, json={**self.records[record_id], **body})
        if method == "DELETE":
            self.records.pop(record_id, None)
            return httpx.Response(200, json={"detail": "deleted"})
        return httpx.Response(404)


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok  {message}")


async def scenarios():
    remote = FakeRemote()
    engine_factory = lambda: SyncEngine(transport=httpx.MockTransport(remote.handler))  # noqa: E731

    async with main.app.router.lifespan_context(main.app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://local") as c:
            # --- fresh install: login works and /local-storage is mounted -----------------
            login = await c.post("/users/login", json={"email": "admin@example.com", "password": "secret123"})
            check(login.status_code == 200, "fresh install: seeded admin can log in")
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            uid = login.json()["user"]["id"]

            # What /offline/configure persists after a successful remote login.
            sync_engine.save_refresh_token(uid, "refresh-A")

            # --- /offline/* requires authentication -----------------------------------------
            for method, path in [
                ("GET", "/offline/status"),
                ("GET", "/offline/sync/history"),
                ("POST", "/offline/sync/trigger"),
                ("POST", "/offline/configure"),
            ]:
                r = await c.request(method, path, json={} if method == "POST" else None)
                check(r.status_code == 401, f"{method} {path} rejects anonymous callers")
            check(
                "api_base_url" not in OfflineConfigureRequest.model_fields,
                "configure cannot be pointed at an arbitrary server",
            )

            # --- upload: no 500, one queue item, images served ------------------------------
            upload = await c.post(
                "/records/upload",
                headers=headers,
                data=RECORD | {"behavior": ["voando"]},
                files=[("images", (f"{i}.jpg", JPEG, "image/jpeg")) for i in range(3)],
            )
            check(upload.status_code == 200, "offline upload succeeds (datetime is serialisable)")
            uploaded = upload.json()
            image_response = await c.get(uploaded["images"][0])
            check(image_response.status_code == 200, "/local-storage serves images on a fresh install")

            history = (await c.get("/offline/sync/history", headers=headers)).json()
            check(history["total"] == 1, "upload queues exactly one sync item")
            check(history["items"][0]["operation"] == "create_record_with_images", "upload item carries the images")

            # --- sync: all images sent, mapping stored --------------------------------------
            result = await engine_factory().sync_all()
            check(result.synced_count == 1 and result.failed_count == 0, f"upload item syncs {result.errors}")
            check(remote.uploaded_image_counts == [3], "all 3 images go to the remote in one request")

            detail = (await c.get(f"/records/{uploaded['id']}", headers=headers)).json()
            check(detail["synced"] and detail["sync_status"] == "synced", "record reports synced after sync")

            # --- update after sync resolves the remote id -----------------------------------
            put = await c.put(
                f"/records/{uploaded['id']}",
                headers=headers,
                json=RECORD | {"images": uploaded["images"], "user_id": uid, "status": "pending", "latitude_camera": 9},
            )
            check(put.status_code == 200, "offline update succeeds (datetime is serialisable)")
            result = await engine_factory().sync_all()
            put_body = remote.bodies[-1]
            check(result.synced_count == 1, "update item syncs")
            check(put_body["latitude_camera"] == 9, "update reaches the remote")
            check(put_body["user_id"] == REMOTE_USER_ID, "update uses the remote user id")
            check(put_body["images"][0].startswith("https://cdn.test"), "update keeps the remote image URLs")
            check(put_body["status"] == "completed", "update keeps the remote status")

            # --- delete after sync is propagated --------------------------------------------
            check((await c.delete(f"/records/{uploaded['id']}", headers=headers)).status_code == 200, "delete ok")
            result = await engine_factory().sync_all()
            check(("DELETE", "/records/501") in remote.calls and result.synced_count == 1, "delete reaches the remote")

            # --- create + update before any sync run, in order ------------------------------
            create = await c.post(
                "/records/", headers=headers, json=RECORD | {"images": ["https://x.test/a.jpg"], "user_id": uid}
            )
            rid = create.json()["id"]
            await c.put(
                f"/records/{rid}",
                headers=headers,
                json=RECORD | {"images": ["https://x.test/a.jpg"], "user_id": uid, "status": "pending"},
            )
            result = await engine_factory().sync_all()
            check(result.synced_count == 2 and result.failed_count == 0, f"create then update both sync in one run {result}")
            check(
                [b["user_id"] for b in remote.bodies if b.get("images") == ["https://x.test/a.jpg"]][0]
                == REMOTE_USER_ID,
                "create uses the remote user id instead of the local one",
            )

            # --- deleting a record that never synced drops its queue items ------------------
            create = await c.post(
                "/records/", headers=headers, json=RECORD | {"images": ["https://x.test/b.jpg"], "user_id": uid}
            )
            rid = create.json()["id"]
            before = len(remote.calls)
            await c.delete(f"/records/{rid}", headers=headers)
            result = await engine_factory().sync_all()
            check(result.synced_count == 0 and result.failed_count == 0, "never-synced record is not resurrected")
            check(
                all(path != "/records/" for _, path in remote.calls[before:]),
                "no remote create is made for a record deleted offline",
            )

            # --- failed items are retried, with the reason recorded -------------------------
            await c.post(
                "/records/", headers=headers, json=RECORD | {"images": ["https://x.test/c.jpg"], "user_id": uid}
            )
            remote.fail_posts = 1
            result = await engine_factory().sync_all()
            check(result.failed_count == 1, "remote error marks the item failed")
            items = (await c.get("/offline/sync/history", headers=headers)).json()["items"]
            failed = [i for i in items if i["status"] == "failed"]
            check(len(failed) == 1 and "HTTP 500" in failed[0]["error"], "failure reason is stored")
            status = (await c.get("/offline/status", headers=headers)).json()
            check(status["pending_sync"] == 1, "failed item still counts as pending in /offline/status")
            result = await engine_factory().sync_all()
            check(result.synced_count == 1 and result.failed_count == 0, "failed item is retried and succeeds")

            # --- a dropped link does not burn retries ---------------------------------------
            await c.post(
                "/records/", headers=headers, json=RECORD | {"images": ["https://x.test/d.jpg"], "user_id": uid}
            )
            remote.down = True
            for _ in range(sync_engine.MAX_RETRIES + 2):
                result = await engine_factory().sync_all()
            check(result.failed_count == 0 and result.errors, "network errors are reported, not counted as failures")
            check(
                (await c.get("/offline/status", headers=headers)).json()["pending_sync"] == 1,
                "item survives more link drops than MAX_RETRIES",
            )
            remote.down = False
            result = await engine_factory().sync_all()
            check(result.synced_count == 1 and result.failed_count == 0, "item syncs once the link is back")

            # --- exhausted failures: dependants fail instead of waiting forever; manual retry
            await c.post(
                "/records/", headers=headers, json=RECORD | {"images": ["https://x.test/e.jpg"], "user_id": uid}
            )
            remote.fail_posts = sync_engine.MAX_RETRIES
            for _ in range(sync_engine.MAX_RETRIES):
                await engine_factory().sync_all()
            check(await sync_engine.count_syncable(uid) == 0, "item stops retrying after MAX_RETRIES server errors")
            history = (await c.get("/offline/sync/history", headers=headers)).json()["items"]
            exhausted = [i for i in history if i["status"] == "failed"][0]
            await c.put(
                f"/records/{exhausted['local_record_id']}",
                headers=headers,
                json=RECORD | {"images": ["https://x.test/e.jpg"], "user_id": uid, "status": "pending"},
            )
            result = await engine_factory().sync_all()
            check(
                result.failed_count == 1 and result.deferred_count == 0,
                "update of a record whose create gave up fails instead of waiting forever",
            )
            check(await sync_engine.reset_failed(uid) == 2, "explicit retry resets the failed items")
            result = await engine_factory().sync_all()
            check(result.synced_count == 2 and result.failed_count == 0, "reset items sync")

            # --- two local users stay isolated ----------------------------------------------
            signup = await c.post("/users/", json={"name": "B", "email": "b@example.com", "password": "secret456"})
            check(signup.status_code == 200, "a second local user can sign up")
            login_b = await c.post("/users/login", json={"email": "b@example.com", "password": "secret456"})
            headers_b = {"Authorization": f"Bearer {login_b.json()['access_token']}"}
            uid_b = login_b.json()["user"]["id"]
            created_b = await c.post(
                "/records/", headers=headers_b, json=RECORD | {"images": ["https://x.test/b1.jpg"], "user_id": uid_b}
            )
            check(created_b.status_code == 200, "user B creates a record offline")

            result = await engine_factory().sync_all()
            check(result.synced_count == 0 and result.deferred_count == 1, "B stays queued until B links an account")
            check(any(e.get("user_id") == uid_b for e in result.errors), "the missing link is reported for B")
            history_a = (await c.get("/offline/sync/history", headers=headers)).json()
            check(
                all(i["local_record_id"] != created_b.json()["id"] for i in history_a["items"]),
                "A cannot see the sync history of B",
            )
            check(
                (await c.get("/offline/status", headers=headers_b)).json()["pending_sync"] == 1
                and (await c.get("/offline/status", headers=headers)).json()["pending_sync"] == 0,
                "pending counts are per user",
            )

            sync_engine.save_refresh_token(uid_b, "refresh-B")
            await c.post(
                "/records/", headers=headers, json=RECORD | {"images": ["https://x.test/a2.jpg"], "user_id": uid}
            )
            result = await engine_factory().sync_all(user_id=uid)
            check(result.synced_count == 1, "syncing as A only touches the items of A")
            check(await sync_engine.count_syncable(uid_b) == 1, "the item of B is untouched by a run for A")
            result = await engine_factory().sync_all()
            sent_for_b = [b for b in remote.bodies if b.get("images") == ["https://x.test/b1.jpg"]]
            check(
                result.synced_count == 1 and sent_for_b[0]["user_id"] == REMOTE_USER_B,
                "the record of B is created under the remote account of B",
            )

            # --- tokens are reused across runs ----------------------------------------------
            check(
                remote.refresh_calls == 2,
                f"one /users/refresh per user across many runs (got {remote.refresh_calls})",
            )

            # --- list endpoint reports sync status, duplicate mappings are rejected ---------
            listing = (await c.get("/records/", headers=headers)).json()
            check(listing and all("synced" in r for r in listing), "list reports sync status for every record")

            from datetime import datetime, timezone

            from sqlalchemy import select
            from sqlalchemy.exc import IntegrityError

            from database_local import AsyncSessionLocal
            from models_local import IdMapping

            async with AsyncSessionLocal() as db:
                existing = (await db.execute(select(IdMapping))).scalars().first()
                db.add(
                    IdMapping(
                        local_id=existing.local_id,
                        remote_id=1,
                        entity_type=existing.entity_type,
                        synced_at=datetime.now(timezone.utc),
                    )
                )
                try:
                    await db.commit()
                except IntegrityError:
                    check(True, "a record cannot have two id mappings")
                else:
                    check(False, "a record cannot have two id mappings")

            # --- websocket broadcast tolerates clients joining mid-send ---------------------
            class Quiet:
                async def send_text(self, _payload):
                    return None

            class LateJoiner:
                async def send_text(self, _payload):
                    sync_background.add_ws_client(Quiet())

            sync_background.add_ws_client(LateJoiner())
            try:
                await sync_background._notify_ws("ping", {})
            except RuntimeError:
                check(False, "broadcast survives a client joining while sending")
            else:
                check(True, "broadcast survives a client joining while sending")

            # --- deleting a record cleans up after it ---------------------------------------
            from datetime import datetime, timezone

            from sqlalchemy import func, select

            from database_local import AsyncSessionLocal
            from models import Analysis, AnalysisImage, Ibis, RecordMapZone
            from storage_local import _resolve_inside_storage

            upload = await c.post(
                "/records/upload",
                headers=headers,
                data=RECORD,
                files=[("images", (f"{i}.jpg", JPEG, "image/jpeg")) for i in range(2)],
            )
            doomed = upload.json()
            on_disk = [Path(os.environ["LOCAL_STORAGE_PATH"]) / u.removeprefix("/local-storage/") for u in doomed["images"]]
            check(all(f.is_file() for f in on_disk), "uploaded images exist on disk")

            now = datetime.now(timezone.utc)
            async with AsyncSessionLocal() as db:
                analysis = Analysis(ibis_quantity=1, datetime=now, recorder_id=doomed["id"])
                db.add(analysis)
                await db.flush()
                image = AnalysisImage(
                    analysis_id=analysis.id, record_id=doomed["id"], image_index=0,
                    image_url=doomed["images"][0], ibis_quantity=1, created_at=now,
                )
                db.add(image)
                await db.flush()
                db.add(Ibis(color="red", age_group="adult", analysis_id=analysis.id, analysis_image_id=image.id))
                db.add(RecordMapZone(record_id=doomed["id"], map_zone_id=999))
                await db.commit()

            check((await c.delete(f"/records/{doomed['id']}", headers=headers)).status_code == 200, "delete succeeds")
            check(not any(f.exists() for f in on_disk), "image files are removed from disk")
            check(
                (await c.get(doomed["images"][0])).status_code == 404,
                "removed images are no longer served by /local-storage",
            )
            async with AsyncSessionLocal() as db:
                leftovers = {
                    model.__name__: (await db.execute(select(func.count()).select_from(model))).scalar()
                    for model in (Analysis, AnalysisImage, Ibis, RecordMapZone)
                }
            check(not any(leftovers.values()), f"analysis, detections and zone links are removed {leftovers}")

            # records.images is free text from the client: it must not reach outside the storage dir
            outside = Path(TMP) / "outside.txt"
            outside.write_text("keep me")
            sneaky = await c.post(
                "/records/",
                headers=headers,
                json=RECORD | {"images": ["/local-storage/../outside.txt"], "user_id": uid},
            )
            await c.delete(f"/records/{sneaky.json()['id']}", headers=headers)
            check(outside.exists(), "a crafted image URL cannot delete files outside the storage directory")
            check(
                _resolve_inside_storage("/local-storage/../outside.txt") is None
                and _resolve_inside_storage("/local-storage/") is None
                and _resolve_inside_storage("/etc/passwd") is None,
                "storage paths outside the storage directory are rejected",
            )

            # --- one sync at a time ---------------------------------------------------------
            async with sync_engine.sync_lock:
                try:
                    await engine_factory().sync_all()
                except SyncInProgress:
                    check(True, "a second sync is refused while one is running")
                else:
                    check(False, "a second sync is refused while one is running")
                check(
                    (await c.get("/offline/status", headers=headers)).json()["syncing"],
                    "/offline/status reports the running sync",
                )


asyncio.run(scenarios())
print("ALL OFFLINE SCENARIOS PASSED")
