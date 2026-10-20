import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import load_config
from app.engine import MonitoringEngine
from app.run_protocol import make_run_frame, parse_run_frame


def serial_config(verified: bool):
    cfg = load_config()
    cfg["mode"] = "serial"
    cfg["acquisition"]["protocol_verified"] = verified
    for station in cfg["stations"]:
        station["port"] = ""
        station["baud"] = 0
    return cfg


class EngineGatingTest(unittest.TestCase):
    def _engine(self, verified: bool, tmp: str):
        with patch("app.engine.DATA_DIR", Path(tmp)), patch(
            "app.engine.LOG_DIR", Path(tmp) / "logs"
        ):
            engine = MonitoringEngine(serial_config(verified))
            engine.start()
        return engine

    def test_unverified_serial_never_shows_displacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(False, tmp)
            try:
                now = time.time()
                fix = parse_run_frame(
                    make_run_frame(east=12.0, north=5.0, up=1.0, diff_status=4),
                    ts=now,
                )
                engine.ingest("rover1", fix)
                snap = engine.snapshot()
                self.assertEqual(snap["data_policy"], "raw_only")
                self.assertFalse(snap["protocol_verified"])
                rover = next(s for s in snap["stations"] if s["id"] == "rover1")
                self.assertEqual(rover["data_status"], "unverified")
                self.assertIsNone(rover["disp"])
                self.assertEqual(rover["level"], 0)
                self.assertEqual(
                    rover["connection"]["state"], "unconfigured"
                )
                self.assertIn("未配置串口", rover["connection"]["message"])
            finally:
                engine.stop()

    def test_non_rtk_fix_is_low_precision_after_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(True, tmp)
            try:
                now = time.time()
                for i in range(45):
                    fix = parse_run_frame(
                        make_run_frame(
                            east=12.0, north=5.0, up=1.0, diff_status=1
                        ),
                        ts=now + i,
                    )
                    engine.ingest("rover1", fix)
                snap = engine.snapshot()
                rover = next(s for s in snap["stations"] if s["id"] == "rover1")
                self.assertEqual(rover["data_status"], "low_precision")
                self.assertIsNone(rover["disp"])
                self.assertFalse(rover["precision_ok"])
                self.assertEqual(rover["diff_status"], 1)
            finally:
                engine.stop()

    def test_rtk_fixed_displacement_after_calibration(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(True, tmp)
            try:
                now = time.time()
                for i in range(45):
                    fix = parse_run_frame(
                        make_run_frame(
                            east=12.0, north=5.0, up=1.0, diff_status=4
                        ),
                        ts=now + i,
                    )
                    engine.ingest("rover1", fix)
                snap = engine.snapshot()
                rover = next(s for s in snap["stations"] if s["id"] == "rover1")
                self.assertEqual(rover["data_status"], "verified")
                self.assertTrue(rover["data_valid"])
                self.assertIsNotNone(rover["disp"])
                self.assertLess(abs(rover["disp"]["total"]), 1.0)
                self.assertEqual(rover["protocol"], "run")
                self.assertTrue(rover["precision_ok"])
            finally:
                engine.stop()


if __name__ == "__main__":
    unittest.main()
