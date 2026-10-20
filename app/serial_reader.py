"""串口 NMEA 采集：自动波特率、自动重连、原始数据落盘。"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

try:
    import serial
    from serial.tools import list_ports
except Exception:  # pragma: no cover - pyserial 未安装时给出可读提示
    serial = None
    list_ports = None

from .nmea import NmeaParser
from .run_protocol import RunParser

BAUD_CANDIDATES = [9600, 115200, 38400, 57600, 230400, 460800, 4800, 921600]


def serial_available() -> bool:
    return serial is not None


def list_serial_ports() -> list[dict[str, Any]]:
    if list_ports is None:
        return []
    result = []
    for info in list_ports.comports():
        result.append(
            {
                "port": info.device,
                "description": info.description or "",
                "hwid": info.hwid or "",
                "vid": info.vid,
                "pid": info.pid,
                "manufacturer": getattr(info, "manufacturer", "") or "",
                "product": getattr(info, "product", "") or "",
            }
        )
    result.sort(key=lambda p: p["port"])
    return result


def detect_baud(
    port: str, seconds: float = 2.5, send_run: bool = False
) -> int | None:
    """扫描常见波特率，识别到 NMEA 或厂商 $RUN 数据即返回。

    ``send_run`` 仅由操作员显式开启；它只发送启动数据流的 ``$RUN,``，
    不修改坐标、电台、波特率等任何设备参数。
    """
    if serial is None:
        return None
    for baud in BAUD_CANDIDATES:
        try:
            with serial.Serial(port, baud, timeout=0.2) as ser:
                if send_run:
                    ser.write(b"$RUN,")
                    ser.flush()
                deadline = time.time() + seconds
                buf = b""
                while time.time() < deadline:
                    chunk = ser.read(512)
                    if chunk:
                        buf += chunk
                        if b"$" in buf and (
                            b"GGA," in buf or b"RMC," in buf or b"$RUN," in buf
                        ):
                            return baud
                        if len(buf) > 8192:
                            buf = buf[-4096:]
        except Exception:
            continue
    return None


class SerialReader:
    def __init__(
        self,
        station: dict[str, Any],
        on_fix: Callable[[str, Any], None],
        on_status: Callable[[str, dict], None],
        log_dir: Path,
    ) -> None:
        self.station = station
        self.station_id = station["id"]
        self.port = str(station.get("port") or "").strip()
        self.baud = int(station.get("baud") or 0)
        self.on_fix = on_fix
        self.on_status = on_status
        self.log_dir = log_dir
        self.running = False
        self.thread: threading.Thread | None = None
        self.parser = NmeaParser()
        self.run_parser = RunParser()
        self._buffer = ""
        self._protocol = "unknown"
        self._log_fh = None
        self._log_path: str | None = None
        self._log_bytes = 0
        self._status: dict[str, Any] = {
            "state": "idle",
            "message": "等待启动",
            "port": self.port,
            "baud": self.baud,
            "protocol": "unknown",
            "sentences": 0,
            "bad_checksum": 0,
            "run_frames": 0,
            "run_bad_frames": 0,
            "last_line_ts": 0.0,
        }

    def _set_status(self, state: str, message: str, **extra: Any) -> None:
        self._status.update(state=state, message=message, port=self.port, baud=self.baud)
        self._status.update(extra)
        self._status["sentences"] = self.parser.sentences
        self._status["bad_checksum"] = self.parser.bad_checksum
        self._status["protocol"] = self._protocol
        self._status["run_frames"] = self.run_parser.stats.frames
        self._status["run_bad_frames"] = self.run_parser.stats.bad_frames
        if self._log_path:
            self._status["log_file"] = self._log_path
        try:
            self.on_status(self.station_id, dict(self._status))
        except Exception:
            pass

    def _open_log(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / f"raw_{self.station_id}.log"
        if path.exists() and path.stat().st_size > 5 * 1024 * 1024:
            backup = path.with_suffix(".log.1")
            try:
                if backup.exists():
                    backup.unlink()
                path.rename(backup)
            except OSError:
                pass
        self._log_fh = open(path, "ab", buffering=0)
        self._log_path = str(path)
        self._status["log_file"] = self._log_path

    def _log(self, raw: bytes) -> None:
        if self._log_fh is None:
            return
        try:
            prefix = time.strftime("[%Y-%m-%d %H:%M:%S] ").encode("ascii")
            self._log_fh.write(prefix + raw)
        except OSError:
            pass

    def _handle_text(self, text: str) -> None:
        """按 $ 分帧，兼容 NMEA 与厂商 $RUN 无换行文本流。"""
        buf = self._buffer + text
        parts = buf.split("$")
        self._buffer = parts.pop() if parts else ""
        if len(self._buffer) > 65536:
            self._buffer = ""
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if part.startswith("RUN,"):
                self._protocol = "run"
                segment = "$" + part
                self._status["last_line_ts"] = time.time()
                fixes = self.run_parser.feed_text(segment)
                if not fixes:
                    continue
                fix = fixes[-1]
                if fix.valid:
                    self._set_status(
                        "receiving",
                        f"{fix.fix_label} · {fix.nsat} 星",
                        last_line=segment[:160],
                        diff_status=fix.diff_status,
                        precision_ok=fix.precision_ok,
                    )
                    self.on_fix(self.station_id, fix)
                else:
                    self._set_status(
                        "no_fix", "定位无效，等待卫星锁定",
                        last_line=segment[:160],
                        diff_status=fix.diff_status,
                        precision_ok=False,
                    )
                continue
            if len(part) >= 6 and part[2:5] in ("GGA", "RMC", "GST", "GSA", "GSV"):
                if self._protocol == "unknown":
                    self._protocol = "nmea"
                self._status["last_line_ts"] = time.time()
                fix = self.parser.feed("$" + part)
                if fix is not None:
                    if fix.valid:
                        self._set_status(
                            "receiving",
                            f"{fix.fix_label} · {fix.nsat} 星",
                            last_line=("$" + part)[:160],
                            precision_ok=fix.precision_ok,
                        )
                        self.on_fix(self.station_id, fix)
                    else:
                        self._set_status(
                            "no_fix", "定位无效，等待卫星锁定",
                            last_line=("$" + part)[:160],
                            precision_ok=False,
                        )

    def start(self) -> None:
        if self.running:
            return
        if serial is None:
            self._set_status("error", "未安装 pyserial，请执行 pip install pyserial")
            return
        if not self.port:
            self._set_status("error", "未分配串口，请在设置中选择")
            return
        self.running = True
        self.thread = threading.Thread(
            target=self._run, name=f"serial-{self.station_id}", daemon=True
        )
        self.thread.start()

    def stop(self) -> None:
        self.running = False
        if self.thread is not None:
            self.thread.join(timeout=2.0)
            self.thread = None

    def _run(self) -> None:
        self._open_log()
        while self.running:
            try:
                self._set_status("connecting", "正在打开串口…")
                baud = self.baud
                if baud <= 0:
                    self._set_status("scanning", "自动扫描波特率…")
                    baud = detect_baud(self.port)
                    if baud is None:
                        self._set_status(
                            "error",
                            "未识别到 NMEA/$RUN 数据，请检查接线/波特率；"
                            "若设备需先发送 $RUN,，请使用 tools/probe.py --send-run",
                        )
                        time.sleep(3.0)
                        continue
                    self.baud = baud
                ser = serial.Serial(self.port, baud, timeout=0.5)
                try:
                    ser.reset_input_buffer()
                except Exception:
                    pass
                self._set_status("connected", f"已连接 {self.port} @ {baud}")
                last_data = time.time()
                while self.running:
                    try:
                        raw = ser.read(4096)
                    except serial.SerialException:
                        break
                    if not raw:
                        if time.time() - last_data > 12.0:
                            self._set_status(
                                "waiting", "串口已打开，但 12 秒未收到数据"
                            )
                        continue
                    last_data = time.time()
                    self._log(raw)
                    self._handle_text(raw.decode("ascii", "ignore"))
                try:
                    ser.close()
                except Exception:
                    pass
            except Exception as exc:
                self._set_status("error", f"串口异常：{exc}")
                time.sleep(2.0)
        self._set_status("stopped", "采集已停止")
        if self._log_fh is not None:
            try:
                self._log_fh.close()
            except Exception:
                pass
            self._log_fh = None


def looks_like_gnss(port_info: dict[str, Any]) -> int:
    """给串口打分：数值越小越可能是 GNSS USB 串口。"""
    text = " ".join(
        str(port_info.get(k, "")) for k in ("description", "product", "manufacturer", "hwid")
    ).lower()
    if any(k in text for k in ("gps", "gnss", "u-blox", "ublox", "beidou", "北斗", "rtk")):
        return 0
    if any(k in text for k in ("ch340", "ch341", "cp210", "ftdi", "usb-serial", "usb serial")):
        return 1
    if "bluetooth" in text or "蓝牙" in text:
        return 4
    if "com" in str(port_info.get("port", "")).lower():
        return 2
    return 3
