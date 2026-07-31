import json
import hashlib
import os
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db, OFFLINE_MODE
from middleware import MAX_REQUEST_BODY_BYTES
from models import Analysis, AnalysisImage, Ibis, MapZone, Record, RecordMapZone, User
from models.sync_queue import SyncQueueItem
from models.id_mapping import IdMapping
from rabbitmq import publish_record_for_inference
from schemas import AnalysisImageRead, AnalysisRead, IbisRead, LinkedMapZoneRead, RecordCreate, RecordDetailRead, RecordRead, RecordSummaryRead, RecordUpdate
from security import get_current_user
from services.map_zone_service import point_inside_zone
from supabase_storage import MAX_UPLOAD_FILE_BYTES, upload_public_image
from storage_local import save_image as save_image_local
from utils.file_validation import validate_image_file

router = APIRouter(prefix="/records", tags=["records"])
RECORDS_CACHE_TTL_SECONDS = int(os.getenv("RECORDS_CACHE_TTL_SECONDS", "30"))
RECORDS_CACHE_MAX_ENTRIES = int(os.getenv("RECORDS_CACHE_MAX_ENTRIES", "1000"))
MAX_TOTAL_UPLOAD_SIZE = MAX_REQUEST_BODY_BYTES
_records_cache: dict[str, tuple[float, str, Any]] = {}


def get_cache_key(*parts: object) -> str:
    return ":".join(str(part) for part in parts)


def build_etag(payload: Any) -> str:
    encoded = json.dumps(
        jsonable_encoder(payload),
        sort_keys=True,
        separators=(",", ":"),
    )
    return f'"{hashlib.sha256(encoded.encode("utf-8")).hexdigest()}"'


def get_cached_payload(request: Request, response: Response, cache_key: str) -> Response | Any | None:
    cached = _records_cache.get(cache_key)

    if cached is None:
        return None

    timestamp, etag, payload = cached
    if time.monotonic() - timestamp > RECORDS_CACHE_TTL_SECONDS:
        _records_cache.pop(cache_key, None)
        return None

    headers = {
        "Cache-Control": f"private, max-age={RECORDS_CACHE_TTL_SECONDS}",
        "ETag": etag,
    }
    for name, value in headers.items():
        response.headers[name] = value

    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)

    return payload


def set_cached_payload(response: Response, cache_key: str, payload: Any) -> Any:
    prune_records_cache()
    etag = build_etag(payload)
    _records_cache[cache_key] = (time.monotonic(), etag, payload)
    prune_records_cache()
    response.headers["Cache-Control"] = f"private, max-age={RECORDS_CACHE_TTL_SECONDS}"
    response.headers["ETag"] = etag
    return payload


def prune_records_cache() -> None:
    now = time.monotonic()
    for cache_key, (timestamp, _etag, _payload) in list(_records_cache.items()):
        if now - timestamp > RECORDS_CACHE_TTL_SECONDS:
            _records_cache.pop(cache_key, None)

    while len(_records_cache) > RECORDS_CACHE_MAX_ENTRIES:
        oldest_key = min(_records_cache, key=lambda key: _records_cache[key][0])
        _records_cache.pop(oldest_key, None)


def invalidate_records_cache(user_id: int) -> None:
    prefix = f"records:{user_id}:"
    for cache_key in list(_records_cache):
        if cache_key.startswith(prefix):
            _records_cache.pop(cache_key, None)


def invalidate_all_records_cache() -> None:
    _records_cache.clear()


def parse_behavior_form_values(values: list[str]) -> list[str]:
    if len(values) == 1:
        value = values[0].strip()
        if value.startswith("["):
            parsed = json.loads(value)
            if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
                raise ValueError("behavior JSON must be a list of strings")
            return parsed
        if "," in value:
            return [item.strip() for item in value.split(",") if item.strip()]
    return values


async def link_record_to_containing_zones(db: AsyncSession, db_record: Record) -> None:
    zones_result = await db.execute(select(MapZone).order_by(MapZone.type, MapZone.sequence_index))
    linked_types: set[str] = set()

    for zone in zones_result.scalars().all():
        if zone.type in linked_types:
            continue
        if not point_inside_zone(
            db_record.latitude_camera,
            db_record.longitude_camera,
            zone.latitude,
            zone.longitude,
            zone.radius_meters,
        ):
            continue

        db.add(RecordMapZone(record_id=db_record.id, map_zone_id=zone.id))
        linked_types.add(zone.type)


