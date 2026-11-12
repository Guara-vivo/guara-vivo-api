import asyncio
import json
import hashlib
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from middleware import MAX_REQUEST_BODY_BYTES
from models import Analysis, AnalysisImage, Ibis, MapZone, Record, RecordMapZone, User
from rabbitmq import publish_record_for_inference
from schemas import AnalysisImageRead, AnalysisRead, IbisRead, LinkedMapZoneRead, RecordCreate, normalize_client_uuid, RecordDetailRead, RecordRead, RecordSummaryRead, RecordUpdate
from security import get_current_user
from services.map_zone_service import point_inside_zone
from supabase_storage import MAX_UPLOAD_FILE_BYTES, delete_uploaded_images, is_own_storage_url, upload_public_image
from utils.file_validation import validate_image_file

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/records", tags=["records"])
RECORDS_CACHE_TTL_SECONDS = int(os.getenv("RECORDS_CACHE_TTL_SECONDS", "30"))
RECORDS_CACHE_MAX_ENTRIES = int(os.getenv("RECORDS_CACHE_MAX_ENTRIES", "1000"))
MAX_TOTAL_UPLOAD_SIZE = MAX_REQUEST_BODY_BYTES
_records_cache: dict[str, tuple[float, str, Any]] = {}
# Bumped on every invalidation. A request reads its token BEFORE querying the database and only
# stores its result if the token is unchanged, so a read that raced with a worker commit (and
# saw the old data) cannot repopulate the cache after the invalidation.
_user_cache_versions: dict[int, int] = {}
_global_cache_version = 0


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
        "Cache-Control": "private, no-cache",
        "ETag": etag,
    }
    for name, value in headers.items():
        response.headers[name] = value

    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)

    return payload


def get_cache_token(user_id: int) -> tuple[int, int]:
    return (_global_cache_version, _user_cache_versions.get(user_id, 0))


def set_cached_payload(
    response: Response,
    cache_key: str,
    payload: Any,
    user_id: int | None = None,
    token: tuple[int, int] | None = None,
) -> Any:
    prune_records_cache()
    etag = build_etag(payload)
    if token is None or user_id is None or token == get_cache_token(user_id):
        _records_cache[cache_key] = (time.monotonic(), etag, payload)
        prune_records_cache()
    response.headers["Cache-Control"] = "private, no-cache"
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
    _user_cache_versions[user_id] = _user_cache_versions.get(user_id, 0) + 1
    prefix = f"records:{user_id}:"
    for cache_key in list(_records_cache):
        if cache_key.startswith(prefix):
            _records_cache.pop(cache_key, None)


def invalidate_all_records_cache() -> None:
    global _global_cache_version
    _global_cache_version += 1
    _records_cache.clear()


def ensure_images_belong_to_user(images: list[str], user_id: int) -> None:
    """Images must be files this API stored for the user; never arbitrary URLs."""
    if not all(is_own_storage_url(image, user_id) for image in images):
        raise HTTPException(
            status_code=422,
            detail="images must be uploaded through /records/upload",
        )


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
    await link_record_to_containing_zones(db, db_record)
    await db.commit()
    await db.refresh(db_record)
    await publish_or_mark_failed(db, db_record)
    return db_record


async def publish_or_mark_failed(db: AsyncSession, db_record: Record) -> None:
    try:
        # pika is blocking; keep it off the event loop.
        await asyncio.to_thread(publish_record_for_inference, db_record.id)
    except Exception as exc:
        logger.exception("could not queue record %s for inference", db_record.id)
        db_record.status = "failed"
        await db.commit()
        invalidate_records_cache(db_record.user_id)
        raise HTTPException(
            status_code=503,
            detail="Record created but could not be queued for inference. Retry with the same client_uuid.",
        ) from exc

    db_record.queued_at = datetime.now(timezone.utc)
    await db.commit()
    invalidate_records_cache(db_record.user_id)


