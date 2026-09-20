"""Geometria de apontamento no referencial leste/norte/cima do tracker.

Terra esférica de raio médio; altitudes em metros sobre o nível do mar.
Sem correção de refração atmosférica ou declinação magnética.
"""

from dataclasses import dataclass
import math


EARTH_RADIUS_M = 6_371_000.0


@dataclass(frozen=True)
class Position:
    latitude: float
    longitude: float
    altitude: float

    def __post_init__(self):
        if not all(math.isfinite(value) for value in (
            self.latitude, self.longitude, self.altitude
        )):
            raise ValueError("Use apenas coordenadas e altitude numéricas finitas.")
        if not -90 <= self.latitude <= 90:
            raise ValueError("A latitude deve estar entre -90 e 90 graus.")
        if not -180 <= self.longitude <= 180:
            raise ValueError("A longitude deve estar entre -180 e 180 graus.")
        if self.altitude <= -EARTH_RADIUS_M:
            raise ValueError("Altitude fora do modelo terrestre.")


@dataclass(frozen=True)
class Pointing:
    east: float
    north: float
    up: float
    horizontal: float
    distance: float
    surface_distance: float
    altitude_difference: float
    azimuth: float | None
    elevation: float | None


def calculate_pointing(tracker: Position, sonde: Position) -> Pointing:
    """Projeta o vetor tracker→sonda no plano tangente ao tracker.

    h² = leste² + norte²; d² = h² + cima². A componente cima inclui
    a curvatura, portanto não é simplesmente a diferença de altitudes.
    Azimute: 0° norte verdadeiro, 90° leste. Elevação: 0° horizonte local.
    """
    lat1, lat2 = map(math.radians, (tracker.latitude, sonde.latitude))
    delta_lat = lat2 - lat1
    delta_lon = math.radians(sonde.longitude - tracker.longitude)
    half_chord = min(1.0, max(0.0,
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    ))
    angle = 2 * math.atan2(math.sqrt(half_chord), math.sqrt(1 - half_chord))
    radius = EARTH_RADIUS_M + sonde.altitude
    east = radius * math.cos(lat2) * math.sin(delta_lon)
    north = radius * (
        math.cos(lat1) * math.sin(lat2)
        - math.sin(lat1) * math.cos(lat2) * math.cos(delta_lon)
    )
    # Esta forma evita subtrair dois raios quase iguais em distâncias curtas.
    up = sonde.altitude - tracker.altitude - 2 * radius * half_chord
    horizontal = math.hypot(east, north)
    distance = math.hypot(horizontal, up)
    azimuth = math.degrees(math.atan2(east, north)) % 360 if horizontal > 1e-6 else None
    elevation = math.degrees(math.atan2(up, horizontal)) if distance > 1e-6 else None
    return Pointing(
        east, north, up, horizontal, distance, EARTH_RADIUS_M * angle,
        sonde.altitude - tracker.altitude, azimuth, elevation,
    )


def position_from_packet(packet: dict) -> Position:
    """Exige posição e fix 3D no mesmo pacote, sem completar com dados antigos."""
    try:
        fix = float(packet["Fix"])
        coordinates = [float(packet[key]) for key in ("Lat", "Lon", "Alt")]
    except (KeyError, TypeError) as error:
        raise ValueError("Pacote GPS incompleto; aguardando Lat, Lon, Alt e Fix.") from error
    except ValueError as error:
        raise ValueError("Pacote GPS inválido; aguardando posição numérica válida.") from error
    if fix != 3:
        raise ValueError("Aguardando GPS com fix 3D (Fix:3).")
    return Position(*coordinates)


def shortest_rotation(current: float, target: float) -> float:
    """Rotação assinada em graus: positiva à direita, negativa à esquerda."""
    return (target - current + 180) % 360 - 180
