from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from models import Ibis
from database import get_db

router = APIRouter(prefix="/ibis", tags=["ibis"])

@router.get("/", response_model=list[Ibis])
def read_ibis_list(db: Session = Depends(get_db)):
    return db.query(Ibis).all()

@router.get("/{ibis_id}", response_model=Ibis)
def read_ibis(ibis_id: int, db: Session = Depends(get_db)):
    ibis = db.query(Ibis).filter(Ibis.id == ibis_id).first()

    if not ibis:
        raise HTTPException(status_code=404, detail="Ibis not found")
    
    return ibis

@router.post("/", response_model=Ibis)
def create_ibis(ibis: Ibis, db: Session = Depends(get_db)):
    db.add(ibis)
    db.commit()
    db.refresh(ibis)

    return ibis