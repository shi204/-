"""监测引擎：采集调度、坐标解算、滤波、特征、预警、存储、快照。"""
from __future__ import annotations

import copy
import threading
import time
from collections import deque
from typing import Any

from .alerts import AlertEngine, LEVEL_COLORS, LEVEL_NAMES
from .config import DATA_DIR, LOG_DIR, load_config, save_config
from .coordinate import geodetic_to_enu
from .features import FeatureExtractor
from .filters import KalmanCA
from .serial_reader import SerialReader, list_serial_ports, serial_available
from .simulator import SCENARIOS, Simulator
from .storage import Storage


def empty_features(t: float | None = None) -> dict[str, Any]:
    t = t or time.time()
    keys = {
        "de_m": 0.0, "dn_m": 0.0, "du_m": 0.0,
        "de_mm": 0.0, "dn_mm": 0.0, "du_mm": 0.0,
        "h_mm": 0.0, "v_mm": 0.0, "total_mm": 0.0,
        "speed_mm_s": 0.0, "speed_mm_min": 0.0, "speed_h_mm_min": 0.0,
        "accel_mm_min2": 0.0, "accel_trend_mm_min2": 0.0,
        "direction_deg": 0.0, "noise_mm": 0.0, "zscore": 0.0,
        "slope_mm_min": 0.0, "calib_progress": 0.0,
    }
    out = {"ts": t, "calibrating": True, "sigma_anomaly": False,
           "cusum_anomaly": False, "origin": None}
    out.update(keys)
    return out


