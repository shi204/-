"""串口探测工具：接线后先跑这个，确认端口、波特率和原始输出。

支持 NMEA 0183 与 BT800S 厂商 $RUN 文本流；逐字节分帧，不依赖换行。
默认只读；只有显式加 --send-run 才会发送一次 ``$RUN,`` 启动数据流。

用法：
    python tools/probe.py --list
    python tools/probe.py --auto
    python tools/probe.py --port COM3
    python tools/probe.py --port COM3 --baud 115200 --seconds 15
    python tools/probe.py --port COM3 --send-run --seconds 20
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.nmea import NmeaParser  # noqa: E402
from app.run_protocol import parse_run_frame  # noqa: E402
from app.serial_reader import BAUD_CANDIDATES, detect_baud, list_serial_ports  # noqa: E402

try:
    import serial
except Exception:
    serial = None


def cmd_list() -> None:
    ports = list_serial_ports()
    if not ports:
        print("未发现串口设备。请检查 USB 线/驱动（CH340、CP210x、FTDI）。")
        return
    print(f"发现 {len(ports)} 个串口：\n")
    for p in ports:
        print(f"  {p['port']:<8} {p['description']}")
        print(f"           {p['hwid']}")
    print("\n提示：拔插设备观察哪个 COM 口消失/出现，即可确认接收机端口。")


def sniff(port: str, baud: int | None, seconds: float, log_path: str | None = None,
          send_run: bool = False) -> None:
    if serial is None:
        print("未安装 pyserial，请先执行：pip install pyserial")
        return
    if baud is None or baud <= 0:
        print(f"[{port}] 正在扫描波特率（{BAUD_CANDIDATES}）…")
        baud = detect_baud(port, send_run=send_run)
        if baud is None:
            print(
                f"[{port}] 未识别到 NMEA/$RUN 数据。请确认接收机已上电、端口未被占用；"
                "若设备需要先启动数据流，请加 --send-run。"
            )
            return
        print(f"[{port}] 识别到波特率：{baud}")
    parser = NmeaParser()
    log_fh = open(log_path, "ab") if log_path else None
    if log_fh:
        print(f"[{port}] 原始数据将保存到：{log_path}")
    print(f"[{port}] @ {baud} 开始采集 {seconds:.0f} 秒，Ctrl+C 结束\n")
    valid = 0
    run_valid = 0
    run_bad = 0
    types: dict[str, int] = {}
    last_print = 0.0
    buffer = ""

    def handle(segment: str, now: float) -> None:
        nonlocal valid, run_valid, run_bad, last_print
        segment = segment.strip()
        if not segment:
            return
        if segment.startswith("RUN,"):
            types["RUN"] = types.get("RUN", 0) + 1
            run_fix = parse_run_frame("$" + segment)
            if run_fix is None:
                run_bad += 1
                return
            run_valid += 1
            valid += 1
            if now - last_print >= 1.0:
                last_print = now
                print(
                    f"\r$RUN  {run_fix.fix_label:<12}  卫星 {run_fix.nsat:>2}  "
                    f"基线 {run_fix.baseline_m:.3f}m  "
                    f"E/N/U {run_fix.rel_enu[0]:.3f}/"
                    f"{run_fix.rel_enu[1]:.3f}/{run_fix.rel_enu[2]:.3f}m",
                    end="",
                    flush=True,
                )
            return
        if len(segment) >= 6 and segment[2:5] in ("GGA", "RMC", "GST", "GSA", "GSV"):
            head = segment[1:6]
            types[head] = types.get(head, 0) + 1
            fix = parser.feed("$" + segment)
            if fix is not None:
                valid += 1
                if now - last_print >= 1.0:
                    last_print = now
                    print(
                        f"\r{fix.utc}  {fix.fix_label:<8}  卫星 {fix.nsat:>2}  "
                        f"HDOP {fix.hdop:>4.2f}",
                        end="",
                        flush=True,
                    )

    try:
        with serial.Serial(port, baud, timeout=0.3) as ser:
            ser.reset_input_buffer()
            if send_run:
                # 只启动连续输出；绝不发送坐标、电台或波特率配置命令。
                ser.write(b"$RUN,")
                ser.flush()
                print("已发送 $RUN,（仅启动数据流；未修改任何设备参数）")
            deadline = time.time() + seconds
            while time.time() < deadline:
                raw = ser.read(4096)
                if not raw:
                    continue
                if log_fh:
                    log_fh.write(raw)
                    log_fh.flush()
                text = raw.decode("ascii", "ignore")
                buffer += text
                if len(buffer) > 65536:
                    buffer = buffer[-4096:]
                parts = buffer.split("$")
                buffer = parts.pop() if parts else ""
                for part in parts:
                    handle(part, time.time())
            if buffer:
                handle(buffer, time.time())
    except KeyboardInterrupt:
        print("\n用户中断")
    except Exception as exc:
        print(f"\n串口错误：{exc}")
        return
    finally:
        if log_fh:
            log_fh.close()
    print("\n\n统计：")
    print(f"  有效定位帧：{valid}（$RUN 有效 {run_valid}，无效 {run_bad}）")
    print(f"  NMEA 语句数：{parser.sentences}，校验错误：{parser.bad_checksum}")
    for key, count in sorted(types.items(), key=lambda kv: -kv[1]):
        print(f"  ${key}  {count} 条")


def cmd_auto(seconds: float) -> None:
    ports = list_serial_ports()
    if not ports:
        print("未发现串口设备。")
        return
    for p in ports:
        print(f"\n=== {p['port']}  {p['description']} ===")
        baud = detect_baud(p["port"], seconds=1.8)
        if baud:
            print(f"识别到 NMEA/$RUN @ {baud}，继续采集 3 秒…")
            sniff(p["port"], baud, min(seconds, 3.0))
        else:
            print("未识别到 NMEA/$RUN（可能是蓝牙串口、非 GNSS 设备或被占用）")


def main() -> None:
    parser = argparse.ArgumentParser(description="GNSS 接收机串口探测")
    parser.add_argument("--list", action="store_true", help="列出串口")
    parser.add_argument("--auto", action="store_true", help="自动扫描所有串口")
    parser.add_argument("--port", help="串口名，如 COM3")
    parser.add_argument("--baud", type=int, default=0, help="波特率，0 表示自动识别")
    parser.add_argument("--seconds", type=float, default=8.0, help="采集秒数")
    parser.add_argument("--log", help="同时保存原始数据到指定文件（只读采集）")
    parser.add_argument(
        "--send-run", action="store_true",
        help="发送一次 $RUN, 启动数据流（不修改设备坐标/参数）",
    )
    args = parser.parse_args()

    if args.list:
        cmd_list()
    elif args.auto:
        cmd_auto(args.seconds)
    elif args.port:
        sniff(args.port, args.baud, args.seconds, args.log, args.send_run)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
