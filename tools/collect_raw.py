"""只读原始数据采集工具（协议验证前使用）。

原则：
- 只打开串口读取，不向设备写入任何配置命令；
- 原样保存全部字节，供后续协议分析；
- 不解析、不计算、不展示任何位移结果；
- 仅统计可识别的 NMEA/$RUN 帧，无法识别时明确提示，不猜测私有协议。
- 只有显式加 --send-run 才发送一次 ``$RUN,`` 启动数据流，不修改坐标/电台/波特率。

用法：
    python tools/collect_raw.py --port COM4 --baud 0 --label mobile
    python tools/collect_raw.py --port COM4 --baud 115200 --send-run --seconds 60
    python tools/collect_raw.py --list
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.serial_reader import detect_baud, list_serial_ports  # noqa: E402

try:
    import serial
except Exception:
    serial = None

LOG_DIR = ROOT / "logs"


def list_ports() -> None:
    ports = list_serial_ports()
    if not ports:
        print("未发现串口设备。请检查 USB 线/驱动。")
        return
    print(f"发现 {len(ports)} 个串口：")
    for p in ports:
        print(f"  {p['port']:<8} {p['description']}")
        print(f"           {p['hwid']}")


def collect(port: str, baud: int, seconds: float, label: str,
            send_run: bool = False) -> None:
    if serial is None:
        print("未安装 pyserial，请先执行：pip install pyserial")
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = LOG_DIR / f"raw_{port}_{label}_{stamp}.bin"

    if baud <= 0:
        print(f"[{port}] 波特率未指定，正在只读扫描…")
        baud = detect_baud(port, seconds=3.0, send_run=send_run)
        if baud is None:
            print(
                f"[{port}] 未识别到 NMEA/$RUN 数据。仍将按 115200 尝试采集原始字节；"
                "若设备需先启动数据流，请加 --send-run。"
            )
            baud = 115200
        else:
            print(f"[{port}] 识别到协议数据，波特率 {baud}")

    counts: Counter[str] = Counter()
    diff_counts: Counter[int] = Counter()
    total_bytes = 0
    frame_count = 0
    run_frames = 0
    run_valid = 0
    run_bad = 0
    bad_checksum = 0
    buffer = ""
    started = time.time()
    last_report = started

    def handle(segment: str) -> None:
        nonlocal frame_count, run_frames, run_valid, run_bad, bad_checksum
        segment = segment.strip()
        if not segment:
            return
        if segment.startswith("RUN,"):
            frame_count += 1
            run_frames += 1
            counts["RUN"] += 1
            fix = parse_run_frame("$" + segment)
            if fix is None:
                run_bad += 1
                return
            run_valid += 1
            if fix.diff_status is not None:
                diff_counts[fix.diff_status] += 1
            return
        if len(segment) >= 6 and segment[2:5] in ("GGA", "RMC", "GST", "GSA", "GSV"):
            frame_count += 1
            head = segment[1:6]
            counts[head] += 1
            if "*" in segment:
                try:
                    body, cs = segment[1:].split("*", 1)
                    calc = 0
                    for ch in body:
                        calc ^= ord(ch)
                    if int(cs[:2], 16) != calc:
                        bad_checksum += 1
                except Exception:
                    bad_checksum += 1

    print(f"[{port}] @ {baud} 只读采集开始")
    print(f"原始文件：{out}")
    print("不写入设备、不计算位移。Ctrl+C 结束。\n")

    try:
        with serial.Serial(port, baud, timeout=0.5) as ser, open(out, "wb") as fh:
            ser.reset_input_buffer()
            if send_run:
                # 只启动数据流，不修改坐标、电台频率、波特率等任何参数
                ser.write(b"$RUN,")
                ser.flush()
                print("已发送 $RUN,（仅启动数据流；未修改任何设备参数）")
            while True:
                if seconds > 0 and time.time() - started >= seconds:
                    break
                chunk = ser.read(4096)
                if not chunk:
                    continue
                fh.write(chunk)
                fh.flush()
                total_bytes += len(chunk)
                # 按 $ 分帧：兼容有换行 NMEA 与无换行厂商 $RUN 文本流
                buffer += chunk.decode("ascii", "ignore")
                if len(buffer) > 65536:
                    buffer = buffer[-4096:]
                parts = buffer.split("$")
                buffer = parts.pop() if parts else ""
                for part in parts:
                    handle(part)
                now = time.time()
                if now - last_report >= 5.0:
                    last_report = now
                    types = ", ".join(f"{k}:{v}" for k, v in counts.most_common(6)) or "暂无"
                    print(
                        f"\r已采集 {total_bytes / 1024:.1f} KB | "
                        f"帧 {frame_count} | $RUN {run_frames} | 校验错误 {bad_checksum} | {types}",
                        end="",
                        flush=True,
                    )
            if buffer:
                handle(buffer)
    except KeyboardInterrupt:
        print("\n用户中断")
    except Exception as exc:
        print(f"\n串口错误：{exc}")

    elapsed = max(1e-6, time.time() - started)
    print("\n\n采集结束")
    print(f"  时长：{elapsed:.1f} s")
    print(f"  原始字节：{total_bytes}（{total_bytes / 1024:.1f} KB）")
    print(f"  可识别帧：{frame_count}，NMEA 校验错误：{bad_checksum}")
    print(f"  $RUN 帧：{run_frames}，字段有效：{run_valid}，无效：{run_bad}")
    if diff_counts:
        print(f"  差分状态分布：{dict(sorted(diff_counts.items()))}")
        print("    0=无效 1=单点 2=DGPS 3=PPS无效 4=RTK固定 5=RTK浮点 6=估算")
    for key, value in counts.most_common():
        print(f"    ${key}  {value}")
    if out.exists():
        digest = hashlib.sha256(out.read_bytes()).hexdigest()[:16]
        print(f"  文件：{out}")
        print(f"  SHA256(前16位)：{digest}")
    if frame_count == 0:
        print(
            "\n未识别到 NMEA/$RUN 文本帧。该设备可能使用私有/二进制协议，"
            "请勿猜测解析，先获取厂商协议文档。"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="GNSS 只读原始数据采集")
    parser.add_argument("--list", action="store_true", help="列出串口")
    parser.add_argument("--port", help="串口，如 COM4")
    parser.add_argument("--baud", type=int, default=0, help="波特率，0 表示只读扫描")
    parser.add_argument("--seconds", type=float, default=0, help="采集秒数，0 表示直到 Ctrl+C")
    parser.add_argument("--label", default="mobile", help="文件名标签，如 mobile/base")
    parser.add_argument(
        "--send-run", action="store_true",
        help="打开串口后发送一次 $RUN, 启动数据流（不修改任何设备参数）",
    )
    args = parser.parse_args()
    if args.list:
        list_ports()
    elif args.port:
        collect(args.port, args.baud, args.seconds, args.label, args.send_run)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
