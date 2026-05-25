import json
import hashlib
import os
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from models import Analysis, AnalysisImage, Ibis, Record, User
from rabbitmq import publish_record_for_inference
from schemas import AnalysisImageRead, AnalysisRead, IbisRead, RecordCreate, RecordDetailRead, RecordRead, RecordSummaryRead, RecordUpdate
from security import get_current_user
from supabase_storage import MAX_UPLOAD_FILE_BYTES, upload_public_image
from utils.file_validation import validate_image_file

router = APIRouter(prefix="/records", tags=["records"])
RECORDS_CACHE_TTL_SECONDS = int(os.getenv("RECORDS_CACHE_TTL_SECONDS", "30"))
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
    etag = build_etag(payload)
    _records_cache[cache_key] = (time.monotonic(), etag, payload)
    response.headers["Cache-Control"] = f"private, max-age={RECORDS_CACHE_TTL_SECONDS}"
    response.headers["ETag"] = etag
    return payload


def invalidate_records_cache(user_id: int) -> None:
    prefix = f"records:{user_id}:"
    for cache_key in list(_records_cache):
        if cache_key.startswith(prefix):
            _records_cache.pop(cache_key, None)


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


async def commit_record_and_publish(db: AsyncSession, db_record: Record) -> Record:
    db.add(db_record)
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


def serialize_record_summary(record: Record, analysis: Analysis | None) -> dict[str, Any]:
    payload = RecordRead.model_validate(record).model_dump(mode="json")
    payload["analysis_id"] = analysis.id if analysis else None
    payload["ibis_quantity"] = analysis.ibis_quantity if analysis else None
    return payload


def serialize_record_detail(
    record: Record,
    analysis: Analysis | None,
    ibis_items: list[Ibis],
    image_analyses: list[AnalysisImage],
) -> dict[str, Any]:
    payload = RecordRead.model_validate(record).model_dump(mode="json")
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
    return result.scalars().all()


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
    payload = [serialize_record_summary(record, analysis) for record, analysis in result.all()]
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

    # Fetch per-image analyses
    image_analyses_result = await db.execute(
        select(AnalysisImage)
        .where(AnalysisImage.record_id == record_id)
        .order_by(AnalysisImage.image_index)
    )
    image_analyses = list(image_analyses_result.scalars().all())

    payload = serialize_record_detail(record, analysis, ibis_items, image_analyses)
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
        raise HTTPException(status_code=403, detail="Not enough permissions")
    
    return record

@router.post("/", response_model=RecordRead)
async def create_record(
    record: RecordCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Ensure user_id matches authenticated user
    if record.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    db_record = Record(**record.model_dump())
    return await commit_record_and_publish(db, db_record)


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

    MAX_TOTAL_UPLOAD_SIZE = 100 * 1024 * 1024  # 100MB aggregate limit
    public_urls = []
    total_size = 0
    
    for image in images:
        content = await image.read()
        if not content:
            raise HTTPException(status_code=422, detail=f"{image.filename or 'file'} is empty")
        if len(content) > MAX_UPLOAD_FILE_BYTES:
            raise HTTPException(status_code=413, detail=f"{image.filename or 'file'} is too large")
        
        # Check aggregate size limit
        total_size += len(content)
        if total_size > MAX_TOTAL_UPLOAD_SIZE:
            raise HTTPException(status_code=413, detail="Total upload size exceeds limit (100MB)")
        
        # Validate file by magic bytes
        validate_image_file(image.filename or "file", content)

        try:
            public_urls.append(
                await upload_public_image(
                    user_id=current_user.id,
                    content=content,
                    filename=image.filename,
                    content_type=image.content_type or "image/jpeg",
                )
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Could not upload image to Supabase: {exc}") from exc
        
        # Explicitly release memory after each file
        del content

    try:
        record = RecordCreate(
            images=public_urls,
            latitude_camera=latitude_camera,
            longitude_camera=longitude_camera,
            behavior=parse_behavior_form_values(behavior),
            date_time=date_time,
            user_id=current_user.id,
            status="pending",
        )
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    db_record = Record(**record.model_dump())
    return await commit_record_and_publish(db, db_record)

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
