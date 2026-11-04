from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from models import Analysis, Ibis, Record, User
from database import get_db
from schemas import IbisRead
from security import get_current_user

# Read-only: detections are written exclusively by the worker.
router = APIRouter(prefix="/ibis", tags=["ibis"])

@router.get("/", response_model=list[IbisRead])
async def read_ibis_list(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Ibis)
        .join(Analysis, Ibis.analysis_id == Analysis.id)
        .join(Record, Analysis.recorder_id == Record.id)
        .where(Record.user_id == current_user.id)
        .order_by(Ibis.id)
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()

@router.get("/{ibis_id}", response_model=IbisRead)
async def read_ibis(
    ibis_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Ibis).where(Ibis.id == ibis_id))
    ibis = result.scalar_one_or_none()

    if not ibis:
        raise HTTPException(status_code=404, detail="Ibis not found")

    await ensure_analysis_owner(db, ibis.analysis_id, current_user.id)
    
    return ibis

async def ensure_analysis_owner(db: AsyncSession, analysis_id: int, user_id: int) -> None:
    result = await db.execute(
        select(Analysis)
        .join(Record, Analysis.recorder_id == Record.id)
        .where(Analysis.id == analysis_id, Record.user_id == user_id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Not found")
