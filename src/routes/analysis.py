from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from models import Analysis
from database import get_db

router = APIRouter(prefix="/analysis", tags=["analysis"])

@router.get("/", response_model=list[Analysis])
def read_analyses(db: Session = Depends(get_db)):
    analyses = db.query(Analysis).all()
    
    return analyses

@router.get("/{analysis_id}", response_model=Analysis)
def read_analysis(analysis_id: int, db: Session = Depends(get_db)):
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()

    if analysis is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    
    return analysis

@router.post("/", response_model=Analysis)
def create_analysis(analysis: Analysis, db: Session = Depends(get_db)):
    db.add(analysis)
    db.commit()
    db.refresh(analysis)

    return analysis

@router.delete("/{analysis_id}")
def delete_analysis(analysis_id: int, db: Session = Depends(get_db)):
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()

    if analysis is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    
    db.delete(analysis)
    db.commit()

    return {"detail": "Analysis deleted successfully"}