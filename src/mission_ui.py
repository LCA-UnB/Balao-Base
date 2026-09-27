"""Mission and replay controls shared by the Tk ground-station view."""
from collections import deque
from dataclasses import asdict
import math
from pathlib import Path
from queue import Empty, Queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import serial

from antenna import Position
from mission import MissionWriter, export_csv, export_kml, export_raw, read_metadata
from replay import MissionReplay
from station import StationReceiver


class MissionControls:
    def init_missions(self):
        self.mission = None
        self.receiver = None
        self.replay = None
        self.replay_saved_settings = None
        self.capture_settings = (None, None)
        self.current_record = None
        self.packet_monotonic = None
        self.sample_count = 0
        self.closing = False
        self.background_results = Queue()
        self.export_busy = False
        self.replay_scrubbing = False
        self.default_mission_directory = Path.home() / "Balao-Missoes"
        self.history_time = deque(maxlen=21600)
        self.history_temp = deque(maxlen=21600)
        self.history_alt = deque(maxlen=21600)
        self.history_press = deque(maxlen=21600)
        self.history_hum = deque(maxlen=21600)
        self.path_coordinates = deque(maxlen=21600)

    def _build_mission_bar(self):
        bar = tk.Frame(self.root, bg="#101720", padx=14, pady=6)
        bar.grid(row=1, column=0, sticky="ew")
        bar.grid_columnconfigure(0, weight=1)
        actions = tk.Frame(bar, bg="#101720")
        actions.grid(row=0, column=0, sticky="ew")
        actions.grid_columnconfigure(0, weight=1)
        self.mission_buttons = tk.Frame(actions, bg="#101720")
        self.mission_buttons.grid(row=0, column=0, sticky="ew")
        self.mission_action_buttons = [
            tk.Button(self.mission_buttons, text=title, command=command, bg="#1b2633", fg="#f2f5f8", relief=tk.FLAT,
                      font=("Segoe UI", 12), padx=22, pady=12, cursor="hand2")
            for title, command in (("Nova missão", self.new_mission), ("Retomar missão", self.resume_mission),
                                   ("Encerrar missão", self.end_mission), ("Reproduzir", self.open_replay),
                                   ("Exportar CSV", self.save_csv), ("Exportar KML", self.save_kml),
                                   ("Exportar bruto", self.save_raw), ("Detalhes", self.log_details))]
        self._mission_layout = None
        self.mission_buttons.bind("<Configure>", lambda event: self._flow_mission_buttons())
        self.lbl_mode = self._label(actions, "AO VIVO", 9, "#38d683", "bold")
        self.lbl_mode.grid(row=0, column=1, sticky="ne", padx=(12, 0))
        self.lbl_mission_health = self._label(bar, "Crie ou retome uma missão para conectar o rádio.", 9, "#94a3b5")
        self.lbl_mission_health.grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.replay_bar = tk.Frame(bar, bg="#101720")
        self.replay_bar.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self.replay_bar.grid_columnconfigure(2, weight=1)
        self.btn_play = tk.Button(self.replay_bar, text="Reproduzir", command=self.toggle_replay, width=10, padx=14, pady=8, font=("Segoe UI", 11))
        self.btn_play.grid(row=0, column=0, padx=(0, 7))
        self.replay_speed = ttk.Combobox(self.replay_bar, state="readonly", width=5, font=("Segoe UI", 11), values=("0.5", "1", "2", "5", "10", "30", "60"))
        self.replay_speed.set("1")
        self.replay_speed.grid(row=0, column=1)
        self.replay_speed.bind("<<ComboboxSelected>>", lambda event: self.replay.set_speed(self.replay_speed.get()) if self.replay else None)
        self.replay_seek = ttk.Scale(self.replay_bar, from_=0, to=1)
        self.replay_seek.grid(row=0, column=2, sticky="ew", padx=10)
        self.replay_seek.bind("<ButtonPress-1>", lambda event: setattr(self, "replay_scrubbing", True))
        self.replay_seek.bind("<ButtonRelease-1>", self.seek_replay)
        self.replay_seek.bind("<KeyRelease>", self.seek_replay)
        self.lbl_replay_time = self._label(self.replay_bar, "00:00:00 / 00:00:00", 9)
        self.lbl_replay_time.grid(row=0, column=3, padx=8)
        tk.Button(self.replay_bar, text="Voltar ao vivo", command=self.exit_replay, padx=14, pady=8, font=("Segoe UI", 11)).grid(row=0, column=4)
        self.replay_bar.grid_remove()

    def _flow_mission_buttons(self):
        """Quebra os botões da barra em linhas quando a janela é estreita ou o zoom é grande."""
        width = self.mission_buttons.winfo_width()
        width = width if width > 1 else 10 ** 6
        gap, layout, used, row, column = 8, [], 0, 0, 0
        for button in self.mission_action_buttons:
            need = button.winfo_reqwidth() + gap
            if column and used + need > width:
                row, column, used = row + 1, 0, 0
            layout.append((row, column))
            used, column = used + need, column + 1
        if layout != self._mission_layout:
            self._mission_layout = layout
            for button, (row, column) in zip(self.mission_action_buttons, layout):
                button.grid(row=row, column=column, padx=(0, gap), pady=(0, gap // 2))

    def on_zoom_applied(self):
        self._mission_layout = None
        self.root.after_idle(self._flow_mission_buttons)

    def _choose_mission(self, title):
        return filedialog.askopenfilename(parent=self.root, title=title,
            initialdir=str(self.default_mission_directory), filetypes=[("Missão SQLite", "*.sqlite3")])

    def _build_command_card(self):
        card = self._card(self.sidebar_content, "Energia e telecomando")
        card.pack(fill=tk.X, pady=(0, 9))
        for title, attribute in (("Bateria", "lbl_bat"), ("ACK recebido", "lbl_ack")):
            row, value = self._data_row(card, title)
            row.pack(fill=tk.X, pady=(0, 7))
            setattr(self, attribute, value)
        row = tk.Frame(card, bg=card.cget("bg"))
        row.pack(fill=tk.X)
        self.cmd_entry = ttk.Entry(row, width=12, font=("Segoe UI", 12))
        self.cmd_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=5)
        tk.Button(row, text="Enviar comando", command=self.send_command, padx=16, pady=8, font=("Segoe UI", 11)).pack(side=tk.RIGHT, padx=(8, 0))

    def _release_mission(self):
        if self.is_connected or self.replay:
            messagebox.showinfo("Missão", "Desconecte o rádio e volte ao modo ao vivo antes de trocar de missão.")
            return False
        if self.mission and not self.mission.close():
            messagebox.showerror("Gravação pendente", "Não foi possível salvar todos os registros. A recuperação continua; consulte Detalhes.")
            return False
        self.mission = None
        return True

    def new_mission(self):
        if self.is_connected or self.replay:
            messagebox.showinfo("Nova missão", "Desconecte o rádio e volte ao modo ao vivo primeiro.")
            return
        name = simpledialog.askstring("Nova missão", "Nome da missão:", parent=self.root)
        if name is None:
            return
        try:
            self.default_mission_directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            messagebox.showerror("Pasta de missões indisponível", str(error))
            return
        parent = filedialog.askdirectory(parent=self.root, title="Pasta para as missões", initialdir=str(self.default_mission_directory))
        if not parent or not self._release_mission():
            return
        try:
            self.mission = MissionWriter.create(parent, name)
            self._reset_display()
            self._notify_configuration()
            self._update_mission_health()
            self._on_port_selected()
        except Exception as error:
            messagebox.showerror("Não foi possível criar a missão", str(error))

    def resume_mission(self):
        path = self._choose_mission("Retomar missão aberta")
        if not path:
            return
        try:
            metadata = read_metadata(path)
            if metadata["status"] == "closed":
                raise ValueError("Essa missão já foi encerrada. Use Reproduzir para consultá-la.")
            if not self._release_mission():
                return
            self.mission = MissionWriter(path)
            self._reset_display()
            self._set_configuration(metadata.get("tracker"), metadata.get("orientation"))
            self._update_mission_health()
            self._on_port_selected()
        except Exception as error:
            messagebox.showerror("Não foi possível retomar", str(error))

    def end_mission(self):
        if not self.mission:
            return
        if self.replay:
            messagebox.showinfo("Missão", "Volte ao modo ao vivo para encerrar a missão.")
            return
        if not self.disconnect_serial():
            return
        if not self.mission.close(end_mission=True):
            messagebox.showerror("Gravação pendente", "A missão ainda não foi encerrada com segurança. A recuperação continua.")
            return
        path = self.mission.path
        self.mission = None
        self.lbl_mission_health.config(text=f"Missão encerrada · {path.parent.name}", fg="#38d683")
        self.lbl_log_status.config(text="Missão salva e encerrada", fg="#94a3b5")
        self._on_port_selected()

    def _notify_configuration(self):
        if self.replay:
            return
        self.capture_settings = (asdict(self.tracker_position) if self.tracker_position else None,
                                 list(self.antenna_orientation) if self.antenna_orientation else None)
        if self.mission and not self.mission.end_requested:
            try:
                self.mission.event("configuration", tracker=self.capture_settings[0], orientation=self.capture_settings[1])
            except RuntimeError as error:
                messagebox.showerror("Gravação indisponível", str(error))

    def _set_configuration(self, tracker, orientation):
        self.tracker_position = Position(**tracker) if tracker else None
        self.antenna_orientation = tuple(orientation) if orientation else None
        for entry, value in zip(self.orientation_entries, orientation or ("", "")):
            entry.config(state="normal")
            entry.delete(0, tk.END)
            entry.insert(0, str(value))
            entry.config(state="disabled" if self.replay else "normal")
        if self.tracker_marker:
            self.tracker_marker.delete()
            self.tracker_marker = None
        if self.tracker_position:
            p = self.tracker_position
            self.tracker_marker = self.map_widget.set_marker(p.latitude, p.longitude, text="Tracker / antena",
                marker_color_circle="#27c8d9", marker_color_outside="#4aa8ff")
            self.lbl_tracker_position.config(text=f"Tracker: {p.latitude:.6f}, {p.longitude:.6f}\nAltitude MSL: {p.altitude:.1f} m")
        else:
            self.lbl_tracker_position.config(text="Defina a posição da antena em solo.")
        if not self.replay:
            self.capture_settings = (tracker, orientation)
        self._pointing_view_key = None

    def toggle_connection(self):
        if self.is_connected:
            self.disconnect_serial()
            return
        if self.replay or not self.mission or self.mission.end_requested:
            messagebox.showinfo("Conexão", "Crie ou retome uma missão no modo ao vivo primeiro.")
            return
        if not self.mission.snapshot()["alive"]:
            messagebox.showerror("Missão indisponível", "Reabra a missão para reiniciar a gravação.")
            return
        port = self.port_devices.get(self.port_cb.get())
        if not port:
            return
        try:
            self.serial_port = serial.Serial(port, 115200, timeout=.2, write_timeout=1)
            self.receiver = StationReceiver(self.serial_port, self.mission, lambda: self.capture_settings)
            self.antenna_packet = None
            self.current_record = None
            self.last_gps_data = None
            self.is_connected = True
            self.btn_connect.config(text="Desconectar", bg="#ff6072", state=tk.NORMAL)
            self.port_cb.config(state="disabled")
            self.btn_refresh.config(state=tk.DISABLED)
            self.lbl_connection.config(text="● CONECTADO", fg="#38d683")
            self.receiver.start()
        except Exception as error:
            self.is_connected = False
            if self.serial_port:
                self.serial_port.close()
            self.receiver = None
            self._on_port_selected()
            messagebox.showerror("Falha de conexão", str(error))

    def disconnect_serial(self):
        if self.receiver:
            if not self.receiver.stop():
                messagebox.showerror("Porta serial", "A leitura ainda não terminou. Aguarde antes de reconectar.")
                return False
            self._drain_receiver()
            self.receiver = None
        self.is_connected = False
        self.serial_port = None
        self.antenna_packet = None
        self.current_record = None
        self.packet_monotonic = None
        self.last_gps_data = None
        self.btn_connect.config(text="Conectar", bg="#38d683")
        self.port_cb.config(state="readonly")
        self.btn_refresh.config(state=tk.NORMAL)
        self.lbl_connection.config(text="● DESCONECTADO", fg="#94a3b5")
        self.refresh_ports()
        self.update_antenna()
        return True

    def send_command(self):
        if self.replay or not self.is_connected or not self.serial_port:
            messagebox.showerror("Telecomando", "Conecte o rádio no modo ao vivo.")
            return
        command = self.cmd_entry.get().strip()
        if not command.isdigit():
            messagebox.showerror("Telecomando", "Informe um número inteiro não negativo.")
            return
        try:
            self.serial_port.write((command + "\n").encode())
            self.mission.event("command_sent", command=command)
            self.cmd_entry.delete(0, tk.END)
        except Exception as error:
            messagebox.showerror("Falha no telecomando", str(error))

    def _drain_receiver(self):
        if not self.receiver:
            return
        while True:
            try:
                kind, value = self.receiver.messages.get_nowait()
            except Empty:
                break
            if kind == "packet":
                self._apply_record(value)
            elif kind == "error":
                self.lbl_connection.config(text="● FALHA SERIAL", fg="#ff6072")
                self.lbl_log_status.config(text=value, fg="#ff6072")
            elif kind == "disconnected":
                self.is_connected = False
                self.antenna_packet = None
                self.packet_monotonic = None
                self.btn_connect.config(text="Conectar", bg="#38d683")
                self.port_cb.config(state="readonly")
                self.btn_refresh.config(state=tk.NORMAL)
                self.lbl_connection.config(text="● DESCONECTADO", fg="#94a3b5")
                self.last_gps_data = None
                self.refresh_ports()

    def _reset_display(self):
        for history in (self.history_time, self.history_temp, self.history_alt, self.history_press, self.history_hum, self.path_coordinates):
            history.clear()
        self.sample_count = 0
        self.current_record = self.last_gps_data = self.antenna_packet = None
        self.packet_monotonic = None
        self.telemetry = {}
        for attribute in ("current_marker", "track_line"):
            item = getattr(self, attribute)
            if item:
                item.delete()
                setattr(self, attribute, None)
        self.lbl_map_coordinates.config(text="Aguardando posição GPS válida")
        self._draw_empty_charts()
        self.update_gui()
        self.update_antenna()

    def _apply_record(self, record, history=True):
        self.current_record = record
        self.packet_monotonic = time.monotonic()
        self.telemetry = dict(record["fields"], **{"Texto Bruto": record.get("callsign") or "—"})
        self.antenna_packet = (record["fields"], None)
        if self.replay:
            recorded_settings = (record.get("tracker"), record.get("orientation"))
            current = (asdict(self.tracker_position) if self.tracker_position else None,
                       list(self.antenna_orientation) if self.antenna_orientation else None)
            if current != recorded_settings:
                self._set_configuration(*recorded_settings)
        if history:
            self._record_history(record)
        self.needs_gui_update = True

    def _record_history(self, record):
        fields = record["fields"]
        self.sample_count += 1
        self.history_time.append(fields.get("Time") or record["received_at"][11:19])
        for history, key in ((self.history_temp, "T"), (self.history_alt, "Alt"), (self.history_press, "P"), (self.history_hum, "U")):
            value = fields.get(key)
            if key == "Alt" and not record["gps_valid"]:
                value = None
            history.append(value if value is not None else math.nan)
        if not record["gps_valid"]:
            self.last_gps_data = None
            return
        latitude, longitude, altitude = (fields[key] for key in ("Lat", "Lon", "Alt"))
        point = (latitude, longitude)
        if not self.path_coordinates or self.path_coordinates[-1] != point:
            self.path_coordinates.append(point)
        previous = self.last_gps_data
        if previous and 0 < record["elapsed"] - previous["elapsed"] <= 10:
            from antenna import calculate_pointing
            delta = record["elapsed"] - previous["elapsed"]
            if fields.get("Time") and previous["gps_time"]:
                def seconds(value):
                    hours, minutes, seconds = map(int, value.split(":"))
                    return hours * 3600 + minutes * 60 + seconds
                delta = (seconds(fields["Time"]) - seconds(previous["gps_time"])) % 86400
            if 0 < delta <= 10:
                move = calculate_pointing(Position(previous["lat"], previous["lon"], previous["alt"]), Position(latitude, longitude, altitude))
                self.telemetry["WindSpeed"] = f"{move.surface_distance / delta:.1f}"
                self.telemetry["WindDir"] = f"{(move.azimuth + 180) % 360:.0f}" if move.azimuth is not None else "—"
                self.telemetry["VertSpeed"] = f"{(altitude - previous['alt']) / delta:+.1f}"
        self.last_gps_data = {"lat": latitude, "lon": longitude, "alt": altitude, "elapsed": record["elapsed"], "gps_time": fields.get("Time")}

    def _packet_age(self):
        if self.replay:
            return self.replay.packet_age()
        return time.monotonic() - self.packet_monotonic if self.packet_monotonic is not None else None

    def open_replay(self):
        if self.is_connected:
            messagebox.showinfo("Reprodução", "Desconecte o rádio antes de reproduzir uma missão.")
            return
        path = self._choose_mission("Reproduzir missão")
        if not path:
            return
        if self.mission and not self.mission.flush():
            messagebox.showerror("Gravação pendente", "Aguarde a recuperação da gravação antes de entrar na reprodução.")
            return
        try:
            replay = MissionReplay(path)
        except Exception as error:
            messagebox.showerror("Não foi possível reproduzir", str(error))
            return
        if not self.replay:
            self.replay_saved_settings = self.capture_settings
        self.replay = replay
        self.replay_rendered_index = -1
        self.btn_tracker.config(state=tk.DISABLED)
        self.btn_orientation.config(state=tk.DISABLED)
        self.lbl_orientation_prompt.config(text="Antena registrada:")
        self._reset_display()
        self.lbl_mode.config(text="REPRODUÇÃO", fg="#ffb547")
        self.lbl_connection.config(text="● REPRODUÇÃO · RÁDIO DESATIVADO", fg="#ffb547")
        self.replay_bar.grid()
        self.replay_seek.configure(to=max(replay.duration, .001))
        self.replay_speed.set("1")
        self._render_replay(seek=True)
        self._on_port_selected()

    def _render_replay(self, seek=False):
        index = self.replay.tick()
        if seek:
            self._reset_display()
            for sample in self.replay.history():
                self._apply_record(sample)
        elif index != self.replay_rendered_index:
            # At high speeds include intermediate samples in the displayed route/charts.
            if index < self.replay_rendered_index:
                self._reset_display()
                self.replay_rendered_index = -1
            for sample in self.replay.records_between(self.replay_rendered_index + 1, index):
                self._apply_record(sample)
        self.sample_count = index + 1
        self.replay_rendered_index = index
        if self.needs_gui_update:
            self.update_gui()
            self.update_charts()
            self.needs_gui_update = False
        self.update_antenna()
        self.btn_play.config(text="Pausar" if self.replay.playing else "Reproduzir")
        if not self.replay_scrubbing:
            self.replay_seek.set(self.replay.position)
        def duration(seconds):
            seconds = int(seconds)
            return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
        self.lbl_replay_time.config(text=f"{duration(self.replay.position)} / {duration(self.replay.duration)}")

    def toggle_replay(self):
        if self.replay:
            self.replay.toggle()
            self._render_replay()

    def seek_replay(self, event=None):
        self.replay_scrubbing = False
        if self.replay:
            self.replay.seek(self.replay_seek.get())
            self._render_replay(seek=True)

    def exit_replay(self):
        if not self.replay:
            return
        self.replay = None
        self.btn_tracker.config(state=tk.NORMAL)
        self.btn_orientation.config(state=tk.NORMAL)
        self.lbl_orientation_prompt.config(text="Antena atual (opcional):")
        self.replay_bar.grid_remove()
        self.lbl_mode.config(text="AO VIVO", fg="#38d683")
        self.lbl_connection.config(text="● DESCONECTADO", fg="#94a3b5")
        self._set_configuration(*self.replay_saved_settings)
        self._reset_display()
        self._update_mission_health()
        self._on_port_selected()

    def _update_mission_health(self):
        if self.replay:
            self.lbl_mission_health.config(text=f"REPRODUÇÃO · {self.replay.metadata['name']} · dados históricos", fg="#ffb547")
            self.lbl_log_status.config(text="Reprodução não grava na missão", fg="#ffb547")
        elif self.mission:
            status = self.mission.snapshot()
            error = status["error"] or (self.receiver.log_error if self.receiver else None)
            text = f"{self.mission.metadata['name']} · {status['written_packets']} pacotes salvos · fila {status['buffer_bytes'] / 1048576:.1f} MB"
            if error:
                text += " · FALHA DE GRAVAÇÃO — recepção continua"
            if status["dropped_records"]:
                text += f" · PERDA: {status['dropped_packets']} pacotes / {status['dropped_raw_bytes']} bytes brutos"
            self.lbl_mission_health.config(text=text, fg="#ff6072" if error or status["dropped_records"] else "#38d683")
            self.lbl_log_status.config(text=f"Última sincronização: {status['last_sync'][11:19] + ' UTC' if status['last_sync'] else 'aguardando'}",
                                       fg="#ff6072" if error else "#94a3b5")

    def log_details(self):
        if self.replay:
            messagebox.showinfo("Reprodução", f"Missão: {self.replay.metadata['name']}\nArquivo: {self.replay.path}")
        elif self.mission:
            s = self.mission.snapshot()
            messagebox.showinfo("Gravação da missão", f"Arquivo: {self.mission.path}\nÚltima sincronização: {s['last_sync'] or '—'}\n"
                f"Fila: {s['buffer_bytes']} bytes + {s['inflight_bytes']} em escrita\nPacotes salvos: {s['written_packets']}\n"
                f"Registros descartados: {s['dropped_records']}\nPacotes descartados: {s['dropped_packets']}\n"
                f"Bytes brutos descartados: {s['dropped_raw_bytes']}\nPeríodo abrangido pelas perdas: {s['loss_start'] or '—'} até {s['loss_end'] or '—'}\n"
                f"Erro: {s['error'] or 'nenhum'}\nDescartes são internos ao PC; não medem perdas no rádio.")
        else:
            messagebox.showinfo("Missão", "Nenhuma missão aberta.")

    def _export(self, kind):
        if self.export_busy:
            return
        path = self.replay.path if self.replay else (self.mission.path if self.mission else self._choose_mission("Exportar missão"))
        if not path:
            return
        extension, label, exporter, unit = {
            "csv": (".csv", "CSV", export_csv, "pacotes"),
            "kml": (".kml", "KML (Google Earth)", export_kml, "pontos"),
            "raw": (".bin", "Serial bruta", export_raw, "bytes"),
        }[kind]
        destination = filedialog.asksaveasfilename(parent=self.root, title="Exportar dados gravados", defaultextension=extension,
                                                  filetypes=[(label, "*" + extension)])
        if not destination:
            return
        if Path(destination).resolve() in {Path(path).resolve(), Path(str(path) + "-wal").resolve(), Path(str(path) + "-shm").resolve()}:
            messagebox.showerror("Destino inválido", "Escolha um arquivo diferente do banco da missão.")
            return
        self.export_busy = True
        self.lbl_log_status.config(text="Exportando registros já gravados…")
        def work():
            try:
                count = exporter(path, destination)
                self.background_results.put(("export", f"Exportados {count} {unit}.\n{destination}"))
            except Exception as error:
                self.background_results.put(("error", str(error)))
        threading.Thread(target=work, name="mission-export", daemon=True).start()

    def save_csv(self):
        self._export("csv")

    def save_kml(self):
        self._export("kml")

    def save_raw(self):
        self._export("raw")

    def gui_updater_loop(self):
        if self.closing:
            return
        try:
            self._drain_receiver()
            if self.replay:
                self._render_replay()
            else:
                self.update_antenna()
                if self.needs_gui_update:
                    self.update_gui()
                    self.update_charts()
                    self.needs_gui_update = False
            age = self._packet_age()
            if age is None:
                self.lbl_packet_age.config(text="● Aguardando pacote", fg="#94a3b5")
            else:
                prefix = "REPRODUÇÃO" if self.replay else ("PACOTE ATRASADO" if age > 10 else "TELEMETRIA ATIVA")
                self.lbl_packet_age.config(text=f"● {prefix} · há {age:.0f} s", fg="#ff6072" if age > 10 else "#38d683")
            self._update_mission_health()
            while not self.background_results.empty():
                kind, text = self.background_results.get_nowait()
                self.export_busy = False
                (messagebox.showerror if kind == "error" else messagebox.showinfo)("Exportação", text)
        finally:
            self.root.after(500, self.gui_updater_loop)

    def close_application(self):
        if self.export_busy:
            messagebox.showinfo("Exportação em andamento", "Aguarde o término da exportação antes de fechar.")
            return
        if not self.disconnect_serial():
            return
        if self.mission and not self.mission.close():
            if not messagebox.askyesno("Dados ainda na memória", "A gravação continua falhando. Fechar agora perde os registros que ainda estão na RAM.\nFechar mesmo assim?"):
                return
            self.mission.abandon()
        self.closing = True
        owners = {}
        def collect_callbacks(widget):
            for command in widget._tclCommands or ():
                owners[command] = widget
            for child in widget.winfo_children():
                collect_callbacks(child)
        collect_callbacks(self.root)
        for job in self.root.tk.call("after", "info"):
            command = str(self.root.tk.call("after", "info", job)[0])
            if command in owners:
                owners[command].after_cancel(job)
        self.root.destroy()
