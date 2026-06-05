from math import asin, cos, radians, sin, sqrt


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
