from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from models import Record
from schemas import RecordCreate, RecordRead, RecordUpdate

router = APIRouter(prefix="/records", tags=["records"])

@router.get("/", response_model=list[RecordRead])
async def read_records(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Record).offset(skip).limit(limit))
    return result.scalars().all()

@router.get("/{record_id}", response_model=RecordRead)
async def read_record(record_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Record).where(Record.id == record_id))
    record = result.scalar_one_or_none()

    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    
    return record

@router.post("/", response_model=RecordRead)
async def create_record(record: RecordCreate, db: AsyncSession = Depends(get_db)):
    db_record = Record(**record.model_dump())

    db.add(db_record)
    await db.commit()
    await db.refresh(db_record)

    return db_record

@router.put("/{record_id}", response_model=RecordRead)
async def update_record(record_id: int, updated_record: RecordUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Record).where(Record.id == record_id))
    record = result.scalar_one_or_none()

    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    
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
async def delete_record(record_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Record).where(Record.id == record_id))
    record = result.scalar_one_or_none()

    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    
    await db.delete(record)
    await db.commit()

    return {"detail": "Record deleted successfully"}
