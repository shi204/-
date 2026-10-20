from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn

from app.config import load_config, save_config
from app.engine import MonitoringEngine
from app.server import create_app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="基于北斗/GNSS的山体桥梁形变智能预警系统"
    )
    parser.add_argument("--mode", choices=["simulation", "serial"], help="运行模式")
    parser.add_argument("--host", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, help="监听端口，默认 8000")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config()
    if args.mode:
        cfg["mode"] = args.mode
        save_config(cfg)
    host = args.host or cfg.get("app", {}).get("host", "127.0.0.1")
    port = args.port or int(cfg.get("app", {}).get("port", 8000))

    engine = MonitoringEngine(cfg)
    app = create_app(engine)

    if not args.no_browser:
        url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
