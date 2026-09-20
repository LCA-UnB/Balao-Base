from collections import deque
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from mission import MissionWriter, export_raw, packet_rows
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

if __name__ == '__main__':
    unittest.main()
