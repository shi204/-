"""WGS84 / ECEF / ENU 坐标转换。

所有长度单位为米，角度单位为度（对外）或弧度（内部）。
"""
from __future__ import annotations

import math
from typing import Sequence

WGS84_A = 6378137.0
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)


def geodetic_to_ecef(lat: float, lon: float, h: float) -> tuple[float, float, float]:
    lat_r = math.radians(lat)
    lon_r = math.radians(lon)
    sin_lat = math.sin(lat_r)
    cos_lat = math.cos(lat_r)
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    x = (n + h) * cos_lat * math.cos(lon_r)
    y = (n + h) * cos_lat * math.sin(lon_r)
    z = (n * (1.0 - WGS84_E2) + h) * sin_lat
    return x, y, z


def ecef_to_geodetic(x: float, y: float, z: float) -> tuple[float, float, float]:
    """ECEF -> 大地坐标，迭代法，毫米级收敛。"""
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    if p < 1e-9:
        lat = math.copysign(math.pi / 2.0, z)
        h = abs(z) - WGS84_A * math.sqrt(1.0 - WGS84_E2)
        return math.degrees(lat), math.degrees(lon), h

    lat = math.atan2(z, p * (1.0 - WGS84_E2))
    h = 0.0
    for _ in range(8):
        sin_lat = math.sin(lat)
        n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
        h = p / math.cos(lat) - n
        lat_new = math.atan2(z, p * (1.0 - WGS84_E2 * n / (n + h)))
        if abs(lat_new - lat) < 1e-12:
            lat = lat_new
            break
        lat = lat_new
    sin_lat = math.sin(lat)
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    h = p / math.cos(lat) - n
    return math.degrees(lat), math.degrees(lon), h


def ecef_to_enu_matrix(lat0: float, lon0: float) -> tuple[tuple[float, ...], ...]:
    lat_r = math.radians(lat0)
    lon_r = math.radians(lon0)
    sin_lat, cos_lat = math.sin(lat_r), math.cos(lat_r)
    sin_lon, cos_lon = math.sin(lon_r), math.cos(lon_r)
    return (
        (-sin_lon, cos_lon, 0.0),
        (-sin_lat * cos_lon, -sin_lat * sin_lon, cos_lat),
        (cos_lat * cos_lon, cos_lat * sin_lon, sin_lat),
    )


def ecef_to_enu(
    xyz: Sequence[float], ref_lat: float, ref_lon: float, ref_h: float
) -> tuple[float, float, float]:
    x0, y0, z0 = geodetic_to_ecef(ref_lat, ref_lon, ref_h)
    dx, dy, dz = xyz[0] - x0, xyz[1] - y0, xyz[2] - z0
    r = ecef_to_enu_matrix(ref_lat, ref_lon)
    e = r[0][0] * dx + r[0][1] * dy + r[0][2] * dz
    n = r[1][0] * dx + r[1][1] * dy + r[1][2] * dz
    u = r[2][0] * dx + r[2][1] * dy + r[2][2] * dz
    return e, n, u


def enu_to_ecef(
    enu: Sequence[float], ref_lat: float, ref_lon: float, ref_h: float
) -> tuple[float, float, float]:
    x0, y0, z0 = geodetic_to_ecef(ref_lat, ref_lon, ref_h)
    r = ecef_to_enu_matrix(ref_lat, ref_lon)
    e, n, u = enu
    # ENU -> ECEF 使用旋转矩阵的转置
    dx = r[0][0] * e + r[1][0] * n + r[2][0] * u
    dy = r[0][1] * e + r[1][1] * n + r[2][1] * u
    dz = r[0][2] * e + r[1][2] * n + r[2][2] * u
    return x0 + dx, y0 + dy, z0 + dz


def geodetic_to_enu(
    lat: float, lon: float, h: float, ref_lat: float, ref_lon: float, ref_h: float
) -> tuple[float, float, float]:
    return ecef_to_enu(geodetic_to_ecef(lat, lon, h), ref_lat, ref_lon, ref_h)


def enu_to_geodetic(
    enu: Sequence[float], ref_lat: float, ref_lon: float, ref_h: float
) -> tuple[float, float, float]:
    return ecef_to_geodetic(*enu_to_ecef(enu, ref_lat, ref_lon, ref_h))


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2.0 * r * math.asin(math.sqrt(min(1.0, a)))
