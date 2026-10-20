import unittest

from app.nmea import NmeaParser, make_gga, parse_line


class NmeaTest(unittest.TestCase):
    def test_parse_gga(self):
        fix = parse_line(
            "$GPGGA,092750.000,5321.6802,N,00630.3372,W,1,8,1.03,61.7,M,55.2,M,,*76"
        )
        self.assertIsNotNone(fix)
        self.assertTrue(fix.valid)
        self.assertAlmostEqual(fix.lat, 53.3613367, places=5)
        self.assertAlmostEqual(fix.lon, -6.50562, places=5)
        self.assertEqual(fix.nsat, 8)
        self.assertFalse(fix.precision_ok)

    def test_make_gga_roundtrip(self):
        raw = make_gga(29.563, 106.5516, 260.0, fix=4, nsat=22, hdop=0.7)
        fix = parse_line(raw)
        self.assertIsNotNone(fix)
        self.assertTrue(fix.valid)
        self.assertTrue(fix.precision_ok)
        self.assertAlmostEqual(fix.lat, 29.563, places=6)
        self.assertAlmostEqual(fix.lon, 106.5516, places=6)

    def test_bad_checksum_rejected(self):
        parser = NmeaParser()
        fix = parser.feed(
            "$GNGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*00"
        )
        self.assertIsNone(fix)
        self.assertEqual(parser.bad_checksum, 1)

    def test_invalid_fix_not_valid(self):
        fix = parse_line(make_gga(29.563, 106.5516, 260.0, fix=0))
        self.assertIsNotNone(fix)
        self.assertFalse(fix.valid)
        self.assertFalse(fix.precision_ok)


if __name__ == "__main__":
    unittest.main()
