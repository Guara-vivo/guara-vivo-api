from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from models import Analysis, Record, User
from database import get_db
from schemas import AnalysisCreate, AnalysisRead, AnalysisUpdate
from security import get_current_user

router = APIRouter(prefix="/analysis", tags=["analysis"])

@router.get("/", response_model=list[AnalysisRead])
async def read_analyses(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Analysis).order_by(Analysis.id).offset(skip).limit(limit))
    analyses = result.scalars().all()
    
    return analyses

@router.get("/{analysis_id}", response_model=AnalysisRead)
async def read_analysis(analysis_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Analysis).where(Analysis.id == analysis_id))
    analysis = result.scalar_one_or_none()

    if analysis is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    
    return analysis

@router.post("/", response_model=AnalysisRead)
async def create_analysis(
    analysis: AnalysisCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Record).where(Record.id == analysis.recorder_id))
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    if record.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    db_analysis = Analysis(**analysis.model_dump())

    db.add(db_analysis)
    await db.commit()
    await db.refresh(db_analysis)

    return db_analysis

@router.put("/{analysis_id}", response_model=AnalysisRead)
async def update_analysis(
    analysis_id: int,
    updated_analysis: AnalysisUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Analysis).where(Analysis.id == analysis_id))
    analysis = result.scalar_one_or_none()

    if analysis is None:
        raise HTTPException(status_code=404, detail="Analysis not found")

    result = await db.execute(select(Record).where(Record.id == analysis.recorder_id))
    current_record = result.scalar_one_or_none()
    if current_record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    if current_record.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    result = await db.execute(select(Record).where(Record.id == updated_analysis.recorder_id))
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    if record.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    analysis.ibis_quantity = updated_analysis.ibis_quantity
    analysis.datetime = updated_analysis.datetime
    analysis.recorder_id = updated_analysis.recorder_id
    await db.commit()
    await db.refresh(analysis)

    return analysis

@router.delete("/{analysis_id}")
async def delete_analysis(
    analysis_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Analysis).where(Analysis.id == analysis_id))
    analysis = result.scalar_one_or_none()

    if analysis is None:
        raise HTTPException(status_code=404, detail="Analysis not found")

    result = await db.execute(select(Record).where(Record.id == analysis.recorder_id))
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    if record.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    
    await db.delete(analysis)
    await db.commit()

    return {"detail": "Analysis deleted successfully"}
