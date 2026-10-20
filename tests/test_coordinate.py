import unittest

from app.coordinate import (
    ecef_to_geodetic,
    geodetic_to_ecef,
    geodetic_to_enu,
    enu_to_geodetic,
)


class CoordinateTest(unittest.TestCase):
    def test_ecef_roundtrip(self):
        lat, lon, alt = 29.563, 106.5516, 260.0
        xyz = geodetic_to_ecef(lat, lon, alt)
        lat2, lon2, alt2 = ecef_to_geodetic(*xyz)
        self.assertAlmostEqual(lat, lat2, places=8)
        self.assertAlmostEqual(lon, lon2, places=8)
        self.assertAlmostEqual(alt, alt2, places=3)

    def test_enu_matches_local_offset(self):
        ref = (29.563, 106.5516, 260.0)
        east, north, up = 12.5, -8.25, 3.75
        lat, lon, alt = enu_to_geodetic((east, north, up), *ref)
        e2, n2, u2 = geodetic_to_enu(lat, lon, alt, *ref)
        self.assertAlmostEqual(east, e2, places=3)
        self.assertAlmostEqual(north, n2, places=3)
        self.assertAlmostEqual(up, u2, places=3)


if __name__ == "__main__":
    unittest.main()
