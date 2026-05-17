from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from models import Record, User
from schemas import RecordCreate, RecordRead, RecordUpdate
from security import get_current_user

router = APIRouter(prefix="/records", tags=["records"])

@router.get("/", response_model=list[RecordRead])
async def read_records(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Record).order_by(Record.id).offset(skip).limit(limit))
    return result.scalars().all()

@router.get("/{record_id}", response_model=RecordRead)
async def read_record(record_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Record).where(Record.id == record_id))
    record = result.scalar_one_or_none()

    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    
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

    db.add(db_record)
    await db.commit()
    await db.refresh(db_record)

    return db_record

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
