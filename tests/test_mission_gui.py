import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from mission import MissionWriter, packet_rows, read_metadata, utc_now
from test_mission import packet, FRAME


@unittest.skipUnless(os.environ.get('DISPLAY'), 'GUI tests require a display (Xvfb supported)')
class MissionUITests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        import tracker
        class MapItem:
            def delete(self): pass
            def set_position(self, *args): pass
            def set_text(self, text): pass
        class OfflineMap(tk.Frame):
            def __init__(self, parent, **kwargs): super().__init__(parent)
            def set_position(self, *args): pass
            def set_zoom(self, *args): pass
            def add_right_click_menu_command(self, **kwargs): pass
            def set_marker(self, *args, **kwargs): return MapItem()
            def set_path(self, *args, **kwargs): return MapItem()
        self.map_patch = patch.object(tracker.tkintermapview, 'TkinterMapView', OfflineMap)
        self.map_patch.start()
        self.errors = []
        self.error_patch = patch('mission_ui.messagebox.showerror', side_effect=lambda *args,**kwargs:self.errors.append(args))
        self.error_patch.start()
        self.root = tk.Tk()
        self.app = tracker.SondeTrackerApp(self.root)
        self.root.update()
        self.temp = tempfile.TemporaryDirectory()
        self.app.mission = MissionWriter.create(self.temp.name, 'UI', sync_interval=.01)

    def tearDown(self):
        self.app.close_application()
        self.root = None
        self.error_patch.stop()
        self.map_patch.stop()
        self.temp.cleanup()
        self.assertEqual(self.errors, [])

    def test_replay_uses_historical_settings_and_never_writes_to_mission(self):
        mission = self.app.mission
        for elapsed in (0, 30, 100):
            mission.submit('packet', packet(), received_at=utc_now(), elapsed=elapsed)
        self.assertTrue(mission.flush())
        self.app._set_configuration({'latitude':1, 'longitude':2, 'altitude':3}, [4,5])
        before = mission.snapshot()['written_records']
        with patch.object(self.app, '_choose_mission', return_value=str(mission.path)):
            self.app.open_replay()
        self.root.update()
        self.assertIsNotNone(self.app.replay)
        self.assertEqual(self.app.tracker_position.altitude, 1000)
        self.assertEqual(self.app.lbl_mode.cget('text'), 'REPRODUÇÃO')
        self.assertEqual(str(self.app.btn_connect.cget('state')), 'disabled')
        self.app.replay.seek(15)
        self.app._render_replay(seek=True)
        self.assertEqual(self.app.lbl_distance.cget('text'), '— m')
        self.assertIn('atrasada', self.app.lbl_pointing_status.cget('text'))
        self.app.replay.seek(100)
        self.app._render_replay(seek=True)
        self.assertNotEqual(self.app.lbl_distance.cget('text'), '— m')
        self.app.exit_replay()
        self.assertEqual(self.app.tracker_position.altitude, 3)
        self.assertEqual(self.app.capture_settings[1], [4,5])
        self.assertEqual(mission.snapshot()['written_records'], before)

    def test_live_fields_clear_and_charts_stay_bounded(self):
        record = dict(packet(), received_at=utc_now(), elapsed=0)
        self.app._apply_record(record)
        self.app.update_gui()
        self.assertEqual(self.app.lbl_bat.cget('text'), '4.1 V')
        record = dict(record, fields={'Fix':0}, gps_valid=False, elapsed=1)
        self.app._apply_record(record)
        self.app.update_gui()
        self.assertEqual(self.app.lbl_bat.cget('text'), '—')
        self.assertIsNone(self.app.current_marker)
        for index in range(25000):
            self.app._record_history(record)
        self.app.update_charts()
        self.assertEqual(len(self.app.history_time), 21600)
        self.assertLessEqual(len(self.app.fig.axes[0].lines[0].get_xdata()), 1001)
        self.root.geometry('1080x720')
        self.app.navigation_tabs.select(1)
        self.root.update()
        self.assertGreater(self.app.antenna_canvas.get_tk_widget().winfo_height(), 150)

    def test_end_marks_mission_closed_and_disables_connect(self):
        path = self.app.mission.path
        self.app.end_mission()
        self.assertIsNone(self.app.mission)
        self.assertEqual(read_metadata(path)['status'], 'closed')
        self.assertEqual(str(self.app.btn_connect.cget('state')), 'disabled')

    @unittest.skipUnless(hasattr(os, 'openpty'), 'pseudo-terminal test requires POSIX')
    def test_real_serial_connection_reconnect_and_invalid_packet(self):
        master, slave = os.openpty()
        try:
            device = os.ttyname(slave)
            mission_id = self.app.mission.metadata['id']
            self.app._set_configuration({'latitude':-15.1, 'longitude':-47.1, 'altitude':1000}, [0,0])
            def connect():
                self.app.port_devices = {'test':device}
                self.app.port_cb['values'] = ['test']
                self.app.port_cb.set('test')
                self.app.toggle_connection()
                self.assertTrue(self.app.is_connected)
            def send(data, expected_count):
                os.write(master, data)
                deadline = time.monotonic() + 3
                while self.app.sample_count < expected_count and time.monotonic() < deadline:
                    self.root.update()
                    time.sleep(.01)
                self.assertEqual(self.app.sample_count, expected_count)
            connect()
            send(FRAME, 1)
            self.assertNotEqual(self.app.lbl_distance.cget('text'), '— m')
            self.assertEqual(self.app.lbl_bat.cget('text'), '4.1 V')
            self.assertTrue(self.app.disconnect_serial())
            connect()
            send(FRAME, 2)
            send(FRAME.replace(b'Alt:3000\n', b''), 3)
            self.assertEqual(self.app.lbl_distance.cget('text'), '— m')
            self.app.disconnect_serial()
            self.assertTrue(self.app.mission.flush())
            records = list(packet_rows(self.app.mission.path))
            self.assertEqual(len(records), 3)
            self.assertNotEqual(records[0]['source_frame'], records[1]['source_frame'])
            self.assertEqual(self.app.mission.metadata['id'], mission_id)
        finally:
            os.close(master)
            os.close(slave)

if __name__ == '__main__':
    unittest.main()
