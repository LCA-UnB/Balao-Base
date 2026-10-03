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

    def test_zoom_shortcuts_scale_fonts_layout_and_figures(self):
        from zoom import ZOOM_MAX, ZOOM_MIN, scaled
        app = self.app
        def font_size(widget):
            return int(self.root.tk.splitlist(str(widget.cget('font')))[1])
        def snapshot():
            return (font_size(app.lbl_callsign), int(app.sidebar_shell.cget('width')),
                    round(app.fig.dpi), round(app.antenna_fig.dpi), self.root.minsize())
        self.assertEqual((ZOOM_MIN, ZOOM_MAX), (1.0, 3.0))
        self.assertGreaterEqual(app.default_zoom, 1.0)
        self.assertLessEqual(app.default_zoom, 1.5)
        self.assertEqual(snapshot()[:3], (scaled(17, app.zoom), scaled(370, app.zoom), round(100 * app.zoom)))
        app.set_zoom(1.0)
        self.root.update()
        original = snapshot()
        self.assertEqual(original[:4], (17, 370, 100, 100))

        self.root.focus_force()
        self.root.event_generate('<Control-plus>')
        self.root.update()
        self.assertEqual(app.zoom, 1.1)
        zoomed = snapshot()
        self.assertEqual(zoomed[:4], (scaled(17, 1.1), scaled(370, 1.1), 110, 110))
        self.assertEqual(original[4], (1080, 820))
        self.assertEqual(zoomed[4], (round(1080 * 1.1), round(820 * 1.1)))
        self.root.event_generate('<Control-minus>')
        self.root.update()
        self.assertEqual(snapshot()[:4], original[:4])

        app.set_zoom(99)
        self.assertEqual(app.zoom, ZOOM_MAX)
        app.set_zoom(0)
        self.assertEqual(app.zoom, ZOOM_MIN)
        self.root.event_generate('<Control-0>')
        self.root.update()
        self.assertEqual(app.zoom, app.default_zoom)

    def test_mission_buttons_wrap_instead_of_leaving_the_window(self):
        app = self.app
        self.root.overrideredirect(True)  # sem o gerenciador de janelas decidindo o tamanho
        def rows_and_overflow():
            self.root.update()
            frame = app.mission_buttons
            rows = {button.grid_info()['row'] for button in app.mission_action_buttons}
            overflow = max(button.winfo_x() + button.winfo_width() for button in app.mission_action_buttons) - frame.winfo_width()
            return rows, overflow
        app.set_zoom(1.0)
        self.root.geometry('2400x900')
        rows, overflow = rows_and_overflow()
        self.assertEqual(rows, {0})
        self.assertLessEqual(overflow, 0)
        for zoom, width in ((1.5, 900), (3.0, 1400)):
            app.set_zoom(zoom)
            self.root.geometry(f'{width}x1400')
            rows, overflow = rows_and_overflow()
            self.assertGreater(len(rows), 1)
            self.assertLessEqual(overflow, 0)

    def test_tracker_dialog_is_large_and_fits_its_content(self):
        import tkinter as tk
        app = self.app
        app.set_zoom(1.5)
        self.root.update()
        sizes, original = [], app.center_dialog
        def spy(dialog, width, height):
            sizes.append(original(dialog, width, height))
            return sizes[-1]
        with patch.object(app, 'center_dialog', side_effect=spy):
            app.configure_tracker()
        dialog = next(child for child in self.root.winfo_children() if isinstance(child, tk.Toplevel))
        (width, height), = sizes
        limit = (self.root.winfo_screenwidth() * .9, self.root.winfo_screenheight() * .9)
        # O gerenciador de janelas decide o tamanho final; aqui vale o que o programa pede.
        self.assertEqual(width, min(720 * 1.5, limit[0]))
        self.assertGreaterEqual(height, min(300 * 1.5, limit[1]))
        self.assertLessEqual(height, limit[1])
        self.assertLessEqual(dialog.winfo_reqwidth(), width)
        self.assertLessEqual(dialog.winfo_reqheight(), height)
        dialog.destroy()

    def test_center_dialog_uses_window_center_and_stays_on_screen(self):
        from unittest.mock import MagicMock
        app = self.app
        app.set_zoom(1.5)
        screen = (self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        with patch.object(self.root, 'winfo_rootx', return_value=200), patch.object(self.root, 'winfo_rooty', return_value=100), \
                patch.object(self.root, 'winfo_width', return_value=1200), patch.object(self.root, 'winfo_height', return_value=800):
            dialog = MagicMock(winfo_reqwidth=lambda: 0, winfo_reqheight=lambda: 0)
            self.assertEqual(app.center_dialog(dialog, 720, 460), [1080, 690])
            dialog.geometry.assert_called_once_with('1080x690+260+155')
            with patch.object(self.root, 'winfo_rootx', return_value=screen[0] - 100), patch.object(self.root, 'winfo_rooty', return_value=-500):
                dialog = MagicMock(winfo_reqwidth=lambda: 0, winfo_reqheight=lambda: 0)
                app.center_dialog(dialog, 720, 460)
                dialog.geometry.assert_called_once_with(f'1080x690+{screen[0] - 1080}+0')

    def click_export_kml(self, destination):
        import tkinter as tk
        def find(widget):
            if isinstance(widget, tk.Button) and widget.cget('text') == 'Exportar KML':
                return widget
            return next((found for child in widget.winfo_children() if (found := find(child))), None)
        button = find(self.root)
        self.assertIsNotNone(button)
        with patch('mission_ui.filedialog.asksaveasfilename', return_value=str(destination)) as dialog:
            button.invoke()
        deadline = time.monotonic() + 5
        while self.app.background_results.empty() and time.monotonic() < deadline:
            time.sleep(.01)
        self.app.gui_updater_loop()
        return dialog

    def test_export_kml_button_writes_file_and_reports_missing_gps(self):
        import xml.etree.ElementTree as ET
        mission = self.app.mission
        mission.submit('packet', packet(), received_at=utc_now(), elapsed=1)
        self.assertTrue(mission.flush())
        destination = Path(self.temp.name) / 'trajeto.kml'
        with patch('mission_ui.messagebox.showinfo') as info:
            dialog = self.click_export_kml(destination)
        logs = Path(__file__).resolve().parents[1] / 'logs'
        self.assertEqual(self.app.default_mission_directory, logs)
        self.assertEqual(dialog.call_args.kwargs['initialdir'], str(logs))
        self.assertEqual(dialog.call_args.kwargs['initialfile'], mission.path.parent.name + '.kml')
        self.assertIn('Exportados 1 pontos', info.call_args.args[1])
        self.assertEqual(ET.parse(destination).getroot().tag, '{http://www.opengis.net/kml/2.2}kml')
        self.assertFalse(self.app.export_busy)

        empty = MissionWriter.create(self.temp.name, 'SemGPS', sync_interval=.01)
        empty.submit('packet', dict(packet(), gps_valid=False), received_at=utc_now(), elapsed=1)
        self.assertTrue(empty.flush())
        self.app.mission = empty
        without_gps = Path(self.temp.name) / 'sem-gps.kml'
        self.click_export_kml(without_gps)
        self.assertEqual(len(self.errors), 1)
        self.assertIn('GPS 3D válido', self.errors[0][1])
        self.assertFalse(without_gps.exists())
        self.errors.clear()
        empty.abandon()

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
        self.root.overrideredirect(True)  # sem o gerenciador de janelas decidindo o tamanho
        self.root.geometry('%dx%d' % self.root.minsize())
        self.app.navigation_tabs.select(1)
        self.root.update()
        self.assertGreater(self.app.antenna_canvas.get_tk_widget().winfo_height(), 150)

    def test_end_marks_mission_closed_and_disables_connect(self):
        path = self.app.mission.path
        self.app.end_mission()
        self.assertIsNone(self.app.mission)
        self.assertEqual(read_metadata(path)['status'], 'closed')
        self.assertEqual(str(self.app.btn_connect.cget('state')), 'disabled')

    def test_calibrate_imu_button_confirms_before_sending(self):
        from unittest.mock import MagicMock
        app = self.app
        app.btn_calibrate_imu.invoke()
        self.assertEqual(len(self.errors), 1)
        self.assertIn('Conecte o rádio', self.errors[0][1])
        self.errors.clear()

        port = MagicMock()
        app.serial_port, app.is_connected = port, True
        with patch('mission_ui.messagebox.askyesno', return_value=False) as ask:
            app.btn_calibrate_imu.invoke()
        self.assertIn('carga parada', ask.call_args.args[1])
        self.assertIn('ACK 2501', ask.call_args.args[1])
        port.write.assert_not_called()

        with patch('mission_ui.messagebox.askyesno', return_value=True), patch.object(app.mission, 'event') as event:
            app.btn_calibrate_imu.invoke()
        port.write.assert_called_once_with(b'2500\n')
        event.assert_called_once_with('command_sent', command='2500')

        port.reset_mock()
        app.cmd_entry.insert(0, '12')
        app.send_command()
        port.write.assert_called_once_with(b'12\n')
        self.assertEqual(app.cmd_entry.get(), '')
        app.serial_port, app.is_connected = None, False

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
