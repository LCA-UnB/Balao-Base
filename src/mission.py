"""Durable, bounded, asynchronous mission storage (stdlib only).

SQLite is the source of truth. Raw serial bytes are BLOBs, never decoded here.
A transaction is committed at most one second after the previous cycle in normal
operation, with synchronous=FULL. Disk/OS stalls can extend that interval.
"""
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import csv
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

SCHEMA_VERSION = 1
BUFFER_BYTES = 64 * 1024 * 1024
BATCH_BYTES = 1024 * 1024


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def database_path(path):
    path = Path(path)
    return path / "mission.sqlite3" if path.is_dir() else path


@contextmanager
def read_database(path):
    connection = sqlite3.connect(database_path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        yield connection
    finally:
        connection.close()


def read_metadata(path):
    with read_database(path) as db:
        values = {key: json.loads(value) for key, value in db.execute("SELECT key, value FROM metadata")}
    if values.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Versão de missão não suportada.")
    return values


class MissionLock:
    """An OS lock, automatically released even when the process crashes."""
    def __init__(self, folder):
        self.file = open(Path(folder) / ".writer.lock", "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0)
                self.file.write(b"0")
                self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.file.close()
            raise ValueError("Esta missão já está aberta para gravação em outro processo.") from error

    def close(self):
        self.file.close()


@dataclass(frozen=True)
class Record:
    kind: str
    received_at: str
    elapsed: float
    payload: bytes
    uid: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def cost(self):
        return len(self.payload) + 256


class MissionWriter:
    def __init__(self, path, *, max_buffer_bytes=BUFFER_BYTES, sync_interval=1.0, retry_interval=1.0):
        self.path = database_path(path).resolve()
        self.metadata = read_metadata(self.path)
        if self.metadata["status"] == "closed":
            raise ValueError("Missão encerrada: disponível apenas para reprodução/exportação.")
        self.lock = MissionLock(self.path.parent)
        try:
            with read_database(self.path) as db:
                last = db.execute("SELECT elapsed, received_at FROM records ORDER BY id DESC LIMIT 1").fetchone()
                self.written_packets = db.execute("SELECT count(*) FROM records WHERE kind='packet'").fetchone()[0]
                previous_losses = [event for (payload,) in db.execute("SELECT payload FROM records WHERE kind='event'")
                                   if (event := json.loads(payload)).get("event") == "buffer_loss"]
            self.dropped_records = sum(item["records"] for item in previous_losses)
            self.dropped_packets = sum(item["packets"] for item in previous_losses)
            self.dropped_raw_bytes = sum(item["raw_bytes"] for item in previous_losses)
            self.loss_start = previous_losses[0]["from_utc"] if previous_losses else None
            self.loss_end = previous_losses[-1]["to_utc"] if previous_losses else None
            self.elapsed_base = 0.0
            if last:
                self.elapsed_base = last[0] + max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(last[1])).total_seconds())
            self.started_monotonic = time.monotonic()
            self.max_buffer_bytes = max_buffer_bytes
            self.sync_interval = sync_interval
            self.retry_interval = retry_interval
            self.condition = threading.Condition()
            self.pending = deque()
            self.pending_bytes = 0
            self.inflight_bytes = 0
            self.loss_pending = None
            self.storage_outage = None
            self.error = None
            self.last_sync = None
            self.written_records = 0
            self.accepting = True
            self.end_requested = False
            self.stop_requested = False
            self.force_stop = False
            self.ready = threading.Event()
            self.startup_error = None
            self.thread = threading.Thread(target=self._run, name="mission-writer", daemon=True)
            self.thread.start()
            if not self.ready.wait(5):
                self.force_stop = True
                raise OSError("A gravação não iniciou em 5 segundos.")
            if self.startup_error:
                raise OSError(self.startup_error)
            self.event("session_started", resumed=bool(last))
        except BaseException:
            self.lock.close()
            raise

    @classmethod
    def create(cls, parent, name, **options):
        mission_id = uuid.uuid4().hex
        folder = Path(parent) / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + mission_id[:8])
        folder.mkdir(parents=True, exist_ok=False)
        path = folder / "mission.sqlite3"
        metadata = {"schema_version": SCHEMA_VERSION, "id": mission_id, "name": name.strip() or "Missão",
                    "created_at": utc_now(), "status": "open", "tracker": None, "orientation": None}
        with sqlite3.connect(path) as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE records (
                    id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('raw','packet','event')),
                    received_at TEXT NOT NULL, elapsed REAL NOT NULL, payload BLOB NOT NULL, record_uid TEXT UNIQUE
                );
                CREATE INDEX records_kind_elapsed ON records(kind, elapsed, id);
            """)
            db.executemany("INSERT INTO metadata VALUES (?, ?)", [(k, json.dumps(v)) for k, v in metadata.items()])
        db.close()
        return cls(path, **options)

    def elapsed(self):
        return self.elapsed_base + time.monotonic() - self.started_monotonic

    def submit(self, kind, payload, *, received_at=None, elapsed=None):
        if kind not in {"raw", "packet", "event"}:
            raise ValueError("Tipo de registro desconhecido")
        payload = bytes(payload) if kind == "raw" else json_bytes(payload)
        with self.condition:
            if not self.accepting or self.end_requested:
                raise RuntimeError("Missão não está aceitando registros.")
            record = Record(kind, received_at or utc_now(), self.elapsed() if elapsed is None else elapsed, payload)
            self.pending.append(record)
            self.pending_bytes += record.cost
            self._trim()
            self.condition.notify_all()

    def event(self, name, **details):
        self.submit("event", {"event": name, **details})

    def _trim(self):
        while self.pending and self.pending_bytes > self.max_buffer_bytes:
            old = self.pending.popleft()
            self.pending_bytes -= old.cost
            self.dropped_records += 1
            self.dropped_packets += old.kind == "packet"
            raw_bytes = len(old.payload) if old.kind == "raw" else 0
            self.dropped_raw_bytes += raw_bytes
            self.loss_start = self.loss_start or old.received_at
            self.loss_end = old.received_at
            if self.loss_pending is None:
                self.loss_pending = {"event": "buffer_loss", "records": 0, "packets": 0, "raw_bytes": 0,
                                     "from_utc": old.received_at, "to_utc": old.received_at, "uid": uuid.uuid4().hex}
            self.loss_pending["records"] += 1
            self.loss_pending["packets"] += old.kind == "packet"
            self.loss_pending["raw_bytes"] += raw_bytes
            self.loss_pending["to_utc"] = old.received_at

    def snapshot(self):
        with self.condition:
            return {"pending": len(self.pending), "buffer_bytes": self.pending_bytes,
                    "inflight_bytes": self.inflight_bytes, "written_packets": self.written_packets,
                    "written_records": self.written_records, "last_sync": self.last_sync, "error": self.error,
                    "dropped_records": self.dropped_records, "dropped_packets": self.dropped_packets,
                    "dropped_raw_bytes": self.dropped_raw_bytes, "loss_start": self.loss_start, "loss_end": self.loss_end,
                    "alive": self.thread.is_alive(), "ending": self.end_requested}

    def flush(self, timeout=3):
        deadline = time.monotonic() + timeout
        with self.condition:
            self.condition.notify_all()
            while self.pending or self.inflight_bytes or self.loss_pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self.thread.is_alive():
                    return False
                self.condition.wait(min(remaining, .1))
        return True

    def close(self, *, end_mission=False, timeout=3):
        # A failed close leaves the writer alive and retrying, retaining RAM data.
        if not self.flush(timeout):
            return False
        if end_mission and not self.end_requested:
            self.event("mission_closed")
            self.end_requested = True
            if not self.flush(timeout):
                return False
        with self.condition:
            self.accepting = False
            self.stop_requested = True
            self.condition.notify_all()
        self.thread.join(timeout)
        return not self.thread.is_alive()

    def abandon(self):
        """Only on an explicitly confirmed application exit with pending data."""
        with self.condition:
            self.accepting = False
            self.force_stop = True
            self.condition.notify_all()
        self.thread.join(2)

    def _commit(self, db, batch, loss):
        with db:
            if self.storage_outage:
                db.execute("INSERT INTO records(kind, received_at, elapsed, payload, record_uid) VALUES('event',?,?,?,?) "
                           "ON CONFLICT(record_uid) DO UPDATE SET payload=excluded.payload",
                           (utc_now(), self.elapsed(), json_bytes({**self.storage_outage, "event": "storage_recovered", "recovered_at": utc_now()}), self.storage_outage["uid"]))
            if loss:
                db.execute("INSERT INTO records(kind, received_at, elapsed, payload, record_uid) VALUES('event',?,?,?,?) "
                           "ON CONFLICT(record_uid) DO UPDATE SET payload=excluded.payload",
                           (utc_now(), self.elapsed(), json_bytes(loss), loss["uid"]))
            db.executemany("INSERT OR IGNORE INTO records(kind, received_at, elapsed, payload, record_uid) VALUES(?,?,?,?,?)",
                           [(r.kind, r.received_at, r.elapsed, r.payload, r.uid) for r in batch])
            for record in batch:
                if record.kind == "event":
                    event = json.loads(record.payload)
                    values = {}
                    if event["event"] == "configuration":
                        values = {key: event[key] for key in ("tracker", "orientation")}
                    elif event["event"] == "mission_closed":
                        values = {"status": "closed", "closed_at": record.received_at}
                    for key, value in values.items():
                        db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, json.dumps(value)))

    def _open_writer_database(self):
        db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=.25)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
        except BaseException:
            db.close()
            raise
        return db

    def _run(self):
        db = None
        try:
            db = self._open_writer_database()
            self.ready.set()
            next_commit = time.monotonic() + self.sync_interval
            while True:
                with self.condition:
                    while not self.force_stop:
                        if self.stop_requested and not self.pending and not self.loss_pending:
                            return
                        delay = next_commit - time.monotonic()
                        if (self.pending or self.loss_pending) and delay <= 0:
                            break
                        self.condition.wait(max(.01, min(delay, .2)) if delay > 0 else .2)
                    if self.force_stop:
                        return
                    batch, size = [], 0
                    while self.pending and (not batch or size + self.pending[0].cost <= BATCH_BYTES):
                        record = self.pending.popleft()
                        batch.append(record)
                        size += record.cost
                        self.pending_bytes -= record.cost
                    loss = self.loss_pending
                    self.loss_pending = None
                    self.inflight_bytes = size or (1 if loss else 0)
                try:
                    if db is None:
                        db = self._open_writer_database()
                    self._commit(db, batch, loss)
                except (OSError, sqlite3.Error) as error:
                    if db is not None:
                        db.close()
                        db = None
                    with self.condition:
                        self.error = f"{type(error).__name__}: {error}"
                        if self.storage_outage is None:
                            self.storage_outage = {"failed_at": utc_now(), "attempts": 0, "uid": uuid.uuid4().hex}
                        self.storage_outage["attempts"] += 1
                        self.storage_outage["error"] = self.error
                        self.pending.extendleft(reversed(batch))
                        self.pending_bytes += size
                        if loss:
                            if self.loss_pending:
                                for key in ("records", "packets", "raw_bytes"):
                                    self.loss_pending[key] += loss[key]
                                self.loss_pending["from_utc"] = loss["from_utc"]
                                self.loss_pending["uid"] = loss["uid"]
                            else:
                                self.loss_pending = loss
                        self._trim()
                        next_commit = time.monotonic() + self.retry_interval
                else:
                    with self.condition:
                        self.written_packets += sum(r.kind == "packet" for r in batch)
                        self.written_records += len(batch)
                        self.last_sync = utc_now()
                        self.error = None
                        self.storage_outage = None
                        # Drain large backlogs promptly in bounded transactions.
                        next_commit = time.monotonic() + (0 if self.pending_bytes >= BATCH_BYTES else self.sync_interval)
                finally:
                    with self.condition:
                        self.inflight_bytes = 0
                        self.condition.notify_all()
        except Exception as error:
            with self.condition:
                self.error = self.startup_error = f"{type(error).__name__}: {error}"
                self.accepting = False
                self.ready.set()
                self.condition.notify_all()
        finally:
            if db is not None:
                db.close()
            self.lock.close()


def packet_rows(path):
    with read_database(path) as db:
        for row in db.execute("SELECT id, received_at, elapsed, payload FROM records WHERE kind='packet' ORDER BY elapsed,id"):
            yield {"id": row[0], "received_at": row[1], "elapsed": row[2], **json.loads(row[3])}


def export_csv(path, destination):
    from telemetry import NUMERIC_FIELDS
    columns = ["record_id", "received_at_utc", "mission_elapsed_s", "gps_time_utc", "callsign", "source_frame",
               "complete", "gps_valid", "issues", *NUMERIC_FIELDS,
               "tracker_latitude", "tracker_longitude", "tracker_altitude_msl_m", "antenna_azimuth_deg", "antenna_elevation_deg",
               "distance_m", "surface_distance_m", "altitude_difference_m", "azimuth_deg", "elevation_deg"]
    count = 0
    with open(destination, "w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        for record in packet_rows(path):
            fields, pointing = record["fields"], record.get("pointing") or {}
            tracker, orientation = record.get("tracker") or {}, record.get("orientation")
            row = {key: fields.get(key) for key in NUMERIC_FIELDS}
            row.update(record_id=record["id"], received_at_utc=record["received_at"], mission_elapsed_s=record["elapsed"],
                       gps_time_utc=fields.get("Time"), callsign=record.get("callsign"), source_frame=record.get("source_frame"),
                       complete=record["complete"], gps_valid=record["gps_valid"], issues="; ".join(record["issues"]),
                       tracker_latitude=tracker.get("latitude"), tracker_longitude=tracker.get("longitude"),
                       tracker_altitude_msl_m=tracker.get("altitude"), antenna_azimuth_deg=orientation[0] if orientation else None,
                       antenna_elevation_deg=orientation[1] if orientation else None,
                       distance_m=pointing.get("distance"), surface_distance_m=pointing.get("surface_distance"),
                       altitude_difference_m=pointing.get("altitude_difference"), azimuth_deg=pointing.get("azimuth"),
                       elevation_deg=pointing.get("elevation"))
            # Radio-supplied text must not become a spreadsheet formula.
            row = {key: "'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value
                   for key, value in row.items()}
            writer.writerow(row)
            count += 1
    return count


KML_NS = "http://www.opengis.net/kml/2.2"
KML_STYLES = (("trajeto", "line", "ff497fff"), ("sonda", "icon", "ff83d638"),
              ("topo", "icon", "ff47b5ff"), ("antena", "icon", "ffd9c827"))


def export_kml(path, destination):
    """Trajetória da sonda em KML (Google Earth), só com pacotes de GPS 3D válido.

    Altitudes em metros sobre o nível do mar (altitudeMode absolute), como o GPS da sonda.
    Devolve a quantidade de pontos exportados; sem nenhum ponto válido não cria arquivo.
    """
    import xml.etree.ElementTree as ET
    points, tracker = [], None
    for record in packet_rows(path):
        tracker = record.get("tracker") or tracker
        if not record["gps_valid"]:
            continue
        fields = record["fields"]
        points.append((float(fields["Lon"]), float(fields["Lat"]), float(fields["Alt"]),
                       record["received_at"], fields.get("Time")))
    if not points:
        raise ValueError("A missão não tem nenhum pacote com GPS 3D válido para exportar.")
    name = read_metadata(path).get("name") or "Missão"

    def text(parent, tag, value):
        ET.SubElement(parent, tag).text = str(value)

    def coordinates(point):
        return f"{point[0]:.7f},{point[1]:.7f},{point[2]:.1f}"

    def placemark(parent, title, style, description, coordinate):
        node = ET.SubElement(parent, "Placemark")
        text(node, "name", title)
        text(node, "description", description)
        text(node, "styleUrl", "#" + style)
        point = ET.SubElement(node, "Point")
        text(point, "altitudeMode", "absolute")
        text(point, "coordinates", coordinate)

    root = ET.Element("kml", xmlns=KML_NS)
    document = ET.SubElement(root, "Document")
    text(document, "name", name)
    text(document, "description", f"{len(points)} pontos com GPS 3D válido. Altitude em metros sobre o nível do mar (MSL).")
    for identifier, kind, color in KML_STYLES:
        style = ET.SubElement(document, "Style", id=identifier)
        if kind == "line":
            line = ET.SubElement(style, "LineStyle")
            text(line, "color", color)
            text(line, "width", 3)
            text(ET.SubElement(style, "PolyStyle"), "color", "40" + color[2:])
        else:
            text(ET.SubElement(style, "IconStyle"), "color", color)
    top = max(points, key=lambda point: point[2])
    for title, style, point in (("Primeiro ponto", "sonda", points[0]), ("Ponto mais alto", "topo", top),
                                ("Último ponto", "sonda", points[-1])):
        placemark(document, title, style,
                  f"Altitude {point[2]:.1f} m · GPS {point[4] or '—'} UTC · recebido {point[3]}", coordinates(point))
    if tracker:
        placemark(document, "Tracker / antena", "antena", f"Altitude {tracker['altitude']:.1f} m MSL",
                  f"{tracker['longitude']:.7f},{tracker['latitude']:.7f},{tracker['altitude']:.1f}")
    route = ET.SubElement(document, "Placemark")
    text(route, "name", "Trajeto da sonda")
    text(route, "styleUrl", "#trajeto")
    line = ET.SubElement(route, "LineString")
    text(line, "extrude", 1)
    text(line, "altitudeMode", "absolute")
    text(line, "coordinates", "\n".join(coordinates(point) for point in points))
    ET.indent(root)
    ET.ElementTree(root).write(destination, encoding="utf-8", xml_declaration=True)
    return len(points)


def export_raw(path, destination):
    count = 0
    with read_database(path) as db, open(destination, "wb") as output:
        for (payload,) in db.execute("SELECT payload FROM records WHERE kind='raw' ORDER BY id"):
            output.write(payload)
            count += len(payload)
    return count
