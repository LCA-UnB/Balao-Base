"""Serial framing for the existing LoRa A/B firmwares; no Tk or disk I/O."""
from dataclasses import asdict
import math
import re
import unicodedata
import uuid

from antenna import Position, calculate_pointing, position_from_packet

NUMERIC_FIELDS = ("Lat", "Lon", "Alt", "AltB", "Sat", "Fix", "T", "P", "U", "Pitch", "Roll", "Yaw",
                  "AX", "AY", "AZ", "GX", "GY", "GZ", "MX", "MY", "MZ", "AXavg", "AYavg", "AZavg", "Bat", "Ack", "RSSI", "SNR")
INTEGER_FIELDS = {"Sat", "Fix", "Ack"}
BASE_FIELDS = {"Lat", "Lon", "Alt", "AltB", "Sat", "Fix", "T", "P", "Time", "Pitch", "Roll", "Yaw"}
A_FIELDS = BASE_FIELDS | {"U", "AX", "AY", "AZ", "GX", "GY", "GZ", "MX", "MY", "MZ"}
B_FIELDS = BASE_FIELDS | {"Ack"}

# Message repeater (line B ground firmware). The text travels inside a
# line-framed LoRa packet, so it is limited to printable ASCII on one line.
MESSAGE_LIMIT = 100
MESSAGE_STATUS = {"ENFILEIRADA": "queued", "ENVIADA": "sent", "ENTREGUE": "delivered",
                  "FALHOU": "failed", "RECEBIDA": "received", "RECUSADA": "refused"}
MESSAGE_KEYS = {"Id": "id", "De": "from", "Hora": "time", "RSSI": "rssi", "SNR": "snr",
                "Tentativa": "attempts", "Tentativas": "attempts", "Motivo": "reason"}
STATION_TOTALS = (1, 2, 3, 5, 6)