class StationRuntime:
    def __init__(self, cfg: dict[str, Any], processing: dict[str, Any],
                 thresholds: dict[str, Any], mode: str,
                 protocol_verified: bool = True) -> None:
        self.cfg = cfg
        self.id = cfg["id"]
        self.name = cfg.get("name", self.id)
        self.role = cfg.get("role", "rover")
        self.mode = mode
        self.protocol_verified = protocol_verified
        sim = mode == "simulation"
        noise_m = 0.004 if sim else float(processing.get("measurement_noise_m", 0.6))
        q = (
            float(processing.get("simulation_process_noise", 0.00005))
            if sim
            else float(processing.get("process_noise", 0.008))
        )
        self.kalman = KalmanCA(
            meas_var=noise_m * noise_m,
            process_noise=q,
            max_gap=float(processing.get("max_gap_s", 15.0)),
        )
        self.extractor = FeatureExtractor(
            calibration_samples=int(processing.get("calibration_samples", 30)),
            velocity_window_s=float(processing.get("velocity_window_s", 30)),
            noise_window_s=float(processing.get("noise_window_s", 90)),
            prediction_window_s=float(processing.get("prediction_window_s", 120)),
        )
        self.alert = AlertEngine(copy.deepcopy(thresholds))
        self.latest_fix: Any = None
        self.filtered: tuple[float, float, float] | None = None
        self.features: dict[str, Any] = empty_features()
        self.alert_state: dict[str, Any] = {
            "level": 0, "raw_level": 0, "name": LEVEL_NAMES[0],
            "color": LEVEL_COLORS[0], "reasons": [], "anomaly": False,
            "trend": "stable", "next_threshold_mm": None,
            "time_to_threshold_min": None,
        }
        self.level = 0
        self.last_saved_level = 0
        self.last_seen = 0.0
        self.fix_count = 0
        self.rejected = 0
        self.base_ok = self.role == "base"
        self.connection: dict[str, Any] = {
            "state": "idle", "message": "等待启动",
            "port": cfg.get("port", ""), "baud": cfg.get("baud", 0),
        }

    @property
    def online(self) -> bool:
        if self.last_seen <= 0:
            return False
        if self.mode == "simulation":
            return time.time() - self.last_seen < 6.0
        return time.time() - self.last_seen < 12.0

    @property
    def data_valid(self) -> bool:
        """只有仿真数据，或协议已验证且达到 RTK 固定解的真实数据，才允许展示位移。"""
        if self.mode == "serial" and not self.protocol_verified:
            return False
        if not self.online:
            return False
        if (
            self.mode == "serial"
            and self.role != "base"
            and (self.latest_fix is None or not getattr(self.latest_fix, "precision_ok", False))
        ):
            return False
        return self.extractor.origin is not None

    @property
    def data_status(self) -> str:
        if self.mode == "serial" and not self.protocol_verified:
            return "unverified"
        if not self.online:
            return "offline"
        if (
            self.mode == "serial"
            and self.role != "base"
            and self.latest_fix is not None
            and not getattr(self.latest_fix, "precision_ok", False)
        ):
            return "low_precision"
        if self.extractor.origin is None:
            return "calibrating"
        if self.mode == "simulation":
            return "simulation"
        return "verified"

    @property
    def data_note(self) -> str:
        status = self.data_status
        return {
            "unverified": "原始数据采集中，协议待验证；不计算真实位移",
            "offline": "设备离线或未收到有效定位",
            "low_precision": "定位精度不足（非 RTK 固定解）；不计算真实位移",
            "calibrating": "正在建立相对位移基准…",
            "simulation": "仿真数据 · 仅用于功能演示",
            "verified": "协议已验证 · RTK 固定解 · 相对位移解算中",
        }.get(status, "")

    def reset(self) -> None:
        self.kalman = KalmanCA(
            meas_var=self.kalman.meas_var,
            process_noise=self.kalman.q,
            max_gap=self.kalman.max_gap,
        )
        self.extractor.reset()
        self.alert = AlertEngine(copy.deepcopy(self.alert.cfg))
        self.features = empty_features()
        self.alert_state = {
            "level": 0, "raw_level": 0, "name": LEVEL_NAMES[0],
            "color": LEVEL_COLORS[0], "reasons": [], "anomaly": False,
            "trend": "stable", "next_threshold_mm": None,
            "time_to_threshold_min": None,
        }
        self.level = 0
        self.last_saved_level = 0
        self.filtered = None
        self.rejected = 0

    def public(self) -> dict[str, Any]:
        f = self.features
        a = self.alert_state
        fix = self.latest_fix
        enu = self.filtered or (0.0, 0.0, 0.0)
        valid = self.data_valid
        protocol = getattr(fix, "protocol", "unknown") if fix is not None else "unknown"
        precision_ok = bool(getattr(fix, "precision_ok", False)) if fix is not None else False
        return {
            "id": self.id,
            "name": self.name,
            "short": self.cfg.get("short", self.id.upper()),
            "role": self.role,
            "color": self.cfg.get("color", "#22d3ee"),
            "description": self.cfg.get("description", ""),
            "scene": self.cfg.get("scene", {}),
            "online": self.online,
            "fix": int(getattr(fix, "fix", 0)) if fix else 0,
            "fix_text": getattr(fix, "fix_label", "等待数据") if fix else "等待数据",
            "nsat": int(getattr(fix, "nsat", 0)) if fix else 0,
            "hdop": round(float(getattr(fix, "hdop", 0.0)), 2) if fix else 0.0,
            "protocol": protocol,
            "protocol_name": {
                "run": "厂商 $RUN 协议",
                "nmea": "NMEA 0183",
                "sim": "仿真注入",
            }.get(protocol, "等待识别"),
            "diff_status": getattr(fix, "diff_status", None) if fix is not None else None,
            "diff_text": getattr(fix, "fix_label", "") if fix is not None else "",
            "precision_ok": precision_ok,
            "baseline_m": (
                round(float(fix.baseline_m), 3)
                if fix is not None and getattr(fix, "baseline_m", None) is not None
                else None
            ),
            "sats_front": getattr(fix, "sats_front", None) if fix is not None else None,
            "sats_back": getattr(fix, "sats_back", None) if fix is not None else None,
            # 不向前端输出任何经纬度/大地坐标，仅保留内部解算后的相对 ENU 偏移。
            "enu": {
                "e": round(enu[0], 3),
                "n": round(enu[1], 3),
                "u": round(enu[2], 3),
            },
            "data_valid": valid,
            "data_status": self.data_status,
            "data_note": self.data_note,
            "is_simulation": self.mode == "simulation",
            "disp": ({
                "e": round(float(f.get("de_mm", 0.0)), 2),
                "n": round(float(f.get("dn_mm", 0.0)), 2),
                "u": round(float(f.get("du_mm", 0.0)), 2),
                "h": round(float(f.get("h_mm", 0.0)), 2),
                "total": round(float(f.get("total_mm", 0.0)), 2),
            } if valid else None),
            "speed_mm_min": round(float(f.get("speed_mm_min", 0.0)), 3) if valid else None,
            "accel_mm_min2": round(float(f.get("accel_mm_min2", 0.0)), 3) if valid else None,
            "direction_deg": round(float(f.get("direction_deg", 0.0)), 1) if valid else None,
            "noise_mm": round(float(f.get("noise_mm", 0.0)), 2) if valid else None,
            "level": int(a.get("level", 0)) if valid else 0,
            "level_name": a.get("name", LEVEL_NAMES[0]) if valid else LEVEL_NAMES[0],
            "level_color": a.get("color", LEVEL_COLORS[0]) if valid else LEVEL_COLORS[0],
            "reasons": a.get("reasons", []) if valid else [],
            "anomaly": bool(a.get("anomaly", False)) if valid else False,
            "trend": a.get("trend", "stable") if valid else "unknown",
            "time_to_threshold_min": a.get("time_to_threshold_min") if valid else None,
            "next_threshold_mm": a.get("next_threshold_mm") if valid else None,
            "calibrating": bool(f.get("calibrating", True)),
            "calib_progress": round(float(f.get("calib_progress", 0.0)), 2),
            "rejected": self.rejected,
            "fix_count": self.fix_count,
            "last_seen": self.last_seen,
            "base_ok": self.base_ok,
            "connection": self.connection,
        }


