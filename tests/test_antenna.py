import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from antenna import (
    EARTH_RADIUS_M, Position, calculate_pointing, position_from_packet, shortest_rotation,
)


class PointingTests(unittest.TestCase):
    def test_pythagorean_3_4_5_triangle(self):
        # Constrói um alvo com 3 km a leste e 4 km acima do plano local.
        radius = math.hypot(EARTH_RADIUS_M + 4000, 3000)
        longitude = math.degrees(math.atan2(3000, EARTH_RADIUS_M + 4000))
        result = calculate_pointing(Position(0, 0, 0), Position(0, longitude, radius - EARTH_RADIUS_M))
        self.assertAlmostEqual(result.east, 3000, places=6)
        self.assertAlmostEqual(result.up, 4000, places=6)
        self.assertAlmostEqual(result.distance, 5000, places=6)
        self.assertAlmostEqual(result.azimuth, 90)
        self.assertAlmostEqual(result.elevation, 53.130102354, places=7)

    def test_cardinal_directions(self):
        for latitude, longitude, azimuth in ((1, 0, 0), (0, 1, 90), (-1, 0, 180), (0, -1, 270)):
            with self.subTest(azimuth=azimuth):
                result = calculate_pointing(Position(0, 0, 1000), Position(latitude, longitude, 1000))
                self.assertAlmostEqual(result.azimuth, azimuth)
                self.assertLess(result.elevation, 0)  # curvatura, mesmo com altitudes iguais

    def test_vertical_and_coincident_points(self):
        origin = Position(-15, -47, 1000)
        for altitude, expected in ((2000, 90), (0, -90), (1000, None)):
            result = calculate_pointing(origin, Position(-15, -47, altitude))
            self.assertAlmostEqual(result.distance, abs(altitude - 1000))
            self.assertIsNone(result.azimuth)
            self.assertEqual(result.elevation, expected)

    def test_dateline_uses_short_path(self):
        result = calculate_pointing(Position(0, 179.9, 0), Position(0, -179.9, 0))
        self.assertAlmostEqual(result.surface_distance, EARTH_RADIUS_M * math.radians(.2), places=6)
        self.assertAlmostEqual(result.azimuth, 90)

    def test_antipodes_and_poles_remain_finite(self):
        for target in (Position(0, 180, 0), Position(90, 0, 0), Position(-90, 0, 0)):
            result = calculate_pointing(Position(0, 0, 0), target)
            self.assertTrue(math.isfinite(result.distance))
            self.assertAlmostEqual(result.distance ** 2, result.horizontal ** 2 + result.up ** 2, delta=.1)

    def test_realistic_flight_against_independent_cartesian_distance(self):
        origin, target = Position(-15.7641474, -47.8691109, 1030), Position(-15.0, -46.8, 30000)

        def cartesian(position):
            lat, lon = map(math.radians, (position.latitude, position.longitude))
            radius = EARTH_RADIUS_M + position.altitude
            return (radius * math.cos(lat) * math.cos(lon),
                    radius * math.cos(lat) * math.sin(lon), radius * math.sin(lat))

        result = calculate_pointing(origin, target)
        self.assertAlmostEqual(result.distance, math.dist(cartesian(origin), cartesian(target)), places=6)
        self.assertLess(result.up, result.altitude_difference)

    def test_invalid_positions(self):
        for values in ((91, 0, 0), (0, -181, 0), (math.nan, 0, 0), (0, math.inf, 0),
                       (0, 0, math.nan), (0, 0, -EARTH_RADIUS_M)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Position(*values)

    def test_requires_complete_3d_fix_in_single_packet(self):
        valid = {"Fix": "3", "Lat": "0", "Lon": "0", "Alt": "0"}
        self.assertEqual(position_from_packet(valid), Position(0, 0, 0))
        for key in valid:
            with self.subTest(missing=key), self.assertRaises(ValueError):
                position_from_packet({k: v for k, v in valid.items() if k != key})
        for fix in ("0", "2", "5", "3.5", "nan", "inválido"):
            with self.subTest(fix=fix), self.assertRaises(ValueError):
                position_from_packet(dict(valid, Fix=fix))
        for value in ("nan", "inf", "--"):
            with self.subTest(altitude=value), self.assertRaises(ValueError):
                position_from_packet(dict(valid, Alt=value))

    def test_shortest_turn_crosses_north(self):
        self.assertEqual(shortest_rotation(350, 10), 20)
        self.assertEqual(shortest_rotation(10, 350), -20)
        self.assertEqual(shortest_rotation(0, 360), 0)


if __name__ == "__main__":
    unittest.main()
