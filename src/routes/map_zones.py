from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import MapZone, User
from schemas import MapZoneCreate, MapZoneRead
from security import get_current_user

router = APIRouter(prefix="/map-zones", tags=["map-zones"])


@router.get("/", response_model=list[MapZoneRead])
async def read_map_zones(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=1000, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get all public map zones."""
    result = await db.execute(
        select(MapZone)
        .order_by(MapZone.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()


@router.post("/", response_model=MapZoneRead)
async def create_map_zone(
    zone: MapZoneCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new map zone. User ID must match authenticated user."""
    if zone.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    db_zone = MapZone(
        type=zone.type,
        latitude=zone.latitude,
        longitude=zone.longitude,
        radius_meters=zone.radius_meters,
        user_id=zone.user_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(db_zone)
    await db.commit()
    await db.refresh(db_zone)
    return db_zone


@router.delete("/{zone_id}")
async def delete_map_zone(
    zone_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a map zone. Only creator can delete."""
    result = await db.execute(select(MapZone).where(MapZone.id == zone_id))
    zone = result.scalar_one_or_none()

    if zone is None:
        raise HTTPException(status_code=404, detail="Zone not found")

    if zone.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    await db.delete(zone)
    await db.commit()
    return {"detail": "Zone deleted successfully"}
