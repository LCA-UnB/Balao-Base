"""Geometria da sonda girada por Pitch/Roll/Yaw; sem Tk nem matplotlib.

A convenção segue o bordo da linha B (filtro complementar do GY-86), nos eixos do IMU:
Z aponta para cima com a placa em repouso (o acelerômetro lê +g em Z),
roll = atan2(ay, az) gira em torno de X, pitch = atan2(-ax, √(ay² + az²)) gira em torno de Y
e o yaw, rumo magnético, cresce no sentido horário visto de cima. A rotação corpo → referência é
R = Rz(-yaw) · Ry(pitch) · Rx(roll). Os três ângulos são tarados no boot, então a referência
é a atitude da sonda ao ligar, e não o norte verdadeiro.
"""
import math

# Corpo da sonda como na interface antiga (trackerV1.2): cilindro deitado ao longo de X, nariz em +X.
PROBE_LENGTH = 3.6
PROBE_RADIUS = 0.5
PROBE_RESOLUTION = 16


def rotation_matrix(pitch, roll, yaw):
    """Matriz 3×3 (lista de linhas) que leva vetores do corpo para a referência; ângulos em graus."""
    theta, phi, psi = math.radians(pitch), math.radians(roll), math.radians(-yaw)
    ct, st, cf, sf, cp, sp = (math.cos(theta), math.sin(theta), math.cos(phi),
                              math.sin(phi), math.cos(psi), math.sin(psi))
    return [
        [cp * ct, cp * st * sf - sp * cf, cp * st * cf + sp * sf],
        [sp * ct, sp * st * sf + cp * cf, sp * st * cf - cp * sf],
        [-st, ct * sf, ct * cf],
    ]


def rotate(matrix, point):
    return tuple(sum(row[index] * point[index] for index in range(3)) for row in matrix)


def probe_cylinder(matrix, length=PROBE_LENGTH, radius=PROBE_RADIUS, resolution=PROBE_RESOLUTION):
    """Grades X, Y, Z (listas de linhas) da superfície do cilindro girado, prontas para plot_surface."""
    grids = ([], [], [])
    for step in range(resolution):
        x = -length / 2 + length * step / (resolution - 1)
        ring = [rotate(matrix, (x, radius * math.cos(angle), radius * math.sin(angle)))
                for angle in (2 * math.pi * index / (resolution - 1) for index in range(resolution))]
        for axis, grid in enumerate(grids):
            grid.append([point[axis] for point in ring])
    return grids


def probe_nose(matrix, length=PROBE_LENGTH):
    """Ponta do nariz (+X) girada."""
    return rotate(matrix, (length / 2, 0, 0))


def attitude_from_packet(fields):
    """(pitch, roll, yaw) em graus, ou None se algum dos três faltar no pacote."""
    angles = tuple(fields.get(key) for key in ("Pitch", "Roll", "Yaw"))
    if any(value is None or not math.isfinite(value) for value in angles):
        return None
    return angles


def tilt_from_vertical(matrix):
    """Ângulo, em graus, entre o eixo Z da sonda e a vertical da referência."""
    return math.degrees(math.acos(max(-1.0, min(1.0, matrix[2][2]))))
