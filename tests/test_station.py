from collections import deque
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import json

from mission import MissionWriter, export_raw, packet_rows, read_database
from station import StationReceiver
from test_mission import FRAME


class FakeSerial:
    port = 'TEST'
    baudrate = 115200
    def __init__(self, chunks):
        self.chunks = deque(chunks)
        self.cancelled = threading.Event()
        self.closed = False
    @property
    def in_waiting(self):
        return len(self.chunks[0]) if self.chunks else 0
    def read(self, count):
        if self.chunks:
            return self.chunks.popleft()
        self.cancelled.wait(.01)
        return b''
    def cancel_read(self):
        self.cancelled.set()
    def close(self):
        self.closed = True


class StationTests(unittest.TestCase):
    def test_serial_receives_and_preserves_raw_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = MissionWriter.create(directory, 'Serial', sync_interval=.01)
            raw = b'\xff\r\n' + FRAME * 5
            receiver = StationReceiver(FakeSerial([raw[:15], raw[15:]]), writer, lambda:(None,None))
            receiver.start()
            deadline = time.monotonic() + 2
            while receiver.received_packets < 5 and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(receiver.stop())
            self.assertEqual(receiver.received_packets, 5)
            self.assertTrue(writer.close())
            self.assertEqual(len(list(packet_rows(writer.path))), 5)
            output = Path(directory) / 'raw.bin'
            export_raw(writer.path, output)
            self.assertEqual(output.read_bytes(), raw)

    def test_dead_logger_does_not_stop_reception(self):
        class BrokenWriter:
            def submit(self, *args, **kwargs):
                raise OSError('logger failed')
            def elapsed(self):
                return 1
        receiver = StationReceiver(FakeSerial([FRAME] * 10), BrokenWriter(), lambda:(None,None))
        receiver.start()
        deadline = time.monotonic() + 2
        while receiver.received_packets < 10 and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(receiver.received_packets, 10)
        self.assertTrue(receiver.thread.is_alive())
        self.assertEqual(receiver.log_error, 'logger failed')
        self.assertTrue(receiver.stop())

    def test_display_backlog_is_bounded_without_dropping_disk_records(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = MissionWriter.create(directory, 'Backlog', sync_interval=.01)
            receiver = StationReceiver(FakeSerial([FRAME] * 600), writer, lambda:(None,None))
            receiver.start()
            deadline = time.monotonic() + 3
            while receiver.received_packets < 600 and time.monotonic() < deadline:
                time.sleep(.01)
            receiver.stop()
            self.assertGreater(receiver.display_skipped, 0)
            self.assertLessEqual(receiver.messages.qsize(), 512)
            self.assertTrue(writer.close())
            self.assertEqual(len(list(packet_rows(writer.path))), 600)

    def test_message_notices_are_saved_as_events_and_forwarded(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = MissionWriter.create(directory, 'Mensagens', sync_interval=.01)
            data = FRAME + b'[MSG] RECEBIDA Id:B7 De:B Hora:12:34:57 RSSI:-80 SNR:5 Texto:Ola\n[ESTACAO] ID:A N:3\n'
            receiver = StationReceiver(FakeSerial([data[:40], data[40:]]), writer, lambda:(None,None))
            receiver.start()
            items = []
            deadline = time.monotonic() + 2
            while len(items) < 3 and time.monotonic() < deadline:
                while not receiver.messages.empty():
                    items.append(receiver.messages.get_nowait())
                time.sleep(.01)
            self.assertTrue(receiver.stop())
            self.assertEqual([kind for kind, _ in items[:3]], ['packet', 'notice', 'notice'])
            self.assertEqual(items[1][1]['text'], 'Ola')
            self.assertTrue(writer.close())
            with read_database(writer.path) as db:
                events = [json.loads(row[0]) for row in db.execute("SELECT payload FROM records WHERE kind='event'")]
            received = next(e for e in events if e['event'] == 'message_received')
            self.assertEqual((received['id'], received['from'], received['text']), ('B7', 'B', 'Ola'))
            self.assertIn({'event': 'station_identity', 'id': 'A', 'total': 3}, events)

if __name__ == '__main__':
    unittest.main()
