import unittest

from app.alerts import AlertEngine


def feat(d=0.0, speed=0.0, accel=0.0, sigma=False, cusum=False, slope=0.0):
    return {
        "h_mm": d,
        "v_mm": 0.0,
        "speed_mm_min": speed,
        "accel_trend_mm_min2": accel,
        "sigma_anomaly": sigma,
        "cusum_anomaly": cusum,
        "slope_mm_min": slope,
    }


CFG = {
    "blue": {"d": 12, "v": 20, "a": 15},
    "yellow": {"d": 30, "v": 40, "a": 30},
    "orange": {"d": 60, "v": 80, "a": 60},
    "red": {"d": 120, "v": 150, "a": 120},
    "up_samples": 3,
    "down_samples": 20,
}


class AlertTest(unittest.TestCase):
    def test_velocity_is_gated_before_displacement(self):
        engine = AlertEngine(CFG)
        for _ in range(10):
            state = engine.judge(feat(d=2.0, speed=500.0))
        self.assertEqual(state["level"], 0)

    def test_acceleration_does_not_trigger(self):
        engine = AlertEngine(CFG)
        for _ in range(10):
            state = engine.judge(feat(d=0.0, accel=500.0))
        self.assertEqual(state["level"], 0)

    def test_level_escalation_requires_consecutive_samples(self):
        engine = AlertEngine(CFG)
        self.assertEqual(engine.judge(feat(d=13.0))["level"], 0)
        self.assertEqual(engine.judge(feat(d=13.0))["level"], 0)
        self.assertEqual(engine.judge(feat(d=13.0))["level"], 1)
        self.assertEqual(engine.judge(feat(d=31.0))["level"], 1)
        self.assertEqual(engine.judge(feat(d=31.0))["level"], 1)
        self.assertEqual(engine.judge(feat(d=31.0))["level"], 2)

    def test_hysteresis_holds_then_recovers(self):
        engine = AlertEngine(CFG)
        for _ in range(3):
            engine.judge(feat(d=31.0))
        self.assertEqual(engine.level, 2)
        for _ in range(19):
            engine.judge(feat(d=0.0))
        self.assertEqual(engine.level, 2)
        state = engine.judge(feat(d=0.0))
        self.assertEqual(state["level"], 0)

    def test_anomaly_flag_is_reported_without_bad_data(self):
        engine = AlertEngine(CFG)
        state = engine.judge(feat(d=1.0, cusum=True))
        self.assertTrue(state["anomaly"])
        self.assertEqual(state["level"], 0)


if __name__ == "__main__":
    unittest.main()