class MonitoringEngine:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self.lock = threading.RLock()
        self.mode = str(cfg.get("mode", "simulation"))
        self.protocol_verified = bool(
            cfg.get("acquisition", {}).get("protocol_verified", False)
        )
        ref = cfg.get("reference", {})
        self.ref = (
            float(ref.get("lat", 31.9831969)),
            float(ref.get("lon", 119.6265216)),
            float(ref.get("alt", 18.1001593)),
        )
        self.storage = Storage(DATA_DIR / "monitor.db")
        self.started_at = time.time()
        self._fix_times: deque[float] = deque(maxlen=2000)
        # 告警事件持久化在 SQLite 中供导出；当前会话只展示本次运行产生的事件，
        # 避免上一次演示的黄色/橙色告警残留在新基准上。
        self._alerts: deque[dict] = deque(maxlen=100)
        self._simulator: Simulator | None = None
        self._readers: list[SerialReader] = []
        self._started = False
        self._runtimes: dict[str, StationRuntime] = {}
        self._build_runtimes()
        self._sample_counter = 0

    # ------------------------------------------------------------------
    def _build_runtimes(self) -> None:
        processing = self.cfg.get("processing", {})
        thresholds = self.cfg.get("thresholds", {})
        self._runtimes = {}
        for station in self.cfg.get("stations", []):
            if station.get("enabled", True):
                self._runtimes[station["id"]] = StationRuntime(
                    station, processing, thresholds, self.mode,
                    self.protocol_verified,
                )

    @property
    def runtimes(self) -> dict[str, StationRuntime]:
        return self._runtimes

    # ------------------------------------------------------------------
    def start(self) -> None:
        with self.lock:
            if self._started:
                return
            self._started = True
            self.storage.add_event("app", f"系统启动，模式={self.mode}")
            self._start_sources()

    def _start_sources(self) -> None:
        if self.mode == "simulation":
            self._simulator = Simulator(self.cfg, self.ingest)
            self._simulator.start()
        else:
            self._readers = []
            for station in self.cfg.get("stations", []):
                if not station.get("enabled", True):
                    continue
                port = str(station.get("port") or "").strip()
                if not port:
                    st = self._runtimes.get(station["id"])
                    if st is not None:
                        st.connection = {
                            "state": "unconfigured",
                            "message": "未配置串口（只读，禁止自动改写设备参数）",
                            "port": "",
                            "baud": station.get("baud", 0),
                        }
                    continue
                reader = SerialReader(
                    station, self.ingest, self._on_serial_status, LOG_DIR
                )
                self._readers.append(reader)
                reader.start()

    def _stop_sources(self) -> None:
        if self._simulator is not None:
            self._simulator.stop()
            self._simulator = None
        for reader in self._readers:
            try:
                reader.stop()
            except Exception:
                pass
        self._readers = []

    def _on_serial_status(self, station_id: str, status: dict[str, Any]) -> None:
        with self.lock:
            st = self._runtimes.get(station_id)
            if st is not None:
                st.connection = status

    # ------------------------------------------------------------------
    def ingest(self, station_id: str, fix: Any) -> None:
        if fix is None or not getattr(fix, "valid", False):
            return
        now = time.time()
        with self.lock:
            st = self._runtimes.get(station_id)
            if st is None:
                return
            st.latest_fix = fix
            st.last_seen = now
            st.fix_count += 1
            self._fix_times.append(now)

            if self.mode == "serial" and not self.protocol_verified:
                # 只读采集阶段：仅统计设备状态，不计算、不存储、不展示真实位移
                st.features = empty_features()
                st.filtered = None
                st.level = 0
                st.alert_state = {
                    "level": 0, "raw_level": 0, "name": LEVEL_NAMES[0],
                    "color": LEVEL_COLORS[0], "reasons": [], "anomaly": False,
                    "trend": "unknown", "next_threshold_mm": None,
                    "time_to_threshold_min": None,
                }
                return

            if (
                self.mode == "serial"
                and st.role != "base"
                and not getattr(fix, "precision_ok", False)
            ):
                # 单点/浮点/DGPS 解不进入毫米级形变解算，只保留设备状态
                st.features = empty_features()
                st.filtered = None
                st.level = 0
                st.alert_state = {
                    "level": 0, "raw_level": 0, "name": LEVEL_NAMES[0],
                    "color": LEVEL_COLORS[0], "reasons": [], "anomaly": False,
                    "trend": "unknown", "next_threshold_mm": None,
                    "time_to_threshold_min": None,
                }
                return

            rel_enu = getattr(fix, "rel_enu", None)
            try:
                if rel_enu is not None and st.role != "base":
                    # 厂商 $RUN 已给出相对基线的 E/N/U，直接使用，避免二次坐标转换
                    enu = (float(rel_enu[0]), float(rel_enu[1]), float(rel_enu[2]))
                else:
                    enu = geodetic_to_enu(fix.lat, fix.lon, fix.alt, *self.ref)
            except Exception:
                return
            sigma = getattr(fix, "sigma_h", None)
            if sigma and 0.003 < sigma < 10.0:
                st.kalman.set_measurement_noise(max(0.005, float(sigma)))
            out = st.kalman.update(enu, fix.ts or now)
            st.filtered = out.pos
            if out.rejected:
                st.rejected += 1

            if st.role == "base":
                st.base_ok = True
                feature_input = out.pos
            elif rel_enu is not None:
                # 设备内部已完成基线解算，直接使用相对位移
                st.base_ok = True
                feature_input = out.pos
            else:
                base = self._runtimes.get("base")
                base_filtered = base.filtered if base is not None else None
                if base_filtered is not None:
                    feature_input = (
                        out.pos[0] - base_filtered[0],
                        out.pos[1] - base_filtered[1],
                        out.pos[2] - base_filtered[2],
                    )
                    st.base_ok = True
                else:
                    feature_input = out.pos
                    st.base_ok = False

            features = st.extractor.add(
                fix.ts or now, feature_input, out.vel, out.acc
            )
            st.features = features
            state = st.alert.judge(features)
            if st.role == "base":
                # 基准站只做参考稳定性监视，不参与形变分级，避免参考站噪声
                # 触发全系统红色告警。基准站明显漂移会在 reference_stable 中体现。
                state = dict(state)
                state["level"] = 0
                state["name"] = LEVEL_NAMES[0]
                state["color"] = LEVEL_COLORS[0]
                state["reasons"] = []
            st.alert_state = state
            st.level = int(state["level"])

            # 基准建立期间不写入位移样本：此时还没有可比较的形变零点，
            # 避免曲线出现一段会被误读为“实测零位移”的假平台。
            if not features.get("calibrating"):
                disp = (features.get("de_m", 0.0), features.get("dn_m", 0.0),
                        features.get("du_m", 0.0))
                self.storage.insert_sample(st.id, fix, enu, disp, features, st.level)
                self._sample_counter += 1

            if st.level != st.last_saved_level:
                message = "；".join(state.get("reasons") or [])
                if st.level == 0:
                    message = message or "形变恢复至正常范围"
                alert = self.storage.insert_alert(
                    st.id, st.level, state["name"], message, features
                )
                self._alerts.appendleft(alert)
                st.last_saved_level = st.level

    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            stations = [st.public() for st in self._runtimes.values()]
            now = time.time()
            online = sum(1 for st in self._runtimes.values() if st.online)
            recent = [t for t in self._fix_times if now - t <= 5.0]
            rate = len(recent) / 5.0 if recent else 0.0
            levels = [st.level for st in self._runtimes.values()]
            max_level = max(levels) if levels else 0
            last_seen = max((st.last_seen for st in self._runtimes.values()), default=0)
            latency = (now - last_seen) * 1000.0 if last_seen else None
            counts = self.storage.counts()
            base = self._runtimes.get("base")
            base_drift = None
            reference_stable = None
            if base is not None and base.data_valid:
                base_drift = max(
                    float(base.features.get("h_mm", 0.0)),
                    abs(float(base.features.get("v_mm", 0.0))),
                )
                blue_d = float(self.cfg.get("thresholds", {}).get("blue", {}).get("d", 10.0))
                reference_stable = base_drift < max(50.0, 3.0 * blue_d)
            policy = self.data_policy()
            return {
                "type": "snapshot",
                "ts": now,
                "server_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "mode": self.mode,
                "mode_name": "实时采集" if self.mode == "serial" else "仿真演示",
                "data_policy": policy,
                "protocol_verified": self.protocol_verified,
                "stations": stations,
                "alerts": list(self._alerts)[:30],
                "system": {
                    "online": online,
                    "total": len(self._runtimes),
                    "fix_rate_hz": round(rate, 2),
                    "uptime_s": int(now - self.started_at),
                    "sample_count": counts["samples"],
                    "unack_alerts": sum(1 for a in self._alerts if not a.get("ack")),
                    "alert_level": max_level,
                    "alert_name": LEVEL_NAMES[max_level],
                    "alert_color": LEVEL_COLORS[max_level],
                    "base_ready": bool(base and base.extractor.origin is not None),
                    "base_drift_mm": round(base_drift, 2) if base_drift is not None else None,
                    "reference_stable": reference_stable,
                    "latency_ms": round(latency, 0) if latency is not None else None,
                    "serial_available": serial_available(),
                },
                "thresholds": copy.deepcopy(self.cfg.get("thresholds", {})),
                "simulation": self._simulator.info() if self._simulator else None,
                "scenarios": SCENARIOS,
            }

    def data_policy(self) -> str:
        if self.mode == "simulation":
            return "simulation"
        if not self.protocol_verified:
            return "raw_only"
        return "verified"

    def alerts(self, limit: int = 50) -> list[dict]:
        with self.lock:
            return list(self._alerts)[:limit]

    def ack_alert(self, alert_id: int) -> bool:
        with self.lock:
            ok = self.storage.ack_alert(alert_id)
            for item in self._alerts:
                if item.get("id") == alert_id:
                    item["ack"] = 1
            return ok

    # ------------------------------------------------------------------
    def set_mode(self, mode: str) -> dict[str, Any]:
        if mode not in ("simulation", "serial"):
            raise ValueError("mode 必须是 simulation 或 serial")
        with self.lock:
            self._stop_sources()
            self.mode = mode
            self.cfg["mode"] = mode
            save_config(self.cfg)
            self._reset_runtimes()
            self._build_runtimes()
            self._fix_times.clear()
            self.storage.add_event("mode", f"切换为{'实时采集' if mode == 'serial' else '仿真演示'}")
            self._start_sources()
        return self.snapshot()

    def _reset_runtimes(self) -> None:
        for st in self._runtimes.values():
            st.reset()

    def reset_baselines(self, station_id: str | None = None) -> None:
        with self.lock:
            targets = (
                [self._runtimes[station_id]]
                if station_id and station_id in self._runtimes
                else list(self._runtimes.values())
            )
            for st in targets:
                st.reset()
            self.storage.add_event("baseline", "基线已重置")
        if self._simulator is not None:
            self._simulator.set_scenario("normal")

    def calibrate(self) -> dict[str, Any]:
        if self.mode == "serial" and not self.protocol_verified:
            raise RuntimeError("协议验证前禁止标定阈值；请先完成原始数据协议验证")
        with self.lock:
            noises = []
            for st in self._runtimes.values():
                if st.role == "rover" and not st.features.get("calibrating"):
                    n = float(st.features.get("noise_mm", 0.0))
                    if n > 0:
                        noises.append(n)
            sigma = max(3.0, sorted(noises)[len(noises) // 2] if noises else 6.0)
            blue = max(8.0, round(4.0 * sigma, 1))
            yellow = max(blue * 2.0, round(8.0 * sigma, 1))
            orange = max(yellow * 2.0, round(14.0 * sigma, 1))
            red = max(orange * 2.0, round(24.0 * sigma, 1))
            v_scale = max(1.0, sigma / 5.0)
            a_scale = max(1.0, sigma / 5.0)
            th = {
                "blue": {"d": blue, "v": round(20.0 * v_scale, 1),
                         "a": round(15.0 * a_scale, 1)},
                "yellow": {"d": yellow, "v": round(40.0 * v_scale, 1),
                           "a": round(30.0 * a_scale, 1)},
                "orange": {"d": orange, "v": round(80.0 * v_scale, 1),
                           "a": round(60.0 * a_scale, 1)},
                "red": {"d": red, "v": round(150.0 * v_scale, 1),
                        "a": round(120.0 * a_scale, 1)},
                "up_samples": 3,
                "down_samples": 20,
            }
            self.cfg["thresholds"] = th
            save_config(self.cfg)
            for st in self._runtimes.values():
                st.alert.update_thresholds(copy.deepcopy(th))
                st.reset()
            self.storage.add_event(
                "calibrate", f"阈值标定：噪声σ={sigma:.2f} mm，蓝色阈值={blue} mm"
            )
            return {"sigma_mm": round(sigma, 2), "thresholds": th}

    def update_thresholds(self, thresholds: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            current = copy.deepcopy(self.cfg.get("thresholds", {}))
            for key, value in thresholds.items():
                if isinstance(value, dict) and isinstance(current.get(key), dict):
                    current[key].update(value)
                else:
                    current[key] = value
            self.cfg["thresholds"] = current
            save_config(self.cfg)
            for st in self._runtimes.values():
                st.alert.update_thresholds(copy.deepcopy(current))
            return current

    # ------------------------------------------------------------------
    def demo_scenario(
        self,
        scenario: str,
        station_id: str = "all",
        step_mm: float | None = None,
        rate_mm_s: float | None = None,
        accel_mm_s2: float | None = None,
        direction_deg: float | None = None,
    ) -> dict[str, Any]:
        if self.mode != "simulation" or self._simulator is None:
            raise RuntimeError("当前为实时采集模式，演示场景仅在仿真模式下可用")
        with self.lock:
            info = self._simulator.set_scenario(
                scenario, station_id, step_mm, rate_mm_s, accel_mm_s2, direction_deg
            )
            self.storage.add_event(
                "scenario", f"演示场景：{info['scenario_name']} / {station_id}"
            )
            return info

    def serial_ports(self) -> list[dict]:
        return list_serial_ports()

    def series(self, station_id: str, minutes: int = 30) -> dict:
        data = self.storage.series(
            station_id, minutes, since_ts=self.started_at
        )
        data["data_status"] = self.data_policy()
        data["data_valid"] = self.data_policy() != "raw_only"
        return data

    def export_csv(self, minutes: int = 60) -> str:
        return self.storage.export_csv(minutes)

    def stop(self) -> None:
        with self.lock:
            self._stop_sources()
            self._started = False
            self.storage.add_event("app", "系统停止")
            self.storage.close()


def create_engine(cfg: dict[str, Any] | None = None) -> MonitoringEngine:
    return MonitoringEngine(cfg or load_config())