async def resume_existing_record(db: AsyncSession, record: Record) -> Record:
    """Answer a repeated create with the record that already exists.

    A record left `failed` (the first attempt could not be queued, or inference gave up)
    would otherwise be reported as sent and never analyzed, so the retry queues it again.
    """
    if record.status == "failed":
        record.status = "pending"
        record.analysis_progress = 0
        await db.commit()
        await db.refresh(record)
        await publish_or_mark_failed(db, record)
    return record


async def find_record_by_client_uuid(
    db: AsyncSession, user_id: int, client_uuid: str | None
) -> Record | None:
    if not client_uuid:
        return None
    result = await db.execute(
        select(Record).where(Record.user_id == user_id, Record.client_uuid == client_uuid)
    )
    return result.scalar_one_or_none()


async def create_record_idempotently(db: AsyncSession, db_record: Record) -> tuple[Record, bool]:
    """Create the record, or return the one already created with the same client_uuid.

    Returns `(record, created)`; `created` is False when an existing record was returned.

    A client that lost the response to a create retries with the same client_uuid; that
    must not duplicate the record or queue the inference twice.
    """
    existing = await find_record_by_client_uuid(db, db_record.user_id, db_record.client_uuid)
    if existing is not None:
        return await resume_existing_record(db, existing), False

    try:
        return await commit_record_and_publish(db, db_record), True
    except IntegrityError:
        # Two requests with the same client_uuid raced past the check above.
        await db.rollback()
        existing = await find_record_by_client_uuid(db, db_record.user_id, db_record.client_uuid)
        if existing is None:
            raise
        return await resume_existing_record(db, existing), False


def serialize_linked_map_zones(zones: list[MapZone]) -> list[dict[str, Any]]:
    return [
        LinkedMapZoneRead.model_validate(zone).model_dump(mode="json")
        for zone in zones
    ]


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
    return [
        serialize_record_read(record, zones_by_record.get(record.id, []))
        for record in records
    ]


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

    token = get_cache_token(current_user.id)
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
    return set_cached_payload(response, cache_key, payload, current_user.id, token)


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

    token = get_cache_token(current_user.id)
    result = await db.execute(
        select(Record, Analysis, User.name)
        .outerjoin(Analysis, Analysis.recorder_id == Record.id)
        .join(User, User.id == Record.user_id)
        .where(Record.id == record_id)
    )
    row = result.one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="Record not found")

    record, analysis, author_name = row
    is_foreign = record.user_id != current_user.id
    if is_foreign and not current_user.is_admin:
        raise HTTPException(status_code=404, detail="Not found")

    ibis_items: list[Ibis] = []
    if analysis is not None:
        ibis_result = await db.execute(
            select(Ibis)
            .where(Ibis.analysis_id == analysis.id)
            .order_by(Ibis.id)
        )
        ibis_items = list(ibis_result.scalars().all())

    # Fetch per-image analyses
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
    payload["author_name"] = author_name

    if is_foreign:
        # An admin reading someone else's record is not cached: the worker invalidates only the
        # owner's entries, so a copy under the admin's key would go stale for up to the TTL.
        response.headers["Cache-Control"] = "private, no-store"
        return payload

    return set_cached_payload(response, cache_key, payload, current_user.id, token)

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
    return serialize_record_read(record, zones_by_record.get(record.id, []))

