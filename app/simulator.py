"""演示用 GNSS 数据模拟器。

生成与真实接收机同构的 Fix 对象，直接进入采集/解算/预警全链路，
用于路演兜底、桌面联调与算法验证。
"""
from __future__ import annotations

import math
import random
import threading
import time
from typing import Any, Callable

from .coordinate import enu_to_geodetic, geodetic_to_enu
from .nmea import Fix

SCENARIOS = {
    "normal": "稳定观测",
    "slow": "缓慢滑动",
    "accelerate": "加速变形",
    "step": "阶跃位移",
    "oscillate": "周期振荡",
    "recover": "回归稳定",
}


class Simulator:
    def __init__(self, cfg: dict[str, Any], emit: Callable[[str, Fix], None]) -> None:
        self.cfg = cfg
        self.emit = emit
        self.running = False
        self.thread: threading.Thread | None = None
        self.started_at = time.time()
        self.scenario = cfg.get("simulation", {}).get("scenario", "normal")
        self.station_id = "all"
        self.scenario_started = time.time()
        self.step_mm = float(cfg.get("simulation", {}).get("step_mm", 40.0))
        self.rate_mm_s = float(cfg.get("simulation", {}).get("rate_mm_s", 0.5))
        self.accel_mm_s2 = float(cfg.get("simulation", {}).get("accel_mm_s2", 0.02))
        self.direction_deg = float(cfg.get("simulation", {}).get("direction_deg", 35.0))
        self.noise_mm = float(cfg.get("simulation", {}).get("noise_mm", 3.2))
        self.common_noise_mm = float(cfg.get("simulation", {}).get("common_noise_mm", 7.0))
        self._rng = random.Random(20261020)
        self._recover_from: dict[str, float] = {}
        self._isolation_offset: float = 0.0

        ref = cfg["reference"]
        self.ref = (float(ref["lat"]), float(ref["lon"]), float(ref["alt"]))
        self.nominal: dict[str, tuple[float, float, float]] = {}
        for station in cfg.get("stations", []):
            # 监测站坐标一律相对基站：优先使用显式 E/N/U（米），
            # 没有配置时才用大地坐标转换，避免仿真站点偏离基准站。
            rel = station.get("relative_enu_m")
            if rel is not None:
                self.nominal[station["id"]] = (
                    float(rel[0]), float(rel[1]), float(rel[2])
                )
            else:
                self.nominal[station["id"]] = geodetic_to_enu(
                    float(station["lat"]), float(station["lon"]), float(station["alt"]),
                    *self.ref,
                )
        self.sampling_hz = max(0.2, float(cfg.get("sampling_hz", 1)))

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, name="simulator", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.running = False

    def set_scenario(self, scenario: str, station_id: str = "all",
                     step_mm: float | None = None, rate_mm_s: float | None = None,
                     accel_mm_s2: float | None = None,
                     direction_deg: float | None = None) -> dict:
        if scenario not in SCENARIOS:
            raise ValueError(f"未知场景: {scenario}")
        self.scenario = scenario
        self.station_id = station_id or "all"
        self.scenario_started = time.time()
        if step_mm is not None:
            self.step_mm = float(step_mm)
        if rate_mm_s is not None:
            self.rate_mm_s = float(rate_mm_s)
        if accel_mm_s2 is not None:
            self.accel_mm_s2 = float(accel_mm_s2)
        if direction_deg is not None:
            self.direction_deg = float(direction_deg)
        self._recover_from.clear()
        return self.info()

    def info(self) -> dict:
        return {
            "scenario": self.scenario,
            "scenario_name": SCENARIOS.get(self.scenario, self.scenario),
            "station_id": self.station_id,
            "elapsed_s": round(time.time() - self.scenario_started, 1),
            "step_mm": self.step_mm,
            "rate_mm_s": self.rate_mm_s,
            "accel_mm_s2": self.accel_mm_s2,
            "direction_deg": self.direction_deg,
        }

    # ------------------------------------------------------------------
    def _common_wander(self, t: float) -> tuple[float, float, float]:
        """所有站点共有的低频误差，相对基线解算时会自动抵消。"""
        a = self.common_noise_mm
        return (
            a * math.sin(2 * math.pi * t / 73.0) + 0.4 * a * math.sin(2 * math.pi * t / 31.0),
            a * math.cos(2 * math.pi * t / 61.0) + 0.4 * a * math.cos(2 * math.pi * t / 23.0),
            0.35 * a * math.sin(2 * math.pi * t / 89.0),
        )

    def _scenario_mm(self, station: dict, t: float) -> tuple[float, float, float]:
        """返回该站当前的模拟形变量（mm, ENU）。"""
        sid = station["id"]
        if station.get("role") == "base":
            return 0.0, 0.0, 0.0
        if self.station_id not in ("all", sid):
            return 0.0, 0.0, 0.0
        elapsed = max(0.0, t - self.scenario_started)
        scenario = self.scenario

        if scenario == "normal":
            return 0.0, 0.0, 0.0
        if scenario == "slow":
            d = self.rate_mm_s * elapsed
        elif scenario == "accelerate":
            d = 0.5 * self.accel_mm_s2 * elapsed * elapsed
        elif scenario == "step":
            d = self.step_mm * min(1.0, elapsed / 3.0)
        elif scenario == "oscillate":
            d = 0.5 * self.step_mm * math.sin(2 * math.pi * elapsed / 60.0)
        elif scenario == "recover":
            start = self._recover_from.setdefault(sid, self.step_mm)
            d = start * max(0.0, 1.0 - elapsed / 25.0)
        else:
            d = 0.0

        # 桥梁跨中主要表现为竖向挠度，坡体主要表现为水平滑移
        if sid == "rover1":
            return (0.10 * d, 0.12 * d, d)
        angle = math.radians(self.direction_deg)
        return (d * math.sin(angle), d * math.cos(angle), 0.08 * d)

    def _make_fix(self, station: dict, t: float) -> Fix:
        common = self._common_wander(t)
        disp = self._scenario_mm(station, t)
        nominal = self.nominal.get(station["id"], (0.0, 0.0, 0.0))
        sigma = 0.45 if station.get("role") == "base" else self.noise_mm
        noise = (
            self._rng.gauss(0.0, sigma),
            self._rng.gauss(0.0, sigma),
            self._rng.gauss(0.0, sigma * 1.6),
        )
        # 偶发粗差，用于验证卡尔曼新息门限
        if self._rng.random() < 0.0025:
            noise = (
                noise[0] + self._rng.choice((-1, 1)) * self._rng.uniform(40, 120),
                noise[1] + self._rng.choice((-1, 1)) * self._rng.uniform(40, 120),
                noise[2],
            )
        enu = (
            nominal[0] + (common[0] + noise[0] + disp[0]) / 1000.0,
            nominal[1] + (common[1] + noise[1] + disp[1]) / 1000.0,
            nominal[2] + (common[2] + noise[2] + disp[2]) / 1000.0,
        )
        lat, lon, alt = enu_to_geodetic(enu, *self.ref)
        # 与厂商 $RUN 一致：基线长度是移动站相对基准站的空间距离。
        baseline_m = None
        if station.get("role") != "base":
            bx, by, bz = self.nominal.get("base", (0.0, 0.0, 0.0))
            baseline_m = math.sqrt(
                (enu[0] - bx) ** 2 + (enu[1] - by) ** 2 + (enu[2] - bz) ** 2
            )
        nsat = self._rng.randint(19, 29)
        hdop = max(0.5, 0.72 + self._rng.random() * 0.35)
        sigma_h = 0.006 + self._rng.random() * 0.006
        return Fix(
            ts=t,
            lat=lat,
            lon=lon,
            alt=alt,
            fix=4,
            nsat=nsat,
            hdop=hdop,
            pdop=hdop * 1.6,
            sigma_h=sigma_h,
            sigma_v=sigma_h * 1.7,
            talker="GN",
            utc=time.strftime("%H%M%S", time.gmtime(t)) + ".00",
            raw="SIMULATED",
            protocol="sim",
            baseline_m=baseline_m,
        )

    def _run(self) -> None:
        interval = 1.0 / self.sampling_hz
        next_tick = time.time()
        while self.running:
            now = time.time()
            if now < next_tick:
                time.sleep(min(0.05, next_tick - now))
                continue
            next_tick += interval
            if next_tick < now - 2.0:
                next_tick = now + interval
            for station in self.cfg.get("stations", []):
                if not station.get("enabled", True):
                    continue
                try:
                    self.emit(station["id"], self._make_fix(station, now))
                except Exception:
                    pass
