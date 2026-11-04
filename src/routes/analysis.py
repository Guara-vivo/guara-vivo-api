from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from models import Analysis, Record, User
from database import get_db
from schemas import AnalysisRead
from security import get_current_user

# Read-only: analyses are written exclusively by the worker.
router = APIRouter(prefix="/analysis", tags=["analysis"])

@router.get("/", response_model=list[AnalysisRead])
async def read_analyses(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Analysis)
        .join(Record, Analysis.recorder_id == Record.id)
        .where(Record.user_id == current_user.id)
        .order_by(Analysis.id)
        .offset(skip)
        .limit(limit)
    )
    analyses = result.scalars().all()
    
    return analyses

@router.get("/{analysis_id}", response_model=AnalysisRead)
async def read_analysis(
    analysis_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Analysis).where(Analysis.id == analysis_id))
    analysis = result.scalar_one_or_none()

    if analysis is None:
        raise HTTPException(status_code=404, detail="Not found")

    result = await db.execute(select(Record).where(Record.id == analysis.recorder_id))
    record = result.scalar_one_or_none()
    if record is None or record.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Not found")
    
    return analysis
