"""NMEA 0183 解析。

兼容 $GPGGA / $GNGGA / $BDGGA / $GBGGA 等多系统 talker，
同时解析 GST（定位精度）、GSA（PDOP）、GSV（卫星信噪比）。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

FIX_TEXT = {
    0: "无效解",
    1: "单点定位",
    2: "差分定位",
    3: "PPS定位",
    4: "RTK固定解",
    5: "RTK浮点解",
    6: "航位推算",
    7: "手动输入",
    8: "仿真定位",
}

DIFF_TEXT = {
    0: "无效解",
    1: "单点定位",
    2: "差分定位(DGPS)",
    3: "PPS无效",
    4: "RTK固定解",
    5: "RTK浮点解",
    6: "估算中",
}


def fix_text(quality: int) -> str:
    return FIX_TEXT.get(quality, f"未知({quality})")


def checksum_ok(sentence: str) -> bool:
    """校验 NMEA *hh 校验和；没有校验和时放行。"""
    if "*" not in sentence:
        return True
    try:
        body, cs = sentence[1:].split("*", 1)
        calc = 0
        for ch in body:
            calc ^= ord(ch)
        return int(cs[:2], 16) == calc
    except Exception:
        return False


def _dm_to_deg(value: str) -> float:
    """ddmm.mmmm -> 十进制度。"""
    if not value:
        raise ValueError("empty")
    dot = value.find(".")
    if dot < 0:
        dot = len(value)
    deg_digits = max(0, dot - 2)
    degrees = float(value[:deg_digits] or 0)
    minutes = float(value[deg_digits:])
    return degrees + minutes / 60.0


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


@dataclass
class Fix:
    ts: float
    lat: float
    lon: float
    alt: float
    fix: int = 0
    nsat: int = 0
    hdop: float = 99.9
    pdop: float = 99.9
    sigma_h: float | None = None
    sigma_v: float | None = None
    speed: float = 0.0
    course: float = 0.0
    talker: str = "GN"
    utc: str = ""
    raw: str = ""
    snr: dict[int, int] = field(default_factory=dict)
    # 厂商 $RUN 协议专用字段（NMEA 时保持默认值）
    protocol: str = "nmea"
    rel_enu: tuple[float, float, float] | None = None
    diff_status: int | None = None
    baseline_m: float | None = None
    sats_front: int | None = None
    sats_back: int | None = None

    @property
    def valid(self) -> bool:
        return self.fix > 0 and not (self.lat == 0.0 and self.lon == 0.0)

    @property
    def fix_label(self) -> str:
        if self.protocol == "run" and self.diff_status is not None:
            return DIFF_TEXT.get(self.diff_status, f"未知({self.diff_status})")
        return fix_text(self.fix)

    @property
    def precision_ok(self) -> bool:
        """是否达到毫米级形变监测可用的 RTK 固定解。"""
        if self.protocol == "run":
            return self.valid and self.diff_status == 4
        return self.valid and self.fix == 4

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "lat": self.lat,
            "lon": self.lon,
            "alt": self.alt,
            "fix": self.fix,
            "fix_text": self.fix_label,
            "nsat": self.nsat,
            "hdop": self.hdop,
            "pdop": self.pdop,
            "sigma_h": self.sigma_h,
            "sigma_v": self.sigma_v,
            "talker": self.talker,
            "utc": self.utc,
        }


class NmeaParser:
    """单接收机的 NMEA 状态机。"""

    def __init__(self) -> None:
        self.sentences = 0
        self.bad_checksum = 0
        self.unknown = 0
        self.last_gga: Fix | None = None
        self.last_rmc: dict[str, Any] = {}
        self.gst: dict[str, float] = {}
        self.gsa: dict[str, float] = {}
        self.snr: dict[int, int] = {}
        self.last_seen = 0.0

    def feed(self, line: str) -> Fix | None:
        line = line.strip().strip("\x00")
        if not line.startswith("$"):
            return None
        self.last_seen = time.time()
        self.sentences += 1
        if not checksum_ok(line):
            self.bad_checksum += 1
            return None
        body = line[1:].split("*", 1)[0]
        parts = body.split(",")
        if not parts:
            return None
        head = parts[0]
        if len(head) < 5:
            return None
        talker, kind = head[:2], head[2:5]
        try:
            if kind == "GGA":
                return self._parse_gga(parts, talker, line)
            if kind == "RMC":
                self._parse_rmc(parts)
            elif kind == "GST":
                self._parse_gst(parts)
            elif kind == "GSA":
                self._parse_gsa(parts)
            elif kind == "GSV":
                self._parse_gsv(parts)
            else:
                self.unknown += 1
        except (ValueError, IndexError):
            return None
        return None

    def _parse_gga(self, f: list[str], talker: str, raw: str) -> Fix | None:
        if len(f) < 15 or not f[2] or not f[4]:
            return None
        lat = _dm_to_deg(f[2]) * (-1.0 if f[3] == "S" else 1.0)
        lon = _dm_to_deg(f[4]) * (-1.0 if f[5] == "W" else 1.0)
        fix = _i(f[6])
        nsat = _i(f[7])
        hdop = _f(f[8], 99.9)
        alt = _f(f[9])
        fix_obj = Fix(
            ts=time.time(),
            lat=lat,
            lon=lon,
            alt=alt,
            fix=fix,
            nsat=nsat,
            hdop=hdop,
            pdop=self.gsa.get("pdop", 99.9),
            sigma_h=self.gst.get("sigma_h"),
            sigma_v=self.gst.get("sigma_v"),
            speed=self.last_rmc.get("speed", 0.0),
            course=self.last_rmc.get("course", 0.0),
            talker=talker,
            utc=f[1],
            raw=raw,
            snr=dict(self.snr),
        )
        self.last_gga = fix_obj
        return fix_obj

    def _parse_rmc(self, f: list[str]) -> None:
        if len(f) < 9:
            return
        self.last_rmc = {
            "utc": f[1],
            "status": f[2],
            "speed": _f(f[7]) * 0.514444,  # knots -> m/s
            "course": _f(f[8]),
        }

    def _parse_gst(self, f: list[str]) -> None:
        # $--GST,time,rms,stdLat,stdLon,stdAlt,stdMajor,stdMinor,orient
        if len(f) < 9:
            return
        self.gst = {
            "rms": _f(f[2]),
            "sigma_lat": abs(_f(f[3])),
            "sigma_lon": abs(_f(f[4])),
            "sigma_h": max(abs(_f(f[3])), abs(_f(f[4]))),
            "sigma_v": abs(_f(f[5])),
        }

    def _parse_gsa(self, f: list[str]) -> None:
        # $--GSA,mode,fixtype,sv...,pdop,hdop,vdop
        if len(f) < 18:
            return
        self.gsa = {
            "fix": _f(f[2]),
            "pdop": _f(f[15], 99.9),
            "hdop": _f(f[16], 99.9),
            "vdop": _f(f[17], 99.9),
        }

    def _parse_gsv(self, f: list[str]) -> None:
        # $--GSV,total,number,sv_in_view,[prn,elev,azim,snr]...
        total = _i(f[1])
        number = _i(f[2])
        if number == 1:
            self.snr = {}
        idx = 4
        while idx + 3 < len(f):
            prn = _i(f[idx])
            snr = _i(f[idx + 3], -1)
            if prn and snr >= 0:
                self.snr[prn] = max(snr, self.snr.get(prn, 0))
            idx += 4
        if number >= total:
            self.last_seen = time.time()


def parse_line(line: str) -> Fix | None:
    """一次性解析辅助函数，主要用于测试。"""
    return NmeaParser().feed(line)


def make_gga(
    lat: float,
    lon: float,
    alt: float,
    fix: int = 4,
    nsat: int = 22,
    hdop: float = 0.8,
    utc: str = "000000.00",
    talker: str = "GN",
) -> str:
    """生成一条合法的 GGA 语句，用于自测/模拟串口。"""

    def dm(value: float, degree_digits: int) -> str:
        negative = value < 0
        value = abs(value)
        deg = int(value)
        minutes = (value - deg) * 60.0
        signed_deg = -deg if negative else deg
        return f"{signed_deg:0{degree_digits}d}{minutes:07.4f}"

    lat_s = dm(lat, 2)
    lon_s = dm(lon, 3)
    body = (
        f"{talker}GGA,{utc},{lat_s},{'S' if lat < 0 else 'N'},"
        f"{lon_s},{'W' if lon < 0 else 'E'},{fix:02d},{nsat:02d},"
        f"{hdop:.1f},{alt:.3f},M,0.0,M,,"
    )
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return f"${body}*{cs:02X}"


def _self_test() -> None:
    samples = [
        "$GNGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47",
        "$GPGGA,092750.000,5321.6802,N,00630.3372,W,1,8,1.03,61.7,M,55.2,M,,*76",
    ]
    for raw in samples:
        fix = parse_line(raw)
        print(raw, "->", fix)

    lat, lon, alt = 29.563, 106.5516, 260.0
    gga = make_gga(lat, lon, alt)
    fix = parse_line(gga)
    assert fix is not None
    assert abs(fix.lat - lat) < 1e-7
    assert abs(fix.lon - lon) < 1e-7
    print("self-test ok:", gga, "->", fix.lat, fix.lon, fix.alt)


if __name__ == "__main__":
    _self_test()
