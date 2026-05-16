from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Record
from schemas import RecordCreate, RecordRead, RecordUpdate

router = APIRouter(prefix="/records", tags=["records"])

@router.get("/", response_model=list[RecordRead])
def read_records(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(Record).offset(skip).limit(limit).all()

@router.get("/{record_id}", response_model=RecordRead)
def read_record(record_id: int, db: Session = Depends(get_db)):
    record = db.query(Record).filter(Record.id == record_id).first()

    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    
    return record

@router.post("/", response_model=RecordRead)
def create_record(record: RecordCreate, db: Session = Depends(get_db)):
    db_record = Record(**record.model_dump())

    db.add(db_record)
    db.commit()
    db.refresh(db_record)

    return db_record

@router.put("/{record_id}", response_model=RecordRead)
def update_record(record_id: int, updated_record: RecordUpdate, db: Session = Depends(get_db)):
    record = db.query(Record).filter(Record.id == record_id).first()

    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    
    record.images = updated_record.images
    record.latitude_camera = updated_record.latitude_camera
    record.longitude_camera = updated_record.longitude_camera
    record.behavior = updated_record.behavior
    record.date_time = updated_record.date_time
    record.user_id = updated_record.user_id
    record.status = updated_record.status
    db.commit()
    db.refresh(record)

    return record

@router.delete("/{record_id}")
def delete_record(record_id: int, db: Session = Depends(get_db)):
    record = db.query(Record).filter(Record.id == record_id).first()

    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    
    db.delete(record)
    db.commit()

    return {"detail": "Record deleted successfully"}
