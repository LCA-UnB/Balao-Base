"""Regression: closing Tk must also end the process with the real map workers."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


@unittest.skipUnless(os.environ.get("DISPLAY") or sys.platform == "win32", "requires a virtual display")
class ApplicationShutdownTests(unittest.TestCase):
    def test_window_close_finishes_map_serial_and_log_workers(self):
        source = Path(__file__).resolve().parents[1] / "src"
        script = textwrap.dedent('''
            import io
            import tempfile
            import threading
            import time
            import tkinter as tk
            from unittest.mock import patch
            from PIL import Image, ImageTk
            from map_cache import RegionDownload, TileCache
            from mission import MissionWriter, packet_rows, utc_now
            from station import StationReceiver
            from tracker import SondeTrackerApp

            buffer = io.BytesIO()
            Image.new("RGB", (256, 256), "navy").save(buffer, format="PNG")
            image_data = buffer.getvalue()
            image_threads = []
            photo_image = ImageTk.PhotoImage
            def make_photo(*args, **kwargs):
                image_threads.append(threading.current_thread())
                return photo_image(*args, **kwargs)
            def fetch(*args):
                time.sleep(.1)
                return image_data

            class Port:
                port, baudrate, in_waiting = "test", 115200, 0
                def __init__(self):
                    self.cancelled = threading.Event()
                    self.closed = False
                    self.sent = False
                def read(self, size):
                    self.cancelled.wait(.05)
                    if not self.sent:
                        self.sent = True
                        return b"PT2UNB\\nLat:-15.8\\nLon:-47.9\\nAlt:1000\\nFix:3\\nAck:0\\n"
                    return b""
                def cancel_read(self): self.cancelled.set()
                def close(self): self.closed = True

            with tempfile.TemporaryDirectory() as directory, \\
                    patch("map_cache.fetch_tile", side_effect=fetch), \\
                    patch("map_cache.ImageTk.PhotoImage", side_effect=make_photo):
                cache = TileCache(directory + "/map.db")
                root = tk.Tk()
                root.withdraw()
                with patch("tracker.open_tile_cache", return_value=cache):
                    app = SondeTrackerApp(root)
                writer = MissionWriter.create(directory, "shutdown", sync_interval=.01)
                app.mission = writer
                port = Port()
                receiver = StationReceiver(port, writer, lambda: (None, None))
                app.receiver = receiver
                app.serial_port = port
                app.is_connected = True
                receiver.start()
                download = RegionDownload(cache, "https://example.test/{z}/{x}/{y}",
                                          [(12, x, 100) for x in range(100)]).start()
                app.region_download = download
                def close_window():
                    assert app.map_widget.loading
                    assert download.running
                    assert app.map_widget.tile_image_cache  # tiles reais passaram pela thread da UI
                    root.tk.call(root.protocol("WM_DELETE_WINDOW"))
                root.after(350, close_window)
                root.mainloop()
                assert app.closing
                assert not app.map_widget.loading
                assert not download.running
                assert not writer.thread.is_alive()
                assert not receiver.thread.is_alive()
                assert port.closed
                assert len(list(packet_rows(writer.path))) == 1
                assert image_threads
                assert all(thread is threading.main_thread() for thread in image_threads)
                app.close_application()  # repetir o fechamento é seguro
                print("shutdown complete")
        ''')
        environment = dict(os.environ, PYTHONPATH=str(source))
        result = subprocess.run([sys.executable, "-c", script], env=environment,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("shutdown complete", result.stdout)
        self.assertNotIn("Exception", result.stderr)


if __name__ == "__main__":
    unittest.main()
