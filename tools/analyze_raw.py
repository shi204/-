"""原始日志协议检查工具（只分析，不修改，不推算位移）。

用法：
    python tools/analyze_raw.py logs/raw_COM4_mobile_20261020_120000.bin
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.run_protocol import parse_run_frame  # noqa: E402


def checksum_ok(sentence: str) -> bool:
    if "*" not in sentence:
        return False
    try:
        body, cs = sentence[1:].split("*", 1)
        calc = 0
        for ch in body:
            calc ^= ord(ch)
        return int(cs[:2], 16) == calc
    except Exception:
        return False


def analyze(path: Path) -> dict:
    data = path.read_bytes()
    text = data.decode("ascii", "ignore")
    frames: list[str] = []
    for chunk in re.split(r"[\r\n]+", text):
        for part in chunk.split("$"):
            part = part.strip()
            if part:
                frames.append("$" + part)

    counts: Counter[str] = Counter()
    examples: dict[str, str] = {}
    run_frames = 0
    run_invalid = 0
    diff_counts: Counter[int] = Counter()
    for frame in frames:
        if frame.startswith("$RUN,"):
            head = "RUN"
            run_frames += 1
            fix = parse_run_frame(frame)
            if fix is None:
                run_invalid += 1
            elif fix.diff_status is not None:
                diff_counts[fix.diff_status] += 1
        else:
            head = frame[1:6]
        counts[head] += 1
        if head not in examples:
            examples[head] = frame[:200]

    nmea_frames = [f for f in frames if not f.startswith("$RUN,")]
    gga = [f for f in nmea_frames if f[3:6] == "GGA"]
    valid_checksum = sum(1 for f in nmea_frames if checksum_ok(f))
    printable = sum(1 for b in data if 32 <= b < 127 or b in (9, 10, 13))
    if run_frames:
        note = (
            "检测到厂商 $RUN 文本帧，字段数量与格式已通过严格校验；"
            "但仍需人工核对固件版本、单位及 E/N/U 基准后再启用真实位移。"
        )
    elif nmea_frames:
        note = "检测到 NMEA 0183 文本语句，可进一步核对字段与解状态。"
    else:
        note = "未检测到 NMEA/$RUN 文本语句；禁止猜测私有/二进制协议，请提供厂商协议文档。"
    return {
        "file": str(path),
        "size_bytes": len(data),
        "printable_ratio": round(printable / max(1, len(data)), 3),
        "frames": len(frames),
        "sentence_types": dict(counts.most_common()),
        "examples": examples,
        "gga_lines": len(gga),
        "valid_checksum": valid_checksum,
        "run_frames": run_frames,
        "run_valid": run_frames - run_invalid,
        "run_invalid": run_invalid,
        "run_diff_status": {str(k): v for k, v in sorted(diff_counts.items())},
        "has_nmea": bool(nmea_frames),
        "has_run": bool(run_frames),
        "note": note,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="原始 GNSS 日志协议检查")
    parser.add_argument("file", help="collect_raw.py 生成的 .bin/.log 文件")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()
    path = Path(args.file)
    if not path.exists():
        print(f"文件不存在：{path}")
        return
    result = analyze(path)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"文件：{result['file']}")
    print(f"大小：{result['size_bytes']} 字节")
    print(f"可打印字符比例：{result['printable_ratio']}")
    print(f"总帧数：{result['frames']}")
    print(f"NMEA 校验通过：{result['valid_checksum']}，GGA 帧：{result['gga_lines']}")
    print(f"$RUN 帧：{result['run_frames']}，字段有效：{result['run_valid']}，"
          f"无效：{result['run_invalid']}")
    if result["run_diff_status"]:
        print(f"差分状态分布：{result['run_diff_status']}")
        print("  0=无效 1=单点 2=DGPS 3=PPS无效 4=RTK固定 5=RTK浮点 6=估算")
    print("语句类型：")
    for key, value in result["sentence_types"].items():
        print(f"  ${key}  {value}")
    print("\n示例：")
    for key, value in result["examples"].items():
        print(f"  ${key}  {value}")
    print(f"\n结论：{result['note']}")


if __name__ == "__main__":
    main()
