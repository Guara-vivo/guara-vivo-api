from math import asin, cos, radians, sin, sqrt

from sqlalchemy import and_, func, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from models import MapZone, Record, RecordMapZone


ZONE_TYPE_LABELS = {
    "feeding": "Alimentação",
    "nest": "Ninho",
}

EARTH_RADIUS_METERS = 6_371_000


def index_to_zone_suffix(index: int) -> str:
    if index < 0:
        raise ValueError("index must be non-negative")

    value = index + 1
    suffix = ""

    while value > 0:
        value -= 1
        suffix = chr(ord("A") + (value % 26)) + suffix
        value //= 26

    return suffix


def format_zone_name(zone_type: str, sequence_index: int) -> str:
    return f"{ZONE_TYPE_LABELS[zone_type]} {index_to_zone_suffix(sequence_index)}"


def find_smallest_free_sequence_index(used_indexes: list[int]) -> int:
    used = set(used_indexes)
    index = 0

    while index in used:
        index += 1

    return index


def distance_meters(lat_a: float, lng_a: float, lat_b: float, lng_b: float) -> float:
    lat_a_rad = radians(lat_a)
    lat_b_rad = radians(lat_b)
    delta_lat = radians(lat_b - lat_a)
    delta_lng = radians(lng_b - lng_a)

    haversine = (
        sin(delta_lat / 2) ** 2
        + cos(lat_a_rad) * cos(lat_b_rad) * sin(delta_lng / 2) ** 2
    )
    return 2 * EARTH_RADIUS_METERS * asin(sqrt(haversine))


def zones_overlap(
    lat_a: float,
    lng_a: float,
    radius_a: int,
    lat_b: float,
    lng_b: float,
    radius_b: int,
) -> bool:
    return distance_meters(lat_a, lng_a, lat_b, lng_b) < radius_a + radius_b


def point_inside_zone(
    point_lat: float,
    point_lng: float,
    zone_lat: float,
    zone_lng: float,
    radius_meters: int,
) -> bool:
    return distance_meters(point_lat, point_lng, zone_lat, zone_lng) <= radius_meters


def should_link_record_to_new_zone(record, zone, existing_zones: list) -> bool:
    if any(existing_zone.type == zone.type for existing_zone in existing_zones):
        return False

    return point_inside_zone(
        record.latitude_camera,
        record.longitude_camera,
        zone.latitude,
        zone.longitude,
        zone.radius_meters,
    )


METERS_PER_DEGREE_LATITUDE = 110_000  # a bit under the real ~111.2 km so the prefilter never excludes


def distance_meters_sql(lat_a, lng_a, lat_b, lng_b):
    """SQL twin of `distance_meters` (haversine). Arguments are SQL expressions or numbers."""
    delta_lat = func.radians(lat_b - lat_a)
    delta_lng = func.radians(lng_b - lng_a)
    haversine = (
        func.power(func.sin(delta_lat / 2), 2)
        + func.cos(func.radians(lat_a)) * func.cos(func.radians(lat_b)) * func.power(func.sin(delta_lng / 2), 2)
    )
    # least(1, h): float rounding can push h a hair above 1 and asin() would fail.
    return 2 * EARTH_RADIUS_METERS * func.asin(func.sqrt(func.least(1.0, haversine)))


def link_records_to_zone_statement(zone):
    """One INSERT ... SELECT that links every record inside `zone` to it, inside the database.

    Same rule as `should_link_record_to_new_zone`: a record already linked to a zone of the
    same type is skipped. The latitude range is a cheap prefilter ahead of the exact distance.
    """
    latitude_margin = zone.radius_meters / METERS_PER_DEGREE_LATITUDE
    already_linked_to_type = (
        select(RecordMapZone.id)
        .join(MapZone, MapZone.id == RecordMapZone.map_zone_id)
        .where(RecordMapZone.record_id == Record.id, MapZone.type == zone.type)
        .exists()
    )
    candidates = select(Record.id, literal(zone.id)).where(
        and_(
            Record.latitude_camera.between(zone.latitude - latitude_margin, zone.latitude + latitude_margin),
            distance_meters_sql(
                Record.latitude_camera, Record.longitude_camera, zone.latitude, zone.longitude
            )
            <= zone.radius_meters,
            ~already_linked_to_type,
        )
    )
    return (
        pg_insert(RecordMapZone)
        .from_select(["record_id", "map_zone_id"], candidates)
        .on_conflict_do_nothing(constraint="uq_record_map_zones_record_zone")
    )
