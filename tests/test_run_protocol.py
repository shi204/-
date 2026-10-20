import unittest

from app.run_protocol import RunParser, make_run_frame, parse_run_frame

VENDOR_SAMPLE = (
    "$RUN,022723,ff,ff,ff,00,00,70915,40353.000,197.80,0.00,0.00,"
    "34.8443296,113.7376900,105.553,15.407,11.373,-13.754,"
    "-0.003,-0.003,-0.001,-0.004,-0.003,-0.003,0.000,0,10,1"
    "$DXY, 0, 0"
)


class RunProtocolTest(unittest.TestCase):
    def test_vendor_sample_fields(self):
        fix = parse_run_frame(VENDOR_SAMPLE)
        self.assertIsNotNone(fix)
        self.assertEqual(fix.protocol, "run")
        self.assertEqual(fix.diff_status, 1)
        self.assertEqual(fix.fix_label, "单点定位")
        self.assertEqual(fix.nsat, 10)
        self.assertEqual(fix.sats_front, 0)
        self.assertEqual(fix.sats_back, 10)
        self.assertAlmostEqual(fix.rel_enu[0], 15.407, places=3)
        self.assertAlmostEqual(fix.rel_enu[1], 11.373, places=3)
        self.assertAlmostEqual(fix.rel_enu[2], -13.754, places=3)
        self.assertTrue(fix.valid)
        self.assertFalse(fix.precision_ok)

    def test_rtk_fixed_is_precision_ok(self):
        fix = parse_run_frame(
            make_run_frame(east=1.2, north=-0.5, up=0.3, diff_status=4)
        )
        self.assertTrue(fix.valid)
        self.assertTrue(fix.precision_ok)
        self.assertEqual(fix.fix_label, "RTK固定解")
        self.assertEqual(fix.rel_enu, (1.2, -0.5, 0.3))

    def test_float_is_not_precision_ok(self):
        fix = parse_run_frame(make_run_frame(diff_status=5))
        self.assertTrue(fix.valid)
        self.assertFalse(fix.precision_ok)

    def test_invalid_status(self):
        self.assertIsNone(parse_run_frame(make_run_frame(diff_status=9)))

    def test_missing_fields_rejected(self):
        self.assertIsNone(parse_run_frame("$RUN,022723,ff,ff"))

    def test_impossible_position_rejected(self):
        self.assertIsNone(parse_run_frame(make_run_frame(lat=999.0)))

    def test_concatenated_frames(self):
        parser = RunParser()
        text = make_run_frame(diff_status=4) + make_run_frame(
            east=1.0, diff_status=4
        )
        fixes = parser.feed_text(text)
        self.assertEqual(len(fixes), 2)
        self.assertEqual(parser.stats.frames, 2)
        self.assertEqual(parser.stats.bad_frames, 0)
        self.assertEqual(fixes[-1].rel_enu[0], 1.0)

    def test_garbage_between_frames_ignored(self):
        parser = RunParser()
        fixes = parser.feed_text("garbage$DXY, 0, 0$RUN,broken$DXY")
        self.assertEqual(len(fixes), 0)
        self.assertEqual(parser.stats.bad_frames, 1)


if __name__ == "__main__":
    unittest.main()
