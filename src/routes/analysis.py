from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from models import Analysis
from database import get_db
from schemas import AnalysisCreate, AnalysisRead, AnalysisUpdate

router = APIRouter(prefix="/analysis", tags=["analysis"])

@router.get("/", response_model=list[AnalysisRead])
def read_analyses(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    analyses = db.query(Analysis).offset(skip).limit(limit).all()
    
    return analyses

@router.get("/{analysis_id}", response_model=AnalysisRead)
def read_analysis(analysis_id: int, db: Session = Depends(get_db)):
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()

    if analysis is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    
    return analysis

@router.post("/", response_model=AnalysisRead)
def create_analysis(analysis: AnalysisCreate, db: Session = Depends(get_db)):
    db_analysis = Analysis(**analysis.model_dump())

    db.add(db_analysis)
    db.commit()
    db.refresh(db_analysis)

    return db_analysis

@router.put("/{analysis_id}", response_model=AnalysisRead)
def update_analysis(analysis_id: int, updated_analysis: AnalysisUpdate, db: Session = Depends(get_db)):
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()

    if analysis is None:
        raise HTTPException(status_code=404, detail="Analysis not found")

    analysis.ibis_quantity = updated_analysis.ibis_quantity
    analysis.flock_size = updated_analysis.flock_size
    analysis.latitude = updated_analysis.latitude
    analysis.longitude = updated_analysis.longitude
    analysis.datetime = updated_analysis.datetime
    analysis.recorder_id = updated_analysis.recorder_id
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