async def commit_record_and_publish(db: AsyncSession, db_record: Record) -> Record:
    db.add(db_record)
    await db.flush()

    if OFFLINE_MODE:
        from models_local import SyncQueueItem as SyncQueueItemLocal

        db_record.status = "pending"
        await db.commit()
        await db.refresh(db_record)

        sync_item = SyncQueueItemLocal(
            operation="create_record",
            local_record_id=db_record.id,
            payload=json.dumps({
                "latitude_camera": db_record.latitude_camera,
                "longitude_camera": db_record.longitude_camera,
                "behavior": db_record.behavior,
                "date_time": db_record.date_time.isoformat() if hasattr(db_record.date_time, 'isoformat') else str(db_record.date_time),
                "user_id": db_record.user_id,
            }),
            status="pending",
            created_at=datetime.now(timezone.utc),
        )
        db.add(sync_item)
        await db.commit()
        invalidate_records_cache(db_record.user_id)
        return db_record

    await link_record_to_containing_zones(db, db_record)
    await db.commit()
    await db.refresh(db_record)

    try:
        publish_record_for_inference(db_record.id)
    except Exception as exc:
        db_record.status = "failed"
        await db.commit()
        invalidate_records_cache(db_record.user_id)
        raise HTTPException(
            status_code=503,
            detail=f"Record created but could not be queued for inference: {exc}",
        ) from exc

    invalidate_records_cache(db_record.user_id)
    return db_record


def serialize_linked_map_zones(zones: list[MapZone]) -> list[dict[str, Any]]:
    return [
        LinkedMapZoneRead.model_validate(zone).model_dump(mode="json")
        for zone in zones
    ]


def inject_sync_info(payload: dict[str, Any], sync_info: tuple[bool, str | None, int | None] | None = None) -> dict[str, Any]:
    if OFFLINE_MODE:
        synced, sync_status, remote_id = sync_info or (False, "pending_sync", None)
        payload["synced"] = synced
        payload["sync_status"] = sync_status
        payload["remote_id"] = remote_id
    return payload


async def get_sync_info(db: AsyncSession, record_id: int) -> tuple[bool, str | None, int | None]:
    if not OFFLINE_MODE:
        return True, None, None
    from models_local import IdMapping as IdMappingLocal
    result = await db.execute(
        select(IdMappingLocal).where(
            IdMappingLocal.local_id == record_id,
            IdMappingLocal.entity_type == "record",
        )
    )
    mapping = result.scalar_one_or_none()
    if mapping:
        return True, "synced", mapping.remote_id
    return False, "pending_sync", None


def serialize_record_read(record: Record, map_zones: list[MapZone] | None = None) -> dict[str, Any]:
    payload = RecordRead.model_validate(record).model_dump(mode="json")
    payload["map_zones"] = serialize_linked_map_zones(map_zones or [])
    return payload


async def load_record_map_zones(
    db: AsyncSession,
    record_ids: list[int],
) -> dict[int, list[MapZone]]:
    if not record_ids:
        return {}

    result = await db.execute(
        select(RecordMapZone.record_id, MapZone)
        .join(MapZone, MapZone.id == RecordMapZone.map_zone_id)
        .where(RecordMapZone.record_id.in_(record_ids))
        .order_by(RecordMapZone.record_id, MapZone.type, MapZone.sequence_index)
    )
    zones_by_record: dict[int, list[MapZone]] = {record_id: [] for record_id in record_ids}

    for record_id, zone in result.all():
        zones_by_record.setdefault(record_id, []).append(zone)

    return zones_by_record


def serialize_record_summary(
    record: Record,
    analysis: Analysis | None,
    map_zones: list[MapZone] | None = None,
) -> dict[str, Any]:
    payload = serialize_record_read(record, map_zones)
    payload["analysis_id"] = analysis.id if analysis else None
    payload["ibis_quantity"] = analysis.ibis_quantity if analysis else None
    return payload


def serialize_record_detail(
    record: Record,
    analysis: Analysis | None,
    ibis_items: list[Ibis],
    image_analyses: list[AnalysisImage],
    map_zones: list[MapZone] | None = None,
) -> dict[str, Any]:
    payload = serialize_record_read(record, map_zones)
    payload["analysis"] = (
        AnalysisRead.model_validate(analysis).model_dump(mode="json")
        if analysis
        else None
    )
    payload["ibis"] = [
        IbisRead.model_validate(item).model_dump(mode="json") for item in ibis_items
    ]
    payload["image_analyses"] = [
        AnalysisImageRead.model_validate(item).model_dump(mode="json") for item in image_analyses
    ]
    return payload

@router.get("/", response_model=list[RecordRead])
async def read_records(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Record)
        .where(Record.user_id == current_user.id)
        .order_by(Record.id)
        .offset(skip)
        .limit(limit)
    )
    records = list(result.scalars().all())
    zones_by_record = await load_record_map_zones(db, [record.id for record in records])
    payloads = [
        serialize_record_read(record, zones_by_record.get(record.id, []))
        for record in records
    ]
    for record, payload in zip(records, payloads):
        if OFFLINE_MODE:
            sync_info = await get_sync_info(db, record.id)
            inject_sync_info(payload, sync_info)
    return payloads


@router.get("/summary", response_model=list[RecordSummaryRead])
async def read_record_summaries(
    request: Request,
    response: Response,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cache_key = get_cache_key("records", current_user.id, "summary", skip, limit)
    cached = get_cached_payload(request, response, cache_key)
    if cached is not None:
        return cached

    result = await db.execute(
        select(Record, Analysis)
        .outerjoin(Analysis, Analysis.recorder_id == Record.id)
        .where(Record.user_id == current_user.id)
        .order_by(Record.id)
        .offset(skip)
        .limit(limit)
    )
    rows = result.all()
    zones_by_record = await load_record_map_zones(db, [record.id for record, _analysis in rows])
    payload = [
        serialize_record_summary(record, analysis, zones_by_record.get(record.id, []))
        for record, analysis in rows
    ]
    if OFFLINE_MODE:
        for record, item in zip([r for r, _ in rows], payload):
            sync_info = await get_sync_info(db, record.id)
            inject_sync_info(item, sync_info)
    return set_cached_payload(response, cache_key, payload)


@router.get("/{record_id}/detail", response_model=RecordDetailRead)
async def read_record_detail(
    record_id: int,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cache_key = get_cache_key("records", current_user.id, "detail", record_id)
    cached = get_cached_payload(request, response, cache_key)
    if cached is not None:
        return cached

    result = await db.execute(
        select(Record, Analysis)
        .outerjoin(Analysis, Analysis.recorder_id == Record.id)
        .where(Record.id == record_id)
    )
    row = result.one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="Record not found")

    record, analysis = row
    if record.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Not found")

    ibis_items: list[Ibis] = []
    if analysis is not None:
        ibis_result = await db.execute(
            select(Ibis)
            .where(Ibis.analysis_id == analysis.id)
            .order_by(Ibis.id)
        )
        ibis_items = list(ibis_result.scalars().all())

    image_analyses_result = await db.execute(
        select(AnalysisImage)
        .where(AnalysisImage.record_id == record_id)
        .order_by(AnalysisImage.image_index)
    )
    image_analyses = list(image_analyses_result.scalars().all())

    zones_by_record = await load_record_map_zones(db, [record.id])
    payload = serialize_record_detail(
        record,
        analysis,
        ibis_items,
        image_analyses,
        zones_by_record.get(record.id, []),
    )
    if OFFLINE_MODE:
        sync_info = await get_sync_info(db, record.id)
        inject_sync_info(payload, sync_info)
    return set_cached_payload(response, cache_key, payload)

@router.get("/{record_id}", response_model=RecordRead)
async def read_record(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Record).where(Record.id == record_id))
    record = result.scalar_one_or_none()

    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")

    if record.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Not found")
    
    zones_by_record = await load_record_map_zones(db, [record.id])
    payload = serialize_record_read(record, zones_by_record.get(record.id, []))
    if OFFLINE_MODE:
        sync_info = await get_sync_info(db, record.id)
        inject_sync_info(payload, sync_info)
    return payload

