from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Analysis, MapZone, Record, RecordMapZone, User
from schemas import MapZoneCreate, MapZoneRead, MapZoneRecordRead
from security import get_current_user
from services.map_zone_service import (
    find_smallest_free_sequence_index,
    format_zone_name,
    link_records_to_zone_statement,
    zones_overlap,
)

router = APIRouter(prefix="/map-zones", tags=["map-zones"])

NEST_ZONE_TYPE = "nest"


def serialize_map_zone_record(
    record: Record,
    analysis: Analysis | None,
    map_zones: list[MapZone],
    author: User,
) -> dict:
    from routes.record import serialize_record_summary

    payload = serialize_record_summary(record, analysis, map_zones)
    payload["author_name"] = author.name
    return payload


def is_visible_map_zone_record(record: Record, analysis: Analysis | None) -> bool:
    return record.status == "completed" and analysis is not None and analysis.ibis_quantity > 0


@router.get("/", response_model=list[MapZoneRead])
async def read_map_zones(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=1000, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get map zones. Nest positions are admin-only."""
    statement = select(MapZone)
    if not current_user.is_admin:
        statement = statement.where(MapZone.type != NEST_ZONE_TYPE)
    result = await db.execute(
        statement
        .order_by(MapZone.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()


@router.get("/{zone_id}/records", response_model=list[MapZoneRecordRead])
async def read_map_zone_records(
    zone_id: int,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get records linked to a map zone.

    On a nest zone, non-admins only see their own records, and get 404 when they have none.
    """
    zone_result = await db.execute(select(MapZone).where(MapZone.id == zone_id))
    zone = zone_result.scalar_one_or_none()

    if zone is None:
        raise HTTPException(status_code=404, detail="Zone not found")

    visible_records = (
        select(Record, Analysis, User)
        .join(RecordMapZone, RecordMapZone.record_id == Record.id)
        .join(User, User.id == Record.user_id)
        .join(Analysis, Analysis.recorder_id == Record.id)
        .where(
            RecordMapZone.map_zone_id == zone_id,
            Record.status == "completed",
            Analysis.ibis_quantity > 0,
        )
    )

    if zone.type == NEST_ZONE_TYPE and not current_user.is_admin:
        visible_records = visible_records.where(Record.user_id == current_user.id)
        # Checked apart from the page so that paging past the end is not mistaken for "none".
        has_own_record = await db.execute(select(visible_records.exists()))
        if not has_own_record.scalar():
            # Same answer as a missing zone: do not reveal that this nest exists.
            raise HTTPException(status_code=404, detail="Zone not found")

    records_result = await db.execute(
        visible_records
        .order_by(Record.date_time.desc(), Record.id.desc())
        .offset(skip)
        .limit(limit)
    )
    rows = records_result.all()

    from routes.record import load_record_map_zones

    zones_by_record = await load_record_map_zones(db, [record.id for record, _analysis, _author in rows])
    return [
        serialize_map_zone_record(record, analysis, zones_by_record.get(record.id, []), author)
        for record, analysis, author in rows
        if is_visible_map_zone_record(record, analysis)
    ]


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    # 404 rather than 403: do not advertise admin-only endpoints.
    if not current_user.is_admin:
        raise HTTPException(status_code=404, detail="Not found")
    return current_user


@router.post("/", response_model=MapZoneRead)
async def create_map_zone(
    zone: MapZoneCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Create a new map zone."""
    existing_result = await db.execute(select(MapZone).where(MapZone.type == zone.type))
    existing_zones = list(existing_result.scalars().all())

    for existing_zone in existing_zones:
        if zones_overlap(
            zone.latitude,
            zone.longitude,
            zone.radius_meters,
            existing_zone.latitude,
            existing_zone.longitude,
            existing_zone.radius_meters,
        ):
            raise HTTPException(
                status_code=422,
                detail="Area overlaps another area of the same type",
            )

    sequence_index = find_smallest_free_sequence_index(
        [item.sequence_index for item in existing_zones]
    )
    db_zone = MapZone(
        type=zone.type,
        name=format_zone_name(zone.type, sequence_index),
        sequence_index=sequence_index,
        latitude=zone.latitude,
        longitude=zone.longitude,
        radius_meters=zone.radius_meters,
        user_id=current_user.id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(db_zone)
    await db.flush()

    # Linking happens in the database; no record is loaded into the API.
    await db.execute(link_records_to_zone_statement(db_zone))

    await db.commit()
    await db.refresh(db_zone)

    from routes.record import invalidate_all_records_cache

    invalidate_all_records_cache()
    return db_zone


@router.delete("/{zone_id}")
async def delete_map_zone(
    zone_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Delete a map zone. Only creator can delete."""
    result = await db.execute(select(MapZone).where(MapZone.id == zone_id))
    zone = result.scalar_one_or_none()

    if zone is None:
        raise HTTPException(status_code=404, detail="Zone not found")

    if zone.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Zone not found")

    await db.delete(zone)
    await db.commit()
    from routes.record import invalidate_all_records_cache

    invalidate_all_records_cache()
    return {"detail": "Zone deleted successfully"}
