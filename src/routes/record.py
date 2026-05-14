from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from src.database import get_db
from src.models import Record

router = APIRouter(prefix="/records", tags=["records"])

@router.get("/", response_model=list[Record])
def read_records(db: Session = Depends(get_db)):
    return db.query(Record).all()

@router.get("/{record_id}", response_model=Record)
def read_record(record_id: int, db: Session = Depends(get_db)):
    record = db.query(Record).filter(Record.id == record_id).first()

    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    
    return record

@router.post("/", response_model=Record)
def create_record(record: Record, db: Session = Depends(get_db)):
    db.add(record)
    db.commit()
    db.refresh(record)

    return record

@router.put("/{record_id}", response_model=Record)
def update_record(record_id: int, updated_record: Record, db: Session = Depends(get_db)):
    record = db.query(Record).filter(Record.id == record_id).first()

    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    
    record.name = updated_record.name
    record.value = updated_record.value
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