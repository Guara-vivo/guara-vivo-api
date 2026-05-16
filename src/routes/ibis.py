from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from models import Ibis
from database import get_db
from schemas import IbisCreate, IbisRead, IbisUpdate

router = APIRouter(prefix="/ibis", tags=["ibis"])

@router.get("/", response_model=list[IbisRead])
def read_ibis_list(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(Ibis).offset(skip).limit(limit).all()

@router.get("/{ibis_id}", response_model=IbisRead)
def read_ibis(ibis_id: int, db: Session = Depends(get_db)):
    ibis = db.query(Ibis).filter(Ibis.id == ibis_id).first()

    if not ibis:
        raise HTTPException(status_code=404, detail="Ibis not found")
    
    return ibis

@router.post("/", response_model=IbisRead)
def create_ibis(ibis: IbisCreate, db: Session = Depends(get_db)):
    db_ibis = Ibis(**ibis.model_dump())

    db.add(db_ibis)
    db.commit()
    db.refresh(db_ibis)

    return db_ibis

@router.put("/{ibis_id}", response_model=IbisRead)
def update_ibis(ibis_id: int, updated_ibis: IbisUpdate, db: Session = Depends(get_db)):
    ibis = db.query(Ibis).filter(Ibis.id == ibis_id).first()

    if ibis is None:
        raise HTTPException(status_code=404, detail="Ibis not found")

    ibis.color = updated_ibis.color
    ibis.age_group = updated_ibis.age_group
    ibis.analysis_id = updated_ibis.analysis_id
    db.commit()
    db.refresh(ibis)

    return ibis

@router.delete("/{ibis_id}")
def delete_ibis(ibis_id: int, db: Session = Depends(get_db)):
    ibis = db.query(Ibis).filter(Ibis.id == ibis_id).first()

    if ibis is None:
        raise HTTPException(status_code=404, detail="Ibis not found")

    db.delete(ibis)
    db.commit()

    return {"detail": "Ibis deleted successfully"}
