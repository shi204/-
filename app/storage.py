"""SQLite 时序数据与告警事件存储。"""
from __future__ import annotations

import csv
import io
import math
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable


class Storage:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._create()

    def _create(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    station_id TEXT NOT NULL,
                    lat REAL, lon REAL, alt REAL,
                    fix INTEGER, nsat INTEGER, hdop REAL,
                    e REAL, n REAL, u REAL,
                    de REAL, dn REAL, du REAL,
                    d_mm REAL, v_mm_min REAL, a_mm_min2 REAL,
                    level INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_samples_station_ts
                    ON samples(station_id, ts);
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    station_id TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    message TEXT,
                    d_mm REAL, v_mm_min REAL, a_mm_min2 REAL,
                    ack INTEGER DEFAULT 0,
                    ack_ts REAL
                );
                CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    kind TEXT NOT NULL,
                    message TEXT
                );
                """
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    def insert_sample(self, station_id: str, fix: Any, enu: tuple, disp: tuple,
                      features: dict, level: int) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO samples
                   (ts, station_id, lat, lon, alt, fix, nsat, hdop,
                    e, n, u, de, dn, du, d_mm, v_mm_min, a_mm_min2, level)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    features.get("ts", time.time()), station_id,
                    fix.lat, fix.lon, fix.alt, fix.fix, fix.nsat, fix.hdop,
                    enu[0], enu[1], enu[2],
                    disp[0], disp[1], disp[2],
                    features.get("total_mm", 0.0),
                    features.get("speed_mm_min", 0.0),
                    features.get("accel_mm_min2", 0.0),
                    level,
                ),
            )
            self._conn.commit()

    def insert_alert(self, station_id: str, level: int, name: str,
                     message: str, features: dict) -> dict:
        ts = time.time()
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO alerts
                   (ts, station_id, level, name, message, d_mm, v_mm_min, a_mm_min2)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    ts, station_id, level, name, message,
                    features.get("total_mm", 0.0),
                    features.get("speed_mm_min", 0.0),
                    features.get("accel_mm_min2", 0.0),
                ),
            )
            self._conn.commit()
            return {
                "id": cur.lastrowid,
                "ts": ts,
                "station_id": station_id,
                "level": level,
                "name": name,
                "message": message,
                "d_mm": features.get("total_mm", 0.0),
                "v_mm_min": features.get("speed_mm_min", 0.0),
                "a_mm_min2": features.get("accel_mm_min2", 0.0),
                "ack": 0,
            }

    def add_event(self, kind: str, message: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (ts, kind, message) VALUES (?,?,?)",
                (time.time(), kind, message),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    def series(self, station_id: str, minutes: int = 30,
               max_points: int = 900, since_ts: float | None = None) -> dict[str, Any]:
        # 相对位移基准在每次进程启动时重建，跨会话的位移值不可直接比较。
        since = time.time() - max(1, minutes) * 60.0
        if since_ts is not None:
            since = max(since, float(since_ts))
        with self._lock:
            rows = self._conn.execute(
                """SELECT ts, de, dn, du, d_mm, v_mm_min, a_mm_min2, level
                   FROM samples WHERE station_id=? AND ts>=? ORDER BY ts""",
                (station_id, since),
            ).fetchall()
        total = len(rows)
        step = max(1, math.ceil(total / max_points)) if total > max_points else 1
        out: list[dict[str, float]] = []
        for idx, row in enumerate(rows):
            if idx % step != 0 and idx != total - 1:
                continue
            out.append(
                {
                    "t": row["ts"],
                    "e": round(row["de"] * 1000.0, 2),
                    "n": round(row["dn"] * 1000.0, 2),
                    "u": round(row["du"] * 1000.0, 2),
                    "d": round(row["d_mm"], 2),
                    "v": round(row["v_mm_min"], 3),
                    "a": round(row["a_mm_min2"], 3),
                    "level": row["level"],
                }
            )
        return {"station_id": station_id, "count": total, "points": out}

    def recent_alerts(self, limit: int = 30) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT id, ts, station_id, level, name, message,
                          d_mm, v_mm_min, a_mm_min2, ack
                   FROM alerts ORDER BY ts DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def ack_alert(self, alert_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE alerts SET ack=1, ack_ts=? WHERE id=?",
                (time.time(), alert_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def counts(self) -> dict[str, int]:
        with self._lock:
            samples = self._conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
            alerts = self._conn.execute(
                "SELECT COUNT(*) FROM alerts WHERE ack=0"
            ).fetchone()[0]
        return {"samples": int(samples), "unack_alerts": int(alerts)}

    def export_csv(self, minutes: int = 60) -> str:
        since = time.time() - max(1, minutes) * 60.0
        with self._lock:
            rows = self._conn.execute(
                """SELECT ts, station_id, lat, lon, alt, fix, nsat, hdop,
                          e, n, u, de, dn, du, d_mm, v_mm_min, a_mm_min2, level
                   FROM samples WHERE ts>=? ORDER BY ts""",
                (since,),
            ).fetchall()
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            ["时间", "站点", "纬度", "经度", "高程", "解状态", "卫星数", "HDOP",
             "E(m)", "N(m)", "U(m)", "dE(m)", "dN(m)", "dU(m)",
             "累计位移(mm)", "速率(mm/min)", "加速度(mm/min²)", "预警等级"]
        )
        for row in rows:
            writer.writerow(
                [time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["ts"]))]
                + list(row)[1:]
            )
        return "﻿" + buf.getvalue()

    def purge_old(self, days: int = 30) -> None:
        cutoff = time.time() - days * 86400
        with self._lock:
            self._conn.execute("DELETE FROM samples WHERE ts<?", (cutoff,))
            self._conn.execute("DELETE FROM alerts WHERE ts<?", (cutoff,))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
