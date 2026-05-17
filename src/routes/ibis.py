from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from models import Analysis, Ibis, Record, User
from database import get_db
from schemas import IbisCreate, IbisRead, IbisUpdate
from security import get_current_user

router = APIRouter(prefix="/ibis", tags=["ibis"])

@router.get("/", response_model=list[IbisRead])
async def read_ibis_list(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Ibis).order_by(Ibis.id).offset(skip).limit(limit))
    return result.scalars().all()

@router.get("/{ibis_id}", response_model=IbisRead)
async def read_ibis(ibis_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Ibis).where(Ibis.id == ibis_id))
    ibis = result.scalar_one_or_none()

    if not ibis:
        raise HTTPException(status_code=404, detail="Ibis not found")
    
    return ibis

@router.post("/", response_model=IbisRead)
async def create_ibis(
    ibis: IbisCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await ensure_analysis_owner(db, ibis.analysis_id, current_user.id)
    db_ibis = Ibis(**ibis.model_dump())

    db.add(db_ibis)
    await db.commit()
    await db.refresh(db_ibis)

    return db_ibis

@router.put("/{ibis_id}", response_model=IbisRead)
async def update_ibis(
    ibis_id: int,
    updated_ibis: IbisUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Ibis).where(Ibis.id == ibis_id))
    ibis = result.scalar_one_or_none()

    if ibis is None:
        raise HTTPException(status_code=404, detail="Ibis not found")

    await ensure_analysis_owner(db, ibis.analysis_id, current_user.id)
    await ensure_analysis_owner(db, updated_ibis.analysis_id, current_user.id)

    ibis.color = updated_ibis.color
    ibis.age_group = updated_ibis.age_group
    ibis.analysis_id = updated_ibis.analysis_id
    await db.commit()
    await db.refresh(ibis)

    return ibis

@router.delete("/{ibis_id}")
async def delete_ibis(
    ibis_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Ibis).where(Ibis.id == ibis_id))
    ibis = result.scalar_one_or_none()

    if ibis is None:
        raise HTTPException(status_code=404, detail="Ibis not found")

    await ensure_analysis_owner(db, ibis.analysis_id, current_user.id)

    await db.delete(ibis)
    await db.commit()

    return {"detail": "Ibis deleted successfully"}


async def ensure_analysis_owner(db: AsyncSession, analysis_id: int, user_id: int) -> None:
    result = await db.execute(
        select(Analysis)
        .join(Record, Analysis.recorder_id == Record.id)
        .where(Analysis.id == analysis_id, Record.user_id == user_id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=403, detail="Not enough permissions")