@router.post("/", response_model=RecordRead, status_code=201)
async def create_record(
    record: RecordCreate,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Ensure user_id matches authenticated user
    if record.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    ensure_images_belong_to_user(record.images, current_user.id)

    record_data = record.model_dump()
    record_data["status"] = "pending"
    record_data["analysis_progress"] = 0
    db_record = Record(**record_data)
    created_record, created = await create_record_idempotently(db, db_record)
    response.status_code = 201 if created else 200
    zones_by_record = await load_record_map_zones(db, [created_record.id])
    return serialize_record_read(created_record, zones_by_record.get(created_record.id, []))


async def _store_images_and_create_record(
    *,
    db: AsyncSession,
    images: list[UploadFile],
    public_urls: list[str],
    current_user: User,
    latitude_camera: float,
    longitude_camera: float,
    behavior: list[str],
    date_time: datetime,
    client_uuid: str | None,
    response: Response,
) -> dict[str, Any]:
    """Validate and store the images, then create the record.

    `public_urls` is filled as files are stored so the caller can delete them on any failure.
    """
    total_size = 0

    for image in images:
        content = await image.read()
        if not content:
            raise HTTPException(status_code=422, detail=f"{image.filename or 'file'} is empty")
        if len(content) > MAX_UPLOAD_FILE_BYTES:
            raise HTTPException(status_code=413, detail=f"{image.filename or 'file'} is too large")

        total_size += len(content)
        if total_size > MAX_TOTAL_UPLOAD_SIZE:
            raise HTTPException(status_code=413, detail="Total upload size exceeds limit")

        # Magic-byte validation; the detected type (not the client's header) is what gets stored.
        detected_mime = validate_image_file(image.filename or "file", content)

        try:
            public_urls.append(
                await upload_public_image(
                    user_id=current_user.id,
                    content=content,
                    content_type=detected_mime,
                )
            )
        except Exception as exc:
            logger.exception("image upload to storage failed")
            raise HTTPException(status_code=502, detail="Could not store the uploaded image") from exc

        del content

    try:
        record = RecordCreate(
            images=public_urls,
            latitude_camera=latitude_camera,
            longitude_camera=longitude_camera,
            behavior=parse_behavior_form_values(behavior),
            date_time=date_time,
            user_id=current_user.id,
            client_uuid=client_uuid,
        )
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    record_data = record.model_dump()
    record_data["status"] = "pending"
    record_data["analysis_progress"] = 0
    db_record = Record(**record_data)
    created_record, created = await create_record_idempotently(db, db_record)
    if not created:
        # A concurrent request with the same client_uuid won; our files are unused.
        await delete_uploaded_images(public_urls)
        public_urls.clear()
    response.status_code = 201 if created else 200
    zones_by_record = await load_record_map_zones(db, [created_record.id])
    return serialize_record_read(created_record, zones_by_record.get(created_record.id, []))


@router.post("/upload", response_model=RecordRead, status_code=201)
async def create_record_with_upload(
    response: Response,
    latitude_camera: float = Form(..., ge=-90, le=90),
    longitude_camera: float = Form(..., ge=-180, le=180),
    behavior: list[str] = Form(...),
    date_time: datetime = Form(...),
    images: list[UploadFile] = File(...),
    client_uuid: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not 1 <= len(images) <= 20:
        raise HTTPException(status_code=422, detail="images must contain between 1 and 20 files")

    try:
        client_uuid = normalize_client_uuid(client_uuid)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # A retry of an upload that already succeeded must not store the images again.
    existing = await find_record_by_client_uuid(db, current_user.id, client_uuid)
    if existing is not None:
        existing = await resume_existing_record(db, existing)
        response.status_code = 200
        zones_by_record = await load_record_map_zones(db, [existing.id])
        return serialize_record_read(existing, zones_by_record.get(existing.id, []))

    public_urls: list[str] = []
    try:
        created_record = await _store_images_and_create_record(
            db=db,
            images=images,
            public_urls=public_urls,
            current_user=current_user,
            latitude_camera=latitude_camera,
            longitude_camera=longitude_camera,
            behavior=behavior,
            date_time=date_time,
            client_uuid=client_uuid,
            response=response,
        )
    except BaseException:
        # Nothing references these files if the record was not created.
        await delete_uploaded_images(public_urls)
        raise
    return created_record

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
    
    ensure_images_belong_to_user(updated_record.images, current_user.id)

    record.images = updated_record.images
    record.latitude_camera = updated_record.latitude_camera
    record.longitude_camera = updated_record.longitude_camera
    record.behavior = updated_record.behavior
    record.date_time = updated_record.date_time
    record.user_id = updated_record.user_id
    # `status` is owned by the worker; a client-sent value is ignored.
    await db.commit()
    await db.refresh(record)
    invalidate_records_cache(current_user.id)

    return record

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
