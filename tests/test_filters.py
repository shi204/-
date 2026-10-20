import unittest

from app.filters import KalmanCV


class KalmanTest(unittest.TestCase):
    def test_initializes_to_first_fix(self):
        k = KalmanCV(meas_var=1e-5, process_noise=1e-6)
        out = k.update((10.0, 20.0, 30.0), 1000.0)
        self.assertTrue(out.reset)
        self.assertEqual(out.pos, (10.0, 20.0, 30.0))

    def test_static_series_stays_stable(self):
        k = KalmanCV(meas_var=1e-5, process_noise=1e-6)
        k.update((0.0, 0.0, 0.0), 0.0)
        for i in range(1, 200):
            out = k.update((0.0001, -0.0001, 0.00005), float(i))
        self.assertFalse(out.rejected)
        self.assertLess(abs(out.pos[0]), 0.001)
        self.assertLess(abs(out.pos[2]), 0.001)

    def test_single_outlier_rejected(self):
        k = KalmanCV(meas_var=1e-6, process_noise=1e-8, gate_sigma=5.0)
        k.update((0.0, 0.0, 0.0), 0.0)
        for i in range(1, 20):
            k.update((0.0, 0.0, 0.0), float(i))
        out = k.update((1.0, 1.0, 1.0), 20.0)
        self.assertTrue(out.rejected)

    def test_large_gap_resets(self):
        k = KalmanCV(meas_var=1e-5, process_noise=1e-6, max_gap=5.0)
        k.update((0.0, 0.0, 0.0), 0.0)
        out = k.update((5.0, 5.0, 5.0), 100.0)
        self.assertTrue(out.reset)
        self.assertEqual(out.pos, (5.0, 5.0, 5.0))


if __name__ == "__main__":
    unittest.main()
