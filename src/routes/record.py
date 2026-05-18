import json
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from models import Record, User
from rabbitmq import publish_record_for_inference
from schemas import RecordCreate, RecordRead, RecordUpdate
from security import get_current_user
from supabase_storage import MAX_UPLOAD_FILE_BYTES, upload_public_image

router = APIRouter(prefix="/records", tags=["records"])


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
        raise HTTPException(
            status_code=503,
            detail="Record created but could not be queued for inference",
        ) from exc

    return db_record

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

    public_urls = []
    for image in images:
        content_type = image.content_type or ""
        if not content_type.lower().startswith("image/"):
            raise HTTPException(status_code=422, detail=f"{image.filename or 'file'} is not an image")

        content = await image.read()
        if not content:
            raise HTTPException(status_code=422, detail=f"{image.filename or 'file'} is empty")
        if len(content) > MAX_UPLOAD_FILE_BYTES:
            raise HTTPException(status_code=413, detail=f"{image.filename or 'file'} is too large")

        try:
            public_urls.append(
                await upload_public_image(
                    user_id=current_user.id,
                    content=content,
                    filename=image.filename,
                    content_type=content_type,
                )
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Could not upload image to Supabase: {exc}") from exc

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

    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")

    if record.user_id != current_user.id or updated_record.user_id != current_user.id:
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

    return record

@router.delete("/{record_id}")
async def delete_record(
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
    
    await db.delete(record)
    await db.commit()

    return {"detail": "Record deleted successfully"}
