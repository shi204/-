"""FastAPI 服务：REST API + WebSocket 实时推送 + 静态大屏。"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import STATIC_DIR
from .engine import MonitoringEngine
from .simulator import SCENARIOS


class WSManager:
    def __init__(self) -> None:
        self.connections: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.connections.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.connections.discard(ws)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        if not self.connections:
            return
        text = json.dumps(payload, ensure_ascii=False)
        dead = []
        for ws in list(self.connections):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


class ModeBody(BaseModel):
    mode: str = Field(pattern="^(simulation|serial)$")


class ScenarioBody(BaseModel):
    scenario: str
    station_id: str = "all"
    step_mm: float | None = None
    rate_mm_s: float | None = None
    accel_mm_s2: float | None = None
    direction_deg: float | None = None


class BaselineBody(BaseModel):
    station_id: str | None = None


class ThresholdPatch(BaseModel):
    thresholds: dict[str, Any]


def create_app(engine: MonitoringEngine) -> FastAPI:
    manager = WSManager()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine.start()
        task = asyncio.create_task(_broadcast_loop(engine, manager))
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            engine.stop()

    app = FastAPI(
        title="北斗/GNSS 山体桥梁形变智能预警系统",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/health")
    def health():
        snap = engine.snapshot()
        return {
            "ok": True,
            "mode": snap["mode"],
            "online": snap["system"]["online"],
            "stations": len(snap["stations"]),
            "ts": time.time(),
        }

    @app.get("/api/snapshot")
    def snapshot():
        return engine.snapshot()

    @app.get("/api/series/{station_id}")
    def series(station_id: str, minutes: int = Query(30, ge=1, le=1440)):
        return engine.series(station_id, minutes)

    @app.get("/api/alerts")
    def alerts(limit: int = Query(50, ge=1, le=200)):
        return {"alerts": engine.alerts(limit)}

    @app.post("/api/alerts/{alert_id}/ack")
    def ack_alert(alert_id: int):
        ok = engine.ack_alert(alert_id)
        if not ok:
            raise HTTPException(404, "告警不存在")
        return {"ok": True}

    @app.post("/api/mode")
    def set_mode(body: ModeBody):
        try:
            return engine.set_mode(body.mode)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/serial/ports")
    def serial_ports():
        # 只读诊断接口：仅列出系统串口，不修改任何设备参数
        return {"ports": engine.serial_ports()}

    @app.post("/api/demo/scenario")
    def scene(body: ScenarioBody):
        if body.scenario not in SCENARIOS:
            raise HTTPException(400, f"未知场景，可选：{list(SCENARIOS)}")
        try:
            return engine.demo_scenario(
                body.scenario, body.station_id, body.step_mm,
                body.rate_mm_s, body.accel_mm_s2, body.direction_deg,
            )
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/demo/reset")
    def demo_reset():
        engine.reset_baselines()
        return engine.snapshot()

    @app.post("/api/baseline/reset")
    def baseline_reset(body: BaselineBody | None = None):
        engine.reset_baselines(body.station_id if body else None)
        return engine.snapshot()

    @app.post("/api/calibrate")
    def calibrate():
        try:
            return engine.calibrate()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.put("/api/thresholds")
    def thresholds(body: ThresholdPatch):
        return {"thresholds": engine.update_thresholds(body.thresholds)}

    @app.get("/api/export.csv")
    def export_csv(minutes: int = Query(60, ge=1, le=10080)):
        content = engine.export_csv(minutes)
        fname = f"deform_data_{time.strftime('%Y%m%d_%H%M')}.csv"
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    @app.get("/api/config")
    def config():
        return {
            "thresholds": engine.cfg.get("thresholds"),
            "stations": [
                {
                    "id": s.get("id"),
                    "name": s.get("name"),
                    "port": s.get("port"),
                    "baud": s.get("baud"),
                    "enabled": s.get("enabled", True),
                }
                for s in engine.cfg.get("stations", [])
            ],
            "mode": engine.mode,
            # 不下发基准坐标：前端只展示相对位移，不展示经纬度。
        }

    # ------------------------------------------------------------------
    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await manager.connect(ws)
        try:
            while True:
                # 客户端保活；同时用于及时发现断开
                await ws.receive_text()
        except WebSocketDisconnect:
            manager.disconnect(ws)
        except Exception:
            manager.disconnect(ws)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


async def _broadcast_loop(engine: MonitoringEngine, manager: WSManager) -> None:
    while True:
        try:
            snapshot = engine.snapshot()
            await manager.broadcast(snapshot)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(1.0)
