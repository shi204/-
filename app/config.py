from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
STATIC_DIR = BASE_DIR / "static"

DEFAULT_CONFIG: dict[str, Any] = {
    "app": {
        "host": "127.0.0.1",
        "port": 8000,
        "title": "基于北斗/GNSS的山体桥梁形变智能预警系统",
        "team": "智守山河 · 中国国际大学生创新大赛",
    },
    "mode": "simulation",  # simulation | serial
    "sampling_hz": 1,
    "reference": {
        # 老师现场实测基站坐标：仅用于内部相对位移解算，绝不下发给设备。
        "lat": 31.9831969,
        "lon": 119.6265216,
        "alt": 18.1001593,
        "auto": False,
    },
    "stations": [
        {
            "id": "base",
            "name": "基准站 · 桥台基岩",
            "short": "BASE",
            "role": "base",
            "port": "",
            "baud": 0,
            "enabled": True,
            "color": "#22d3ee",
            "lat": 31.9831969,
            "lon": 119.6265216,
            "alt": 18.1001593,
            # 以下坐标均由“相对基站的 E/N/U（米）”推导，仅用于仿真内部解算。
            "relative_enu_m": [0.0, 0.0, 0.0],
            "scene": {"x": -78, "z": 6, "y": 11},
            "description": "稳定基准点，提供形变解算参考",
        },
        {
            "id": "rover1",
            "name": "监测站 01 · 主跨跨中",
            "short": "R01",
            "role": "rover",
            "port": "",
            "baud": 0,
            "enabled": True,
            "color": "#34d399",
            "lat": 31.9831427881,
            "lon": 119.6273469034,
            "alt": 18.1006386261,
            "relative_enu_m": [78.0, -6.0, 0.0],
            "scene": {"x": 0, "z": 0, "y": 16},
            "description": "桥梁主跨跨中，重点监测竖向挠度",
        },
        {
            "id": "rover2",
            "name": "监测站 02 · 坡体中部",
            "short": "R02",
            "role": "rover",
            "port": "",
            "baud": 0,
            "enabled": True,
            "color": "#a78bfa",
            "lat": 31.9834133288,
            "lon": 119.6279394318,
            "alt": 24.1016109223,
            "relative_enu_m": [134.0, 24.0, 6.0],
            "scene": {"x": 56, "z": 30, "y": 20},
            "description": "山体边坡中部，重点监测水平滑移",
        },
    ],
    "thresholds": {
        # 位移单位 mm，速率单位 mm/min，加速度单位 mm/min²
        "blue": {"d": 12.0, "v": 20.0, "a": 15.0},
        "yellow": {"d": 30.0, "v": 40.0, "a": 30.0},
        "orange": {"d": 60.0, "v": 80.0, "a": 60.0},
        "red": {"d": 120.0, "v": 150.0, "a": 120.0},
        "up_samples": 3,
        "down_samples": 20,
    },
    "processing": {
        "calibration_samples": 30,
        "velocity_window_s": 30,
        "noise_window_s": 90,
        "prediction_window_s": 120,
        "process_noise": 0.008,
        "simulation_process_noise": 0.00005,
        "measurement_noise_m": 0.6,
        "max_gap_s": 5.0,
    },
    "simulation": {
        "noise_mm": 0.6,
        "common_noise_mm": 0.8,
        "scenario": "normal",
        "rate_mm_s": 0.5,
        "accel_mm_s2": 0.005,
        "step_mm": 40.0,
        "direction_deg": 35.0,
    },
    "acquisition": {
        # 真实数据采集为只读；协议经原始日志验证后才能置为 true
        "protocol_verified": False,
        "verified_station_ids": [],
        "raw_log_enabled": True,
    },
    "alerts": {
        "webhook_url": "",
        "sound": True,
        "auto_ack_seconds": 0,
    },
}

_lock = threading.RLock()
_cache: dict[str, Any] | None = None


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_config() -> dict[str, Any]:
    """读取 config.json，缺失字段自动补默认值。"""
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        raw: dict[str, Any] = {}
        if CONFIG_PATH.exists():
            try:
                raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except Exception:
                raw = {}
        cfg = _deep_merge(DEFAULT_CONFIG, raw)
        _cache = cfg
        if not CONFIG_PATH.exists():
            save_config(cfg)
        return cfg


def save_config(cfg: dict[str, Any]) -> None:
    global _cache
    with _lock:
        CONFIG_PATH.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _cache = cfg


def update_config(patch: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        cfg = _deep_merge(load_config(), patch)
        save_config(cfg)
        return cfg


def station_by_id(cfg: dict[str, Any], station_id: str) -> dict[str, Any] | None:
    for station in cfg.get("stations", []):
        if station.get("id") == station_id:
            return station
    return None


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