@router.post("/", response_model=RecordRead)
async def create_record(
    record: RecordCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if record.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    record_data = record.model_dump()
    record_data["status"] = "pending"
    record_data["analysis_progress"] = 0
    db_record = Record(**record_data)
    created_record = await commit_record_and_publish(db, db_record)
    zones_by_record = await load_record_map_zones(db, [created_record.id])
    payload = serialize_record_read(created_record, zones_by_record.get(created_record.id, []))
    if OFFLINE_MODE:
        inject_sync_info(payload)
    return payload


@router.post("/upload", response_model=RecordRead)
async def create_record_with_upload(
    latitude_camera: float = Form(..., ge=-90, le=90),
    longitude_camera: float = Form(..., ge=-180, le=180),
    behavior: list[str] = Form(...),
    date_time: datetime = Form(...),
    images: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not 1 <= len(images) <= 20:
        raise HTTPException(status_code=422, detail="images must contain between 1 and 20 files")

    image_urls = []
    local_image_paths = []
    total_size = 0
    
    for image in images:
        content = await image.read()
        if not content:
            raise HTTPException(status_code=422, detail=f"{image.filename or 'file'} is empty")
        if len(content) > MAX_UPLOAD_FILE_BYTES:
            raise HTTPException(status_code=413, detail=f"{image.filename or 'file'} is too large")
        
        total_size += len(content)
        if total_size > MAX_TOTAL_UPLOAD_SIZE:
            raise HTTPException(status_code=413, detail="Total upload size exceeds limit (10MB)")
        
        validate_image_file(image.filename or "file", content)

        if OFFLINE_MODE:
            url = save_image_local(
                user_id=current_user.id,
                content=content,
                filename=image.filename,
            )
            image_urls.append(url)
            local_image_paths.append(url)
        else:
            try:
                url = await upload_public_image(
                    user_id=current_user.id,
                    content=content,
                    filename=image.filename,
                    content_type=image.content_type or "image/jpeg",
                )
                image_urls.append(url)
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"Could not upload image: {exc}") from exc
        
        del content

    try:
        record = RecordCreate(
            images=image_urls,
            latitude_camera=latitude_camera,
            longitude_camera=longitude_camera,
            behavior=parse_behavior_form_values(behavior),
            date_time=date_time,
            user_id=current_user.id,
        )
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    record_data = record.model_dump()
    record_data["status"] = "pending"
    record_data["analysis_progress"] = 0
    db_record = Record(**record_data)
    created_record = await commit_record_and_publish(db, db_record)

    if OFFLINE_MODE:
        from models_local import SyncQueueItem as SyncQueueItemLocal
        from models_local import RecordLocal
        sync_item = SyncQueueItemLocal(
            operation="create_record_with_images",
            local_record_id=created_record.id,
            payload=json.dumps(record_data),
            image_local_paths=json.dumps(local_image_paths),
            status="pending",
            created_at=datetime.now(timezone.utc),
        )
        db.add(sync_item)
        await db.commit()

    zones_by_record = await load_record_map_zones(db, [created_record.id])
    payload = serialize_record_read(created_record, zones_by_record.get(created_record.id, []))
    if OFFLINE_MODE:
        inject_sync_info(payload)
    return payload

@router.put("/{record_id}", response_model=RecordRead)
async def update_record(
    record_id: int,
    updated_record: RecordUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Record).where(Record.id == record_id))
    record = result.scalar_one_or_none()

    if record is None or record.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Not found")

    if updated_record.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    
    record.images = updated_record.images
    record.latitude_camera = updated_record.latitude_camera
    record.longitude_camera = updated_record.longitude_camera
    record.behavior = updated_record.behavior
    record.date_time = updated_record.date_time
    record.user_id = updated_record.user_id
    record.status = updated_record.status
    await db.commit()
    await db.refresh(record)
    invalidate_records_cache(current_user.id)

    if OFFLINE_MODE:
        from models_local import SyncQueueItem as SyncQueueItemLocal
        sync_item = SyncQueueItemLocal(
            operation="update_record",
            local_record_id=record.id,
            payload=json.dumps(updated_record.model_dump()),
            status="pending",
            created_at=datetime.now(timezone.utc),
        )
        db.add(sync_item)
        await db.commit()

    payload = serialize_record_read(record)
    if OFFLINE_MODE:
        inject_sync_info(payload)
    return payload

@router.delete("/{record_id}")
async def delete_record(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Record).where(Record.id == record_id))
    record = result.scalar_one_or_none()

    if record is None or record.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Not found")

    await db.delete(record)
    await db.commit()
    invalidate_records_cache(current_user.id)

    return {"detail": "Record deleted successfully"}
