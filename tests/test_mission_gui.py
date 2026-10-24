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
            def __init__(self, *position): self.position = position
            def delete(self): pass
            def set_position(self, *args): self.position = args
            def set_text(self, text): pass
        class OfflineMap(tk.Frame):
            def __init__(self, parent, **kwargs):
                super().__init__(parent)
                self.position = None
            def set_position(self, *args): self.position = args
            def set_zoom(self, *args): pass
            def add_right_click_menu_command(self, **kwargs): pass
            def set_marker(self, latitude, longitude, **kwargs): return MapItem(latitude, longitude)
            def set_path(self, *args, **kwargs): return MapItem()
        self.map_patch = patch.object(tracker, 'OfflineMapView', OfflineMap)
        self.map_patch.start()
        self.cache_patch = patch.object(tracker, 'open_tile_cache', return_value=None)
        self.cache_patch.start()
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
        self.cache_patch.stop()
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
        self.assertIn('REPRODUÇÃO', self.app.lbl_connection.cget('text'))
        self.assertTrue(self.app.mission_bar.winfo_ismapped())
        self.assertEqual(str(self.app.btn_connect.cget('state')), 'disabled')
        self.app.replay.seek(15)
        self.app._render_replay(seek=True)
        self.assertIn('Última posição', self.app.lbl_distance_status.cget('text'))
        self.assertIn('atrasada', self.app.lbl_pointing_status.cget('text'))
        self.app.replay.seek(100)
        self.app._render_replay(seek=True)
        self.assertNotEqual(self.app.lbl_distance.cget('text'), '— m')
        self.app.exit_replay()
        self.root.update()
        self.assertFalse(self.app.mission_bar.winfo_ismapped())
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

    def test_mission_actions_open_to_the_side_without_moving_map(self):
        app = self.app
        self.root.overrideredirect(True)
        app.set_zoom(1.0)
        self.root.geometry('1600x1000')
        self.root.update()
        before = app.map_widget.winfo_rooty(), app.map_widget.winfo_height()
        self.assertFalse(app.mission_buttons.winfo_ismapped())
        app.btn_mission_toggle.invoke()
        self.root.update()
        self.assertTrue(app.mission_buttons.winfo_ismapped())
        self.assertGreaterEqual(app.mission_buttons.winfo_rootx(),
                                app.btn_mission_toggle.winfo_rootx() + app.btn_mission_toggle.winfo_width())
        self.assertFalse(app.mission_bar.winfo_ismapped())
        self.assertEqual((app.map_widget.winfo_rooty(), app.map_widget.winfo_height()), before)
        self.assertEqual([app.mission_buttons.entrycget(i, 'label') for i in range(8)],
                         ['Nova missão', 'Retomar missão', 'Encerrar missão', 'Reproduzir',
                          'Exportar CSV', 'Exportar KML', 'Exportar bruto', 'Detalhes'])
        self.root.event_generate('<Escape>')
        self.root.update()
        self.assertFalse(app.mission_buttons.winfo_ismapped())
        self.assertFalse(app.mission_buttons_visible)
        self.root.focus_force()
        app.btn_mission_toggle.invoke()
        self.root.update()
        with patch('mission_ui.messagebox.showinfo') as details:
            app.mission_buttons.activate(7)
            app.mission_buttons.event_generate('<Return>')
            self.root.update()
        self.assertEqual(details.call_count, 1)
        self.assertFalse(app.mission_buttons_visible)

    def test_actions_live_beside_status_in_compact_header(self):
        app = self.app
        self.root.overrideredirect(True)
        header, button = app.header_identity.master, app.btn_mission_toggle
        self.assertIs(button.master, app.header_actions)
        self.assertIs(app.btn_charts.master, app.header_actions)
        for zoom in (1.0, 1.5, 3.0):
            app.set_zoom(zoom)
            self.root.update()
            top = button.winfo_rooty() - header.winfo_rooty()
            self.assertGreaterEqual(top, 0)
            self.assertLessEqual(top + button.winfo_height(), header.winfo_height())
            self.assertFalse(app.mission_bar.winfo_ismapped())
            self.assertGreaterEqual(button.winfo_rootx(),
                                    app.lbl_connection.master.winfo_rootx() + app.lbl_connection.master.winfo_width())

    def test_tracker_card_lives_in_antenna_tab_beside_3d_view(self):
        app = self.app
        card = app.btn_tracker.master
        antenna_tab = app.navigation_tabs.nametowidget(app.navigation_tabs.tabs()[1])
        self.assertIs(card.master, antenna_tab)
        self.assertNotIn(card, app.sidebar_content.winfo_children())
        self.assertEqual(card.grid_info()['column'], app.antenna_canvas.get_tk_widget().grid_info()['column'] + 1)
        # Sem apontamento, o motivo aparece só uma vez na aba, acima da vista 3D.
        self.assertEqual(app.lbl_distance_status.cget('text'), '')
        self.assertTrue(app.lbl_pointing_status.cget('text'))

    def test_navigation_buttons_sit_beside_map_title_and_select_tabs(self):
        import tracker
        app = self.app
        self.root.overrideredirect(True)
        self.root.geometry('2160x1350')
        self.root.update()
        # Sem faixa de abas: o mapa começa no topo do Notebook.
        self.assertEqual(app.map_widget.winfo_rooty(), app.navigation_tabs.winfo_rooty())
        self.assertEqual([button.cget('text') for button in app.navigation_buttons],
                         ['Mapa da missão', 'Antena 3D', 'Sonda 3D'])
        self.assertEqual(app.navigation_buttons_group.grid_info()['row'], app.map_title_group.grid_info()['row'])
        app.navigation_buttons[2].invoke()
        self.root.update()
        self.assertEqual(app.navigation_tabs.index('current'), 2)
        self.assertEqual(app.navigation_buttons[2].cget('fg'), tracker.COLOR_ACCENT_CYAN)
        self.assertNotEqual(app.navigation_buttons[0].cget('fg'), tracker.COLOR_ACCENT_CYAN)
        app.navigation_buttons[0].invoke()
        # Na janela estreita os controles do mapa descem para a segunda linha em vez de serem cortados.
        for zoom, size, wrapped in ((1.5, '2160x1350', False), (1.0, '1080x820', True)):
            app.set_zoom(zoom)
            self.root.geometry(size)
            self.root.update()
            self.assertEqual(app.map_layer_group.grid_info()['row'], 1 if wrapped else 0)
            for group in app.map_header.winfo_children():
                self.assertGreaterEqual(group.winfo_width(), group.winfo_reqwidth())

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

    def test_recenter_button_prefers_sonde_then_tracker_then_home(self):
        import tracker
        self.app.map_widget.set_position(0, 0)
        self.app.btn_recenter.invoke()
        self.assertEqual(self.app.map_widget.position, tracker.HOME_POSITION)
        self.app.tracker_marker = self.app.map_widget.set_marker(-15.8, -47.9)
        self.app.btn_recenter.invoke()
        self.assertEqual(self.app.map_widget.position, (-15.8, -47.9))
        self.app.current_marker = self.app.map_widget.set_marker(-15.6, -47.7)
        self.app.current_marker.set_position(-15.5, -47.6)
        self.app.btn_recenter.invoke()
        self.assertEqual(self.app.map_widget.position, (-15.5, -47.6))

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
        menu = self.app.mission_buttons
        index = next(i for i in range(menu.index('end') + 1) if menu.entrycget(i, 'label') == 'Exportar KML')
        with patch('mission_ui.filedialog.asksaveasfilename', return_value=str(destination)) as dialog:
            menu.invoke(index)
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

    def test_probe_tab_rotates_model_with_attitude(self):
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
        app = self.app
        self.assertEqual(app.lbl_probe_pitch.cget('text'), '—°')
        self.assertIn('Aguardando', app.lbl_probe_status.cget('text'))
        record = dict(packet(), received_at=utc_now(), elapsed=0)
        record['fields'] = dict(record['fields'], Pitch=-10.5, Roll=170, Yaw=45)
        app._apply_record(record)
        app.update_gui()
        self.assertEqual([label.cget('text') for label in (app.lbl_probe_pitch, app.lbl_probe_roll, app.lbl_probe_yaw)],
                         ['-10.5°', '+170.0°', '+45.0°'])
        self.assertIn('cabeça para baixo', app.lbl_probe_status.cget('text'))
        boxes = [item for item in app.probe_axis.collections if isinstance(item, Poly3DCollection)]
        self.assertEqual(len(boxes), 1)
        app._apply_record(dict(record, fields={'Fix': 0}, gps_valid=False, elapsed=1))
        app.update_gui()
        self.assertEqual(app.lbl_probe_yaw.cget('text'), '—°')
        self.assertFalse([item for item in app.probe_axis.collections if isinstance(item, Poly3DCollection)])
        self.root.overrideredirect(True)
        self.root.geometry('%dx%d' % self.root.minsize())
        app.navigation_tabs.select(2)
        self.root.update()
        self.assertFalse(app.charts_card.winfo_ismapped())
        self.assertGreater(app.probe_canvas.get_tk_widget().winfo_height(), 150)
        app.navigation_tabs.select(0)
        self.root.update()
        self.assertFalse(app.charts_card.winfo_ismapped())
        app.btn_charts.invoke()
        self.root.update()
        self.assertTrue(app.charts_card.winfo_ismapped())
        app.navigation_tabs.select(2)
        self.root.update()
        self.assertFalse(app.charts_card.winfo_ismapped())
        app.navigation_tabs.select(0)
        self.root.update()
        self.assertTrue(app.charts_card.winfo_ismapped())

    def test_rescue_data_fits_without_scrolling_and_map_gets_most_of_window(self):
        app = self.app
        self.root.overrideredirect(True)
        app.set_zoom(1.0)
        self.root.minsize(1080, 600)  # simula o limite aplicado numa tela de notebook
        for width, height in ((1280, 688), (1366, 768), (1920, 1080)):
            self.root.geometry(f'{width}x{height}')
            self.root.update()
            self.assertEqual((self.root.winfo_width(), self.root.winfo_height()), (width, height))
            self.assertEqual(app.sidebar_tabs.index('current'), 0)
            canvas = app.sidebar_canvases[app.sidebar_tabs.select()]
            self.assertLessEqual(app.sidebar_content.winfo_height(), canvas.winfo_height())
            self.assertGreater(app.map_widget.winfo_width(), width * .6)
            self.assertGreater(app.map_widget.winfo_height(), height * .6)
            self.assertFalse(app.charts_card.winfo_ismapped())
        self.assertEqual([app.sidebar_tabs.tab(tab, 'text') for tab in app.sidebar_tabs.tabs()],
                         ['Resgate', 'Sensores', 'Mensagens', 'Comandos'])
        app._set_configuration({'latitude':-15.1, 'longitude':-47.1, 'altitude':1000}, None)
        app._apply_record(dict(packet(), received_at=utc_now(), elapsed=0))
        app.update_gui()
        app.update_antenna()
        self.assertNotEqual(app.lbl_rescue_distance.cget('text'), '—')
        self.assertIn('recente', app.lbl_rescue_status.cget('text'))

    def test_charts_fill_bottom_section_while_map_and_sidebar_remain_visible(self):
        app = self.app
        self.root.overrideredirect(True)
        app.set_zoom(1.0)
        self.root.minsize(1080, 600)
        for width, height in ((1280, 688), (1920, 1080)):
            self.root.geometry(f'{width}x{height}')
            self.root.update()
            map_height = app.map_widget.winfo_height()
            app.btn_charts.invoke()
            self.root.update()
            self.assertTrue(app.workspace.winfo_ismapped())
            self.assertTrue(app.sidebar_shell.winfo_ismapped())
            self.assertTrue(app.map_widget.winfo_ismapped())
            self.assertTrue(app.charts_card.winfo_ismapped())
            self.assertGreaterEqual(app.charts_card.winfo_rooty(),
                                    app.workspace.winfo_rooty() + app.workspace.winfo_height())
            self.assertLess(app.charts_card.winfo_height(), app.workspace.winfo_height())
            chart = app.canvas.get_tk_widget()
            self.assertGreater(chart.winfo_width(), width * .95)
            self.assertGreater(chart.winfo_height(), height * .15)
            self.assertLess(chart.winfo_height(), height * .4)
            self.assertEqual(app.btn_charts.cget('text'), 'Ocultar gráficos')
            app.btn_charts.invoke()
            self.root.update()
            self.assertFalse(app.charts_card.winfo_ismapped())
            self.assertEqual(app.map_widget.winfo_height(), map_height)

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
            self.assertNotEqual(self.app.lbl_distance.cget('text'), '— m')  # mantém o último fix 3D
            self.app.disconnect_serial()
            self.assertTrue(self.app.mission.flush())
            records = list(packet_rows(self.app.mission.path))
            self.assertEqual(len(records), 3)
            self.assertNotEqual(records[0]['source_frame'], records[1]['source_frame'])
            self.assertEqual(self.app.mission.metadata['id'], mission_id)
        finally:
            os.close(master)
            os.close(slave)

    def test_messages_station_identity_and_command_restriction(self):
        import select
        master, slave = os.openpty()
        try:
            self.app.port_devices = {'test': os.ttyname(slave)}
            self.app.port_cb['values'] = ['test']
            self.app.port_cb.set('test')
            self.app.toggle_connection()
            self.assertTrue(self.app.is_connected)
            os.write(master, b'[ESTACAO] ID:B N:3\n[MSG] ENFILEIRADA Id:B7 Texto:Ola\n'
                             b'[MSG] RECEBIDA Id:C2 De:C Hora:10:00:01 RSSI:-90 SNR:3 Texto:Pouso em -15.8 -47.9\n'
                             b'[ACK] Ack:6\n[MSG] ENTREGUE Id:B7 Tentativas:1\n')
            deadline = time.monotonic() + 3
            while 'repetida' not in self.app.lbl_message_status.cget('text') and time.monotonic() < deadline:
                self.root.update()
                time.sleep(.01)
            self.assertEqual(self.app.lbl_message_status.cget('text'), 'Mensagem B7 repetida pelo balão.')
            self.assertEqual(self.app.lbl_station.cget('text'), 'B de 3')
            self.assertEqual((self.app.station_letter.get(), self.app.station_total.get()), ('B', '3'))
            self.assertIn('[10:00:01] C: Pouso em -15.8 -47.9', self.app.message_log.get('1.0', 'end'))
            self.assertEqual(self.app.sidebar_tabs.tab(2, 'text'), 'Mensagens (1)')
            self.app.sidebar_tabs.select(2)
            self.root.update()
            self.assertEqual(self.app.sidebar_tabs.tab(2, 'text'), 'Mensagens')
            self.app.sidebar_tabs.select(0)
            self.root.update()
            self.app.update_gui()
            self.assertEqual(self.app.lbl_ack.cget('text'), '6')

            self.app.message_text.set('Direção  norte')
            self.assertEqual(self.app.lbl_message_count.cget('text'), '13/100')
            self.app.send_message()
            self.assertEqual(self.app.message_text.get(), 'Direção  norte')

            def notice(data, condition):
                os.write(master, data)
                deadline = time.monotonic() + 3
                while not condition() and time.monotonic() < deadline:
                    self.root.update()
                    time.sleep(.01)
                self.assertTrue(condition())
            notice(b'[MSG] RECUSADA Motivo:ocupada Id:B7\n',
                   lambda: 'recusada' in self.app.lbl_message_status.cget('text'))
            self.assertEqual(self.app.message_text.get(), 'Direção  norte')
            notice(b'[MSG] ENFILEIRADA Id:B8 Texto:Direcao norte\n', lambda: self.app.message_text.get() == '')
            self.app.station_total.set('5')
            self.app._update_station_letters()
            self.app.station_letter.set('E')
            self.app.apply_station()
            written, deadline = b'', time.monotonic() + 3
            while b'ID E 5' not in written and time.monotonic() < deadline:
                if select.select([master], [], [], .05)[0]:
                    written += os.read(master, 1024)
            self.assertEqual(written.replace(b'\r', b''), b'ID?\nM Direcao norte\nID E 5\n')

            self.app.cmd_entry.insert(0, '5')
            self.app.send_command()
            self.assertEqual(self.errors, [('Telecomando', 'Telecomandos só podem sair da estação A.')])
            self.errors.clear()
            self.app.calibrate_imu()  # também é telecomando: barrado antes da confirmação
            self.assertEqual(self.errors, [('Telecomando', 'Telecomandos só podem sair da estação A.')])
            self.errors.clear()
            self.app.disconnect_serial()
            self.app._reset_display()  # trocar de missão ou abrir reprodução
            self.assertEqual(self.app.message_log.get('1.0', 'end').strip(), '')
            self.assertEqual(self.app.lbl_message_status.cget('text'), 'Nenhuma mensagem enviada.')
            self.assertEqual(self.app.lbl_station.cget('text'), 'B de 3')
            self.app.port_devices = {'test': os.ttyname(slave)}  # outro rádio na mesma porta
            self.app.port_cb['values'] = ['test']
            self.app.port_cb.set('test')
            self.app.toggle_connection()
            self.assertTrue(self.app.is_connected)
            self.assertIsNone(self.app.station_identity)
            self.assertEqual(self.app.lbl_station.cget('text'), '—')
            self.app.disconnect_serial()
        finally:
            os.close(master)
            os.close(slave)

if __name__ == '__main__':
    unittest.main()
