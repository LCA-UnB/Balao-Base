import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from attitude import (
    PROBE_LENGTH, PROBE_RADIUS, PROBE_RESOLUTION, attitude_from_packet, probe_cylinder, probe_nose, rotate,
    rotation_matrix, tilt_from_vertical,
)


def transpose(matrix):
    return [list(column) for column in zip(*matrix)]


class AttitudeTests(unittest.TestCase):
    def assertVectorAlmostEqual(self, first, second):
        for a, b in zip(first, second):
            self.assertAlmostEqual(a, b, places=9)

    def test_zero_attitude_is_identity(self):
        matrix = rotation_matrix(0, 0, 0)
        for row, expected in zip(matrix, ((1, 0, 0), (0, 1, 0), (0, 0, 1))):
            self.assertVectorAlmostEqual(row, expected)
        self.assertAlmostEqual(tilt_from_vertical(matrix), 0)

    def test_matrix_is_orthonormal(self):
        matrix = rotation_matrix(23, -71, 140)
        product = [[sum(a * b for a, b in zip(row, column)) for column in zip(*matrix)] for row in transpose(matrix)]
        for row, expected in zip(product, ((1, 0, 0), (0, 1, 0), (0, 0, 1))):
            self.assertVectorAlmostEqual(row, expected)

    def test_firmware_formulas_recover_pitch_and_roll(self):
        # O acelerômetro em repouso lê a vertical da referência nos eixos do corpo: R^T · ẑ.
        for pitch, roll in ((0, 30), (25, 0), (-40, 65), (12, -150), (80, 10)):
            ax, ay, az = rotate(transpose(rotation_matrix(pitch, roll, 37)), (0, 0, 1))
            self.assertAlmostEqual(math.degrees(math.atan2(ay, az)), roll, places=9)
            self.assertAlmostEqual(math.degrees(math.atan2(-ax, math.hypot(ay, az))), pitch, places=9)

    def test_yaw_is_clockwise_heading_from_magnetometer(self):
        # Com a sonda nivelada, o firmware calcula yaw = atan2(my, mx) do campo fixo na referência.
        for yaw in (0, 45, 90, -120, 179):
            mx, my, _ = rotate(transpose(rotation_matrix(0, 0, yaw)), (1, 0, 0))
            self.assertAlmostEqual(math.degrees(math.atan2(my, mx)), yaw, places=9)
        front = rotate(rotation_matrix(0, 0, 90), (1, 0, 0))
        self.assertVectorAlmostEqual(front, (0, -1, 0))  # 90° no sentido horário visto de cima

    def test_tilt_combines_pitch_and_roll(self):
        self.assertAlmostEqual(tilt_from_vertical(rotation_matrix(30, 0, 200)), 30)
        self.assertAlmostEqual(tilt_from_vertical(rotation_matrix(0, -45, 0)), 45)
        self.assertAlmostEqual(tilt_from_vertical(rotation_matrix(0, 180, 0)), 180)

    def test_cylinder_lies_along_x_with_nose_in_front(self):
        grids = probe_cylinder(rotation_matrix(0, 0, 0))
        self.assertTrue(all(len(grid) == PROBE_RESOLUTION and all(len(row) == PROBE_RESOLUTION for row in grid)
                            for grid in grids))
        xs, ys, zs = grids
        self.assertAlmostEqual(min(map(min, xs)), -PROBE_LENGTH / 2)
        self.assertAlmostEqual(max(map(max, xs)), PROBE_LENGTH / 2)
        for row_y, row_z in zip(ys, zs):
            for y, z in zip(row_y, row_z):
                self.assertAlmostEqual(math.hypot(y, z), PROBE_RADIUS)
        self.assertVectorAlmostEqual(probe_nose(rotation_matrix(0, 0, 0)), (PROBE_LENGTH / 2, 0, 0))
        # Pitch positivo abaixa o nariz (ver test_firmware_formulas_recover_pitch_and_roll).
        self.assertVectorAlmostEqual(probe_nose(rotation_matrix(90, 0, 0)), (0, 0, -PROBE_LENGTH / 2))
        pitched = probe_cylinder(rotation_matrix(90, 0, 0))
        self.assertAlmostEqual(min(map(min, pitched[2])), -PROBE_LENGTH / 2)

    def test_attitude_from_packet_requires_three_finite_angles(self):
        self.assertEqual(attitude_from_packet({"Pitch": 1.5, "Roll": -2, "Yaw": 30}), (1.5, -2, 30))
        self.assertIsNone(attitude_from_packet({"Pitch": 1.5, "Roll": -2}))
        self.assertIsNone(attitude_from_packet({"Pitch": 1.5, "Roll": None, "Yaw": 3}))
        self.assertIsNone(attitude_from_packet({"Pitch": math.nan, "Roll": 0, "Yaw": 0}))


if __name__ == '__main__':
    unittest.main()
