"""Serial worker: captures exact bytes and never touches a Tk widget."""
from queue import Empty, Full, Queue
import threading
import time

from mission import utc_now
from telemetry import PacketParser, enrich_packet


class StationReceiver:
    def __init__(self, port, writer, settings):
        self.port, self.writer, self.settings = port, writer, settings
        self.stop_event = threading.Event()
        self.messages = Queue(maxsize=512)
        self.received_packets = 0
        self.display_skipped = 0
        self.log_error = None
        self.thread = threading.Thread(target=self._run, name="serial-receiver", daemon=True)

    def start(self):
        self.thread.start()

    def _message(self, kind, value):
        try:
            self.messages.put_nowait((kind, value))
        except Full:
            try:
                self.messages.get_nowait()
            except Empty:
                pass
            self.display_skipped += 1
            self.messages.put_nowait((kind, value))

    def _save(self, kind, payload, **timing):
        try:
            self.writer.submit(kind, payload, **timing)
        except Exception as error:
            # A dead logging worker must never take the serial receiver down.
            self.log_error = str(error)

    def _packets(self, packets, received_at):
        for packet in packets:
            tracker, orientation = self.settings()
            packet = enrich_packet(packet, tracker, orientation)
            elapsed = self.writer.elapsed()
            self._save("packet", packet, received_at=received_at, elapsed=elapsed)
            self.received_packets += 1
            self._message("packet", dict(packet, received_at=received_at, elapsed=elapsed))

    def _notices(self, notices):
        for notice in notices:
            details = {key: value for key, value in notice.items() if key not in {"kind", "status", "received_at"}}
            name = f"message_{notice['status']}" if notice["kind"] == "message" else "station_identity"
            self._save("event", {"event": name, **details}, received_at=notice["received_at"])
            self._message("notice", notice)

    def _run(self):
        parser = PacketParser()
        last_data = time.monotonic()
        try:
            self._save("event", {"event": "serial_connected", "port": self.port.port, "baud": self.port.baudrate})
            while not self.stop_event.is_set():
                data = self.port.read(min(max(self.port.in_waiting, 1), 16384))
                received_at = utc_now()
                if data:
                    self._save("raw", data, received_at=received_at)
                    self._packets(parser.feed(data, received_at), received_at)
                    self._notices(parser.take_notices())
                    last_data = time.monotonic()
                elif time.monotonic() - last_data > 3:
                    self._packets(parser.finish(received_at, reason="timeout"), received_at)
        except Exception as error:
            if not self.stop_event.is_set():
                self._save("event", {"event": "serial_error", "detail": str(error)})
                self._message("error", str(error))
        finally:
            received_at = utc_now()
            self._packets(parser.finish(received_at), received_at)
            self._notices(parser.take_notices())
            self._save("event", {"event": "serial_disconnected"})
            try:
                self.port.close()
            except Exception:
                pass
            self._message("disconnected", None)

    def stop(self):
        self.stop_event.set()
        try:
            self.port.cancel_read()
        except (AttributeError, OSError):
            pass
        self.thread.join(2)
        return not self.thread.is_alive()
