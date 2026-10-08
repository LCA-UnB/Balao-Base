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

from antenna import Position, position_from_packet
from mission import MissionWriter, export_csv, export_kml, export_raw, read_metadata
from replay import MissionReplay
from station import StationReceiver
from telemetry import MESSAGE_LIMIT, STATION_TOTALS, sanitize_message

# O bordo trata qualquer comando acima de 2000 como calibração do IMU e
# confirma com Ack = comando + 1.
IMU_CALIBRATION_COMMAND = "2500"
LOGS_DIRECTORY = Path(__file__).resolve().parents[1] / "logs"


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
        self.station_identity = None
        self.unread_messages = 0
        self.background_results = Queue()
        self.export_busy = False
        self.replay_scrubbing = False
        self.default_mission_directory = LOGS_DIRECTORY
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
        self.mission_bar = bar
        # As ações abrem ao lado do botão e não ocupam uma linha do workspace.
        self.btn_mission_toggle = tk.Button(self.header_actions, command=self.toggle_mission_buttons, bg="#1b2633",
                                            fg="#38d683", activebackground="#263547", activeforeground="#38d683",
                                            relief=tk.FLAT, font=("Segoe UI", 10, "bold"), padx=12, pady=3, cursor="hand2")
        self.btn_mission_toggle.pack(side=tk.LEFT, before=self.btn_charts)
        self.mission_buttons = tk.Menu(self.btn_mission_toggle, tearoff=False, bg="#1b2633", fg="#f2f5f8",
                                       activebackground="#263547", activeforeground="#38d683",
                                       font=("Segoe UI", 10), relief=tk.FLAT)
        for index, (title, command) in enumerate((("Nova missão", self.new_mission), ("Retomar missão", self.resume_mission),
                                                 ("Encerrar missão", self.end_mission), ("Reproduzir", self.open_replay),
                                                 ("Exportar CSV", self.save_csv), ("Exportar KML", self.save_kml),
                                                 ("Exportar bruto", self.save_raw), ("Detalhes", self.log_details))):
            def invoke(command=command):
                self.set_mission_buttons_visible(False)
                command()
            self.mission_buttons.add_command(label=title, command=invoke, columnbreak=index > 0 and index % 2 == 0)
        self.mission_buttons.bind("<Unmap>", self._on_mission_menu_closed)
        self.root.bind("<Button-1>", self._dismiss_mission_menu, add="+")
        self.root.bind("<Escape>", lambda event: self.set_mission_buttons_visible(False), add="+")
        self.set_mission_buttons_visible(False)
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

    def set_mission_buttons_visible(self, visible):
        self.mission_buttons_visible = visible
        self.btn_mission_toggle.config(text="◂  Ocultar ações" if visible else "▸  Ações da missão")
        if visible:
            button = self.btn_mission_toggle
            self.root.update_idletasks()
            # O menu se abre horizontalmente no espaço ao lado do botão.
            self.mission_buttons.tk_popup(button.winfo_rootx() + button.winfo_width() + 6, button.winfo_rooty())
            self.mission_buttons.focus_set()
            self.mission_buttons.activate(0)
        else:
            self.mission_buttons.unpost()
            self.mission_buttons.grab_release()
        self._sync_mission_bar_visibility()

    def _on_mission_menu_closed(self, event=None):
        self.mission_buttons_visible = False
        self.mission_buttons.grab_release()
        self.btn_mission_toggle.config(text="▸  Ações da missão")

    def _dismiss_mission_menu(self, event):
        if event.widget is not self.btn_mission_toggle and self.mission_buttons_visible:
            self.set_mission_buttons_visible(False)

    def _sync_mission_bar_visibility(self):
        if self.replay:
            self.mission_bar.grid()
        else:
            self.mission_bar.grid_remove()

    def toggle_mission_buttons(self):
        self.set_mission_buttons_visible(not self.mission_buttons_visible)

    def on_zoom_applied(self):
        if self.mission_buttons_visible:
            self.set_mission_buttons_visible(False)

    def _choose_mission(self, title):
        return filedialog.askopenfilename(parent=self.root, title=title,
            initialdir=str(self.default_mission_directory), filetypes=[("Missão SQLite", "*.sqlite3")])

    def _build_command_card(self, parent):
        card = self._card(parent, "Telecomando")
        card.pack(fill=tk.X, pady=(0, 9))
        for title, attribute in (("ACK recebido", "lbl_ack"),):
            row, value = self._data_row(card, title)
            row.pack(fill=tk.X, pady=(0, 7))
            setattr(self, attribute, value)
        row = tk.Frame(card, bg=card.cget("bg"))
        row.pack(fill=tk.X)
        self.cmd_entry = ttk.Entry(row, width=12, font=("Segoe UI", 12))
        self.cmd_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=5)
        tk.Button(row, text="Enviar comando", command=self.send_command, padx=16, pady=8, font=("Segoe UI", 11)).pack(side=tk.RIGHT, padx=(8, 0))
        self.btn_calibrate_imu = tk.Button(card, text="Calibrar IMU", command=self.calibrate_imu, padx=16, pady=8, font=("Segoe UI", 11))
        self.btn_calibrate_imu.pack(fill=tk.X, pady=(8, 0))

    def _build_message_card(self, parent):
        card = self._card(parent, "Mensagens via balão")
        card.pack(fill=tk.X, pady=(0, 9))
        row, self.lbl_station = self._data_row(card, "Esta estação")
        row.pack(fill=tk.X, pady=(0, 7))
        config = tk.Frame(card, bg=card.cget("bg"))
        config.pack(fill=tk.X, pady=(0, 7))
        self._label(config, "Letra", 9, "#94a3b5").pack(side=tk.LEFT)
        self.station_letter = ttk.Combobox(config, state="readonly", width=3, font=("Segoe UI", 11))
        self.station_letter.pack(side=tk.LEFT, padx=(6, 10))
        self._label(config, "de", 9, "#94a3b5").pack(side=tk.LEFT)
        self.station_total = ttk.Combobox(config, state="readonly", width=3, font=("Segoe UI", 11),
                                          values=STATION_TOTALS)
        self.station_total.pack(side=tk.LEFT, padx=(6, 0))
        self.station_total.bind("<<ComboboxSelected>>", lambda event: self._update_station_letters())
        tk.Button(config, text="Aplicar", command=self.apply_station, padx=14, pady=6, font=("Segoe UI", 11)).pack(side=tk.RIGHT)
        self._set_station_choice("A", 1)
        self.message_log = tk.Text(card, height=7, wrap=tk.WORD, state=tk.DISABLED, bg="#1b2633", fg="#f2f5f8",
                                   relief=tk.FLAT, font=("Consolas", 10), padx=6, pady=4)
        self.message_log.pack(fill=tk.X, pady=(0, 7))
        row = tk.Frame(card, bg=card.cget("bg"))
        row.pack(fill=tk.X)
        self.message_text = tk.StringVar()
        self.message_text.trace_add("write", lambda *args: self._update_message_count())
        self.message_entry = ttk.Entry(row, textvariable=self.message_text, width=12, font=("Segoe UI", 12))
        self.message_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=5)
        self.message_entry.bind("<Return>", lambda event: self.send_message())
        tk.Button(row, text="Enviar mensagem", command=self.send_message, padx=16, pady=8, font=("Segoe UI", 11)).pack(side=tk.RIGHT, padx=(8, 0))
        self.lbl_message_count = self._label(card, f"0/{MESSAGE_LIMIT}", 8, "#617186")
        self.lbl_message_count.pack(anchor=tk.E, pady=(3, 0))
        self.lbl_message_status = self._label(card, "Nenhuma mensagem enviada.", 9, "#94a3b5", justify=tk.LEFT, wraplength=320)
        self.lbl_message_status.pack(anchor=tk.W)
        card.bind("<Configure>", lambda event: self.lbl_message_status.config(wraplength=max(100, event.width - 30)))

    def _set_station_choice(self, letter, total):
        self.station_total.set(str(total))
        self._update_station_letters()
        self.station_letter.set(letter)

    def _update_station_letters(self):
        letters = tuple("ABCDEF"[:int(self.station_total.get())])
        self.station_letter.config(values=letters)
        if self.station_letter.get() not in letters:
            self.station_letter.set("A")

    def _update_message_count(self):
        size = len(sanitize_message(self.message_text.get(), limit=None))
        self.lbl_message_count.config(text=f"{size}/{MESSAGE_LIMIT}", fg="#ff6072" if size > MESSAGE_LIMIT else "#617186")

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
        self.mission = None
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
            # A identidade é do rádio: outro rádio (ou um firmware sem [ESTACAO])
            # não pode herdar a restrição de telecomando do anterior.
            self.station_identity = None
            self.lbl_station.config(text="—")
            self.btn_connect.config(text="Desconectar", bg="#ff6072", state=tk.NORMAL)
            self.port_cb.config(state="disabled")
            self.btn_refresh.config(state=tk.DISABLED)
            self.lbl_connection.config(text="● CONECTADO", fg="#38d683")
            self.receiver.start()
            # Se o rádio não reiniciar ao abrir a porta, a identidade não sai
            # no boot; a consulta garante que a interface saiba qual estação é.
            try:
                self.serial_port.write(b"ID?\n")
            except Exception:
                pass  # uma falha real da porta aparece pelo receptor
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

    def _radio_ready(self):
        if self.replay or not self.is_connected or not self.serial_port:
            messagebox.showerror("Telecomando", "Conecte o rádio no modo ao vivo.")
            return False
        # A solo recusa telecomandos fora da estação A; avisar aqui evita que o
        # operador espere um ACK que nunca virá (vale também para Calibrar IMU).
        if self.station_identity and self.station_identity[0] != "A":
            messagebox.showerror("Telecomando", "Telecomandos só podem sair da estação A.")
            return False
        return True

    def _write_command(self, command):
        try:
            self.serial_port.write((command + "\n").encode())
            self.mission.event("command_sent", command=command)
            return True
        except Exception as error:
            messagebox.showerror("Falha no telecomando", str(error))
            return False

    def send_command(self):
        if not self._radio_ready():
            return
        command = self.cmd_entry.get().strip()
        if not command.isdigit():
            messagebox.showerror("Telecomando", "Informe um número inteiro não negativo.")
            return
        if self._write_command(command):
            self.cmd_entry.delete(0, tk.END)

    def calibrate_imu(self):
        if not self._radio_ready():
            return
        if not messagebox.askyesno("Calibrar IMU",
                "A sonda vai refazer o zero de pitch, roll e yaw na posição atual.\n\n"
                "Use com a carga parada: em movimento o zero fica errado. Durante a "
                "calibração (~4 s) a sonda não transmite telemetria nem escuta comandos.\n\n"
                f"A confirmação chega como ACK {int(IMU_CALIBRATION_COMMAND) + 1}. Enviar?", parent=self.root):
            return
        self._write_command(IMU_CALIBRATION_COMMAND)

    def _write_serial(self, title, line):
        if self.replay or not self.is_connected or not self.serial_port:
            messagebox.showerror(title, "Conecte o rádio no modo ao vivo.")
            return False
        try:
            self.serial_port.write(line.encode("ascii"))
            return True
        except Exception as error:
            messagebox.showerror(f"Falha: {title.lower()}", str(error))
            return False

    def send_message(self):
        text = sanitize_message(self.message_text.get(), limit=None)
        if not text:
            messagebox.showerror("Mensagem", "Escreva a mensagem antes de enviar.")
            return
        if len(text) > MESSAGE_LIMIT:
            messagebox.showerror("Mensagem", f"A mensagem tem {len(text)} caracteres; o limite é {MESSAGE_LIMIT}.")
            return
        if self._write_serial("Mensagem", f"M {text}\n"):
            # O rascunho só sai do campo quando o rádio confirma ENFILEIRADA:
            # uma recusa (outra mensagem ainda pendente) não pode apagá-lo.
            self.mission.event("message_requested", text=text)
            self.lbl_message_status.config(text="Aguardando o rádio de solo aceitar a mensagem…", fg="#94a3b5")

    def apply_station(self):
        letter, total = self.station_letter.get(), int(self.station_total.get())
        if self._write_serial("Estação", f"ID {letter} {total}\n"):
            self.mission.event("station_requested", id=letter, total=total)

    def _append_message(self, line):
        self.message_log.config(state=tk.NORMAL)
        self.message_log.insert(tk.END, ("\n" if self.message_log.index("end-1c") != "1.0" else "") + line)
        if int(self.message_log.index("end-1c").split(".")[0]) > 200:
            self.message_log.delete("1.0", "2.0")
        self.message_log.config(state=tk.DISABLED)
        self.message_log.see(tk.END)
        if self.sidebar_tabs.index("current") != 2:
            self.unread_messages += 1
            count = str(self.unread_messages) if self.unread_messages <= 99 else "99+"
            self.sidebar_tabs.tab(2, text=f"Mensagens ({count})")

    def _apply_notice(self, notice):
        if notice["kind"] == "ack":
            self.telemetry["Ack"] = notice["value"]
            self.needs_gui_update = True
            return
        if notice["kind"] == "station":
            self.station_identity = (notice["id"], notice["total"])
            self.lbl_station.config(text=f"{notice['id']} de {notice['total']}")
            if notice["total"] in STATION_TOTALS:
                self._set_station_choice(notice["id"], notice["total"])
            return
        status, message_id, attempts = notice["status"], notice.get("id", "—"), notice.get("attempts", "?")
        if status == "received":
            sender = notice.get("from") or message_id[:1]
            own = " (esta estação)" if self.station_identity and sender == self.station_identity[0] else ""
            stamp = notice.get("time") or notice["received_at"][11:19]
            self._append_message(f"[{stamp}] {sender}{own}: {notice.get('text', '')}")
            return
        if status == "queued" and sanitize_message(self.message_text.get(), limit=None) == notice.get("text"):
            self.message_text.set("")
        reasons = {"ocupada": f"o rádio ainda aguarda a confirmação da mensagem {message_id}", "vazia": "mensagem vazia"}
        text, color = {
            "queued": (f"Mensagem {message_id} na fila do rádio de solo.", "#94a3b5"),
            "sent": (f"Mensagem {message_id} enviada ao balão (tentativa {attempts}).", "#ffb547"),
            "delivered": (f"Mensagem {message_id} repetida pelo balão.", "#38d683"),
            "failed": (f"Mensagem {message_id} sem confirmação após {attempts} tentativas.", "#ff6072"),
            "refused": (f"Mensagem recusada: {reasons.get(notice.get('reason'), notice.get('reason', '—'))}. O texto continua no campo.", "#ff6072"),
        }[status]
        self.lbl_message_status.config(text=text, fg=color)

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
            elif kind == "notice":
                self._apply_notice(value)
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
        self.current_record = self.last_gps_data = self.antenna_packet = self.sonde_fix = None
        self.packet_monotonic = None
        self.telemetry = {}
        for attribute in ("current_marker", "track_line"):
            item = getattr(self, attribute)
            if item:
                item.delete()
                setattr(self, attribute, None)
        self.lbl_map_coordinates.config(text="Aguardando posição GPS válida")
        # Mensagens de outro voo (inclusive coordenadas de recuperação) não
        # podem aparecer junto da missão atual.
        self.message_log.config(state=tk.NORMAL)
        self.message_log.delete("1.0", tk.END)
        self.message_log.config(state=tk.DISABLED)
        self.unread_messages = 0
        self.sidebar_tabs.tab(2, text="Mensagens")
        self.lbl_message_status.config(text="Nenhuma mensagem enviada.", fg="#94a3b5")
        self._draw_empty_charts()
        self.update_gui()
        self.update_antenna()

    def _apply_record(self, record, history=True):
        self.current_record = record
        self.packet_monotonic = time.monotonic()
        self.telemetry = dict(record["fields"], **{"Texto Bruto": record.get("callsign") or "—"})
        self.antenna_packet = (record["fields"], None)
        try:
            # A antena continua apontando para a última posição com fix 3D quando o GPS falha.
            fix_time = record["elapsed"] - self.replay.origin if self.replay else self.packet_monotonic
            self.sonde_fix = (position_from_packet(record["fields"]), fix_time)
        except ValueError:
            pass
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

    def _sonde_fix_age(self):
        if self.sonde_fix is None:
            return None
        return (self.replay.position if self.replay else time.monotonic()) - self.sonde_fix[1]

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
        self.lbl_connection.config(text="● REPRODUÇÃO · RÁDIO DESATIVADO", fg="#ffb547")
        self.replay_bar.grid()
        self._sync_mission_bar_visibility()
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
        self._sync_mission_bar_visibility()
        self.lbl_connection.config(text="● DESCONECTADO", fg="#94a3b5")
        self._set_configuration(*self.replay_saved_settings)
        self._reset_display()
        self._update_mission_health()
        self._on_port_selected()

    def _update_mission_health(self):
        if self.replay:
            self.lbl_log_status.config(text="Reprodução não grava na missão", fg="#ffb547")
        elif self.mission:
            status = self.mission.snapshot()
            error = status["error"] or (self.receiver.log_error if self.receiver else None)
            text = f"Última sincronização: {status['last_sync'][11:19] + ' UTC' if status['last_sync'] else 'aguardando'}"
            if error:
                text = "Falha de gravação · consulte Detalhes"
            elif status["dropped_records"]:
                text = "Perda de registros · consulte Detalhes"
            self.lbl_log_status.config(text=text, fg="#ff6072" if error or status["dropped_records"] else "#94a3b5")

    def log_details(self):
        if self.replay:
            messagebox.showinfo("Reprodução", f"Missão: {self.replay.metadata['name']}\nArquivo: {self.replay.path}")
        elif self.mission:
            s = self.mission.snapshot()
            messagebox.showinfo("Gravação da missão", f"Arquivo: {self.mission.path}\nÚltima sincronização: {s['last_sync'] or '—'}\n"
                f"Fila: {s['buffer_bytes']} bytes + {s['inflight_bytes']} em escrita\nPacotes salvos: {s['written_packets']}\n"
                f"Registros descartados: {s['dropped_records']}\nPacotes descartados: {s['dropped_packets']}\n"
                f"Bytes brutos descartados: {s['dropped_raw_bytes']}\nPeríodo abrangido pelas perdas: {s['loss_start'] or '—'} até {s['loss_end'] or '—'}\n"
                f"Erro: {s['error'] or 'nenhum'}\nCópia em texto: {self.mission.text_path}\n"
                f"Erro na cópia em texto: {s['text_error'] or 'nenhum'}"
                + (f" ({s['text_missing_bytes']} bytes só no banco)" if s['text_missing_bytes'] else "") + "\nDescartes são internos ao PC; não medem perdas no rádio.")
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
        try:
            self.default_mission_directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        destination = filedialog.asksaveasfilename(parent=self.root, title="Exportar dados gravados", defaultextension=extension,
                                                  initialdir=str(self.default_mission_directory),
                                                  initialfile=Path(path).parent.name + extension,
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
        if self.closing:
            return
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
        self.lbl_log_status.config(text="Encerrando aplicativo…")
        if self.region_download:
            self.region_download.cancel()
        if hasattr(self.map_widget, "stop_loading"):
            self.map_widget.stop_loading()
        owners = {}
        def collect_callbacks(widget):
            if isinstance(widget, (tk.Button, tk.Entry, tk.Text, ttk.Entry, ttk.Combobox, ttk.Scale)):
                widget.configure(state=tk.DISABLED)
            for command in widget._tclCommands or ():
                owners[command] = widget
            for child in widget.winfo_children():
                collect_callbacks(child)
        collect_callbacks(self.root)
        for job in self.root.tk.call("after", "info"):
            command = str(self.root.tk.call("after", "info", job)[0])
            if command in owners:
                owners[command].after_cancel(job)
        self.root.title("Encerrando — Monitor de Missão")
        self._finish_application_close()

    def _finish_application_close(self):
        # Mantém o loop Tk ativo até as requisições em andamento terminarem,
        # sem bloquear a interface em join() nem destruir imagens sob os workers.
        if getattr(self.map_widget, "loading", False) or (self.region_download and self.region_download.running):
            self.root.after(50, self._finish_application_close)
            return
        if self.tile_cache:
            self.tile_cache.close()
        self.root.quit()
        self.root.destroy()
