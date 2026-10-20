import unittest

from app.features import FeatureExtractor


class FeatureTest(unittest.TestCase):
    def test_calibration_and_zero_displacement(self):
        ex = FeatureExtractor(
            calibration_samples=10,
            velocity_window_s=10,
            noise_window_s=20,
            prediction_window_s=20,
        )
        out = None
        for i in range(80):
            out = ex.add(float(i), (0.0, 0.0, 0.0))
        self.assertFalse(out["calibrating"])
        self.assertLess(out["total_mm"], 1e-6)
        self.assertLess(out["noise_mm"], 1e-6)
        self.assertFalse(out["sigma_anomaly"])

    def test_step_is_detected_as_displacement(self):
        ex = FeatureExtractor(
            calibration_samples=10,
            velocity_window_s=10,
            noise_window_s=20,
            prediction_window_s=20,
        )
        for i in range(10):
            ex.add(float(i), (0.0, 0.0, 0.0))
        out = None
        for i in range(10, 60):
            out = ex.add(float(i), (0.0, 0.0, 0.04))
        self.assertGreater(out["total_mm"], 35.0)
        self.assertLess(out["total_mm"], 45.0)
        self.assertAlmostEqual(out["direction_deg"], 0.0, places=1)

    def test_constant_slope_rate_is_reasonable(self):
        ex = FeatureExtractor(
            calibration_samples=10,
            velocity_window_s=20,
            noise_window_s=30,
            prediction_window_s=30,
        )
        out = None
        for i in range(120):
            # 0.5 mm/s = 30 mm/min
            out = ex.add(float(i), (0.0, 0.0, 0.0005 * i))
        self.assertGreater(out["speed_mm_min"], 20.0)
        self.assertLess(out["speed_mm_min"], 40.0)


if __name__ == "__main__":
    unittest.main()
