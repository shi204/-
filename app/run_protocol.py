"""厂商 $RUN 私有文本协议解析（严格校验，不猜测字段）。

依据用户提供的《车载智能终端程序通讯》与差分定位设备说明：

    $RUN,时间,开关量1,开关量2,开关量3,开关量4,开关量5,
         日期,GPS秒,航向,俯仰,横滚,纬度,经度,高度,
         东向距离,北向距离,天向距离,
         东向速度,北向速度,天向速度,
         东向速度差,北向速度差,天向速度差,
         基线长度,前天线卫星数,后天线卫星数,差分状态

其中“东/北/天向距离”是设备解算出的相对基线分量，可直接作为移动站
相对基站的位移观测值；无需前端展示经纬度。

差分状态：0 无效、1 单点、2 DGPS、3 PPS 无效、4 RTK 固定、
          5 RTK 浮点、6 估算。
只有 4（RTK 固定解）才允许进入毫米级形变解算；其余状态仅展示设备
状态，不计算、不展示真实位移。

协议来自厂商资料，仍需用真实移动站日志复核字段顺序和固件差异。
本模块只解析，不向设备写入任何内容。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

from .nmea import Fix

RUN_PREFIX = "$RUN,"
MIN_FIELDS = 27

# 字段下标（以 $RUN, 之后的逗号分隔字段计）
IDX_TIME = 0
IDX_DATE = 6
IDX_GPS_SEC = 7
IDX_HEADING = 8
IDX_LAT = 11
IDX_LON = 12
IDX_ALT = 13
IDX_EAST = 14
IDX_NORTH = 15
IDX_UP = 16
IDX_VE = 17
IDX_VN = 18
IDX_VU = 19
IDX_BASELINE = 23
IDX_SATS_FRONT = 24
IDX_SATS_BACK = 25
IDX_DIFF = 26

_DIFF_TO_FIX = {0: 0, 1: 1, 2: 2, 3: 0, 4: 4, 5: 5, 6: 6}


def _f(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _i(value: str, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def parse_run_frame(segment: str, ts: float | None = None) -> Fix | None:
    """解析一条（可能带 $DXY 尾部的）$RUN 帧；字段不足或越界时返回 None。"""
    segment = segment.strip().strip("\x00")
    if not segment.startswith(RUN_PREFIX):
        return None
    # 一帧可能以 $DXY 尾部结束，也可能与下一帧拼接
    body = segment[len(RUN_PREFIX):].split("$", 1)[0].strip()
    fields = [part.strip() for part in body.split(",")]
    if len(fields) < MIN_FIELDS:
        return None

    lat = _f(fields[IDX_LAT], float("nan"))
    lon = _f(fields[IDX_LON], float("nan"))
    alt = _f(fields[IDX_ALT], float("nan"))
    east = _f(fields[IDX_EAST], float("nan"))
    north = _f(fields[IDX_NORTH], float("nan"))
    up = _f(fields[IDX_UP], float("nan"))
    if not all(math.isfinite(v) for v in (lat, lon, alt, east, north, up)):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    if max(abs(east), abs(north), abs(up)) > 100000.0:
        return None

    diff_status = _i(fields[IDX_DIFF], -1)
    if diff_status not in _DIFF_TO_FIX:
        return None

    baseline = _f(fields[IDX_BASELINE], -1.0)
    if baseline < 0 or not math.isfinite(baseline) or baseline > 100000.0:
        return None

    sats_front = max(0, _i(fields[IDX_SATS_FRONT], 0))
    sats_back = max(0, _i(fields[IDX_SATS_BACK], 0))
    if sats_front > 200 or sats_back > 200:
        return None
    nsat = max(sats_front, sats_back)

    ve = _f(fields[IDX_VE], 0.0)
    vn = _f(fields[IDX_VN], 0.0)
    vu = _f(fields[IDX_VU], 0.0)
    if max(abs(ve), abs(vn), abs(vu)) > 1000.0:
        return None

    fix_value = _DIFF_TO_FIX[diff_status]
    has_position = diff_status in (1, 2, 4, 5, 6)
    if has_position and lat == 0.0 and lon == 0.0:
        has_position = False

    return Fix(
        ts=ts if ts is not None else time.time(),
        lat=lat,
        lon=lon,
        alt=alt,
        fix=fix_value if has_position else 0,
        nsat=nsat,
        hdop=99.9,
        pdop=99.9,
        sigma_h=None,
        sigma_v=None,
        speed=math.hypot(ve, vn),
        course=(math.degrees(math.atan2(ve, vn)) + 360.0) % 360.0 if (ve or vn) else 0.0,
        talker="RUN",
        utc=fields[IDX_TIME],
        raw=segment[:300],
        protocol="run",
        rel_enu=(east, north, up),
        diff_status=diff_status,
        baseline_m=baseline,
        sats_front=sats_front,
        sats_back=sats_back,
    )


@dataclass
class RunStats:
    frames: int = 0
    bad_frames: int = 0
    last_seen: float = 0.0


class RunParser:
    """处理可能没有换行、多帧粘连的 $RUN 文本流。"""

    def __init__(self) -> None:
        self.stats = RunStats()
        self.last_fix: Fix | None = None

    def feed_text(self, text: str, ts: float | None = None) -> list[Fix]:
        out: list[Fix] = []
        for part in text.split("$"):
            part = part.strip()
            if not part.startswith("RUN,"):
                continue
            self.stats.frames += 1
            self.stats.last_seen = time.time()
            fix = parse_run_frame("$" + part, ts)
            if fix is None:
                self.stats.bad_frames += 1
                continue
            self.last_fix = fix
            out.append(fix)
        return out


def make_run_frame(
    east: float = 0.0,
    north: float = 0.0,
    up: float = 0.0,
    lat: float = 29.563,
    lon: float = 106.5516,
    alt: float = 260.0,
    diff_status: int = 4,
    front_sats: int = 12,
    back_sats: int = 18,
    baseline: float = 0.0,
    ve: float = 0.0,
    vn: float = 0.0,
    vu: float = 0.0,
) -> str:
    """生成一条用于测试/模拟串口的 $RUN 帧（不是真实设备数据）。"""
    fields = [
        "000000", "ff", "ff", "ff", "00", "00", "70915", "40353.000",
        "0.00", "0.00", "0.00",
        f"{lat:.7f}", f"{lon:.7f}", f"{alt:.3f}",
        f"{east:.3f}", f"{north:.3f}", f"{up:.3f}",
        f"{ve:.3f}", f"{vn:.3f}", f"{vu:.3f}",
        "0.000", "0.000", "0.000", f"{baseline:.3f}",
        str(front_sats), str(back_sats), str(diff_status),
    ]
    return "$RUN," + ",".join(fields) + "$DXY, 0, 0"


if __name__ == "__main__":
    sample = (
        "$RUN,022723,ff,ff,ff,00,00,70915,40353.000,197.80,0.00,0.00,"
        "34.8443296,113.7376900,105.553,15.407,11.373,-13.754,"
        "-0.003,-0.003,-0.001,-0.004,-0.003,-0.003,0.000,0,10,1"
        "$DXY, 0, 0"
    )
    parsed = parse_run_frame(sample)
    print(parsed)
    print("rel_enu", parsed.rel_enu if parsed else None)