def sanitize_message(text, limit=MESSAGE_LIMIT):
    """Remove accents and control characters; the firmware accepts ASCII only."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = " ".join("".join(c if c.isprintable() else " " for c in text).split())
    return text[:limit]


def parse_notice(line):
    """Parse a ``[MSG]`` or ``[ESTACAO]`` line printed by the ground firmware."""
    station = re.fullmatch(r"\[ESTACAO\]\s+ID:([A-Z])\s+N:(\d+)", line)
    if station:
        return {"kind": "station", "id": station.group(1), "total": int(station.group(2))}
    match = re.fullmatch(r"\[MSG\]\s+([A-Z]+)(.*)", line)
    if not match or match.group(1) not in MESSAGE_STATUS:
        return None
    head, has_text, text = match.group(2).partition("Texto:")
    notice = {"kind": "message", "status": MESSAGE_STATUS[match.group(1)]}
    for key, value in re.findall(r"(\w+):(\S+)", head):
        if key in MESSAGE_KEYS:
            notice[MESSAGE_KEYS[key]] = int(value) if key in {"RSSI", "SNR", "Tentativa", "Tentativas"} and re.fullmatch(r"-?\d+", value) else value
    if has_text:
        notice["text"] = text
    return notice


def typed_packet(raw, *, callsign, source_frame, source, terminated, reason):
    fields, issues = {}, []
    for key, value in raw.items():
        if key not in NUMERIC_FIELDS and key != "Time":
            continue
        if key == "Time":
            if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d", value):
                fields[key] = value
            else:
                fields[key] = None
                issues.append("invalid:Time")
            continue
        try:
            number = float(value)
            if not math.isfinite(number) or (key in INTEGER_FIELDS and not number.is_integer()):
                raise ValueError
            fields[key] = int(number) if key in INTEGER_FIELDS else number
        except (ValueError, OverflowError):
            fields[key] = None
            issues.append(f"invalid:{key}")
    required = A_FIELDS if "MZ" in raw or source == "raw" else B_FIELDS
    # Early B firmware has no battery/averaged acceleration; accept both versions.
    if "Ack" in raw:
        required = B_FIELDS | ({"AXavg", "AYavg", "AZavg", "Bat"} if any(k in raw for k in ("AXavg", "AYavg", "AZavg", "Bat")) else set())
    missing = sorted(required - raw.keys())
    issues.extend(f"missing:{key}" for key in missing)
    if not terminated:
        issues.append(f"incomplete:{reason}")
    try:
        position_from_packet(fields)
        gps_valid = True
    except ValueError:
        gps_valid = False
        issues.append("gps:unavailable")
    return {"fields": fields, "callsign": callsign, "source_frame": source_frame, "source": source,
            "complete": terminated and not missing and not any(i.startswith("invalid:") for i in issues),
            "gps_valid": gps_valid, "issues": issues}


def enrich_packet(packet, tracker=None, orientation=None):
    packet = dict(packet, tracker=tracker, orientation=orientation, pointing=None)
    if tracker and packet["gps_valid"]:
        try:
            packet["pointing"] = asdict(calculate_pointing(Position(**tracker), position_from_packet(packet["fields"])))
        except (ValueError, TypeError):
            packet["issues"] = [*packet["issues"], "tracker:invalid"]
    return packet


class PacketParser:
    def __init__(self):
        self.session_id = uuid.uuid4().hex
        self.frame_number = 0
        self.buffer = bytearray()
        self.fields = {}
        self.link = {}
        self.callsign = None
        self.source = "bare"
        self.raw_seen = False
        self.suppress = False
        self.frame_time = None
        self.notices = []

    def take_notices(self):
        notices, self.notices = self.notices, []
        return notices

    def _finish(self, terminated=False, reason="boundary"):
        if not self.fields:
            return []
        self.frame_number += 1
        result = typed_packet({**self.link, **self.fields}, callsign=self.callsign,
                              source_frame=f"{self.session_id}:{self.frame_number}", source=self.source,
                              terminated=terminated, reason=reason)
        result["frame_received_at"] = self.frame_time
        self.fields = {}
        self.link = {}
        self.frame_time = None
        return [result]

    def feed(self, data, received_at):
        self.buffer.extend(data)
        results = []
        while b"\n" in self.buffer:
            line, _, remainder = self.buffer.partition(b"\n")
            self.buffer = bytearray(remainder)
            results.extend(self._line(line.decode("utf-8", errors="replace").strip(), received_at))
        if len(self.buffer) > 65536:
            results.extend(self._finish(reason="line_too_long"))
            self.buffer.clear()  # exact bytes remain in the raw capture
        return results

    def finish(self, received_at, reason="disconnect"):
        results = []
        if self.buffer:
            results.extend(self._line(self.buffer.decode("utf-8", errors="replace").strip(), received_at))
            self.buffer.clear()
        results.extend(self._finish(reason=reason))
        return results

    def _line(self, line, received_at):
        results = []
        if line.startswith(("[MSG]", "[ESTACAO]")):
            # Status lines never belong to a telemetry frame; the message text
            # may even contain "RSSI:" or field names.
            notice = parse_notice(line)
            if notice:
                self.notices.append(dict(notice, received_at=received_at))
            return results
        if line.startswith("Pacote Recebido!"):
            results.extend(self._finish(reason="next_packet"))
            self.link, self.raw_seen, self.suppress = {}, False, False
            self.callsign = None
        if "RSSI:" in line and "SNR:" in line:
            if not self.suppress:
                for key in ("RSSI", "SNR"):
                    match = re.search(rf"{key}:\s*([-+\d.]+)", line)
                    if match:
                        self.link[key] = match.group(1)
            return results
        if line.startswith("Texto Bruto:"):
            results.extend(self._finish(reason="next_packet"))
            self.callsign = line.split(":", 1)[1].strip() or None
            self.source, self.raw_seen, self.suppress = "raw", True, False
            return results
        header = re.fullmatch(r"-+\s*([A-Za-z0-9]+)\s*-+", line)
        if header:
            results.extend(self._finish(reason="formatted_copy" if self.raw_seen else "next_packet"))
            if self.raw_seen:
                # The A receiver prints a parsed copy of the same RF packet.
                # Skip that entire block, not equal-valued packets from later receptions.
                self.suppress = True
            else:
                self.link = {}
                self.callsign, self.source, self.suppress = header.group(1), "formatted", False
            return results
        if line and (set(line) == {"="} or set(line) == {"-"}):
            results.extend(self._finish(reason="separator"))
            self.raw_seen, self.suppress = False, False
            self.link = {}
            return results
        if re.fullmatch(r"[A-Z0-9]{3,12}", line):
            results.extend(self._finish(reason="next_packet"))
            self.callsign, self.source = line, "bare"
            self.raw_seen, self.suppress, self.link = False, False, {}
            return results
        if self.suppress or ":" not in line:
            return results
        key, value = (part.strip() for part in line.split(":", 1))
        if key not in NUMERIC_FIELDS and key != "Time":
            return results
        if key in self.fields:
            # Repeated field without framing indicates a new/incomplete packet.
            results.extend(self._finish(reason="repeated_field"))
            self.link = {}
        if self.frame_time is None:
            self.frame_time = received_at
        self.fields[key] = value
        if key in {"MZ", "Ack"}:
            results.extend(self._finish(terminated=True))
        return results
