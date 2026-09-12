import datetime
import math
import os
import re
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import matplotlib
import serial
import serial.tools.list_ports
import tkintermapview

matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure


# Design tokens
COLOR_BG_MAIN = "#0b1017"
COLOR_BG_SURFACE = "#101720"
COLOR_BG_CARD = "#151e29"
COLOR_BG_ELEVATED = "#1b2633"
COLOR_BORDER = "#263547"
COLOR_TEXT_MAIN = "#f2f5f8"
COLOR_TEXT_MUTED = "#94a3b5"
COLOR_TEXT_SUBTLE = "#617186"
COLOR_ACCENT_GREEN = "#38d683"
COLOR_ACCENT_BLUE = "#4aa8ff"
COLOR_ACCENT_CYAN = "#27c8d9"
COLOR_WARNING = "#ffb547"
COLOR_DANGER = "#ff6072"
COLOR_GRAPH_GRID = "#253244"
COLOR_TRACK_LINE = "#ff7849"
FONT_FAMILY = "Segoe UI"
FONT_MONO = "Consolas"


class SondeTrackerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("LoRa Telemetry Ground Station — Monitor de Missão")
        self.root.geometry("1440x900")
        self.root.minsize(1080, 720)
        self.root.configure(bg=COLOR_BG_MAIN)

        self.serial_port = None
        self.is_connected = False
        self.read_thread = None
        self.current_marker = None
        self.track_line = None
        self.log_file = None
        self.log_path = None
        self.needs_gui_update = False
        self.last_packet_time = None
        self.last_gps_data = None
        self.port_devices = {}

        self.history_time = []
        self.history_temp = []
        self.history_alt = []
        self.history_press = []
        self.history_hum = []
        self.path_coordinates = []

        self.telemetry = {
            "Texto Bruto": "--", "Lat": -15.7641474, "Lon": -47.8691109,
            "Alt": 0.0, "AltB": 0.0, "Sat": 0, "Fix": 0,
            "T": 0.0, "P": 0.0, "U": 0.0, "Time": "--:--:--",
            "Pitch": 0.0, "Roll": 0.0, "Yaw": 0.0,
            "AX": 0.0, "AY": 0.0, "AZ": 0.0,
            "GX": 0.0, "GY": 0.0, "GZ": 0.0,
            "MX": 0.0, "MY": 0.0, "MZ": 0.0,
            "RSSI": 0, "SNR": 0,
            "WindSpeed": "--", "WindDir": "--", "VertSpeed": "--"
        }

        self._configure_styles()
        self.setup_ui()
        self.root.after(1000, self.gui_updater_loop)
        self.root.after(2000, self._poll_usb_ports)

    def _configure_styles(self):
        self.style = ttk.Style()
        self.style.theme_use("default")
        self.style.configure(
            "Telemetry.TCombobox",
            fieldbackground=COLOR_BG_ELEVATED,
            background=COLOR_BG_ELEVATED,
            foreground=COLOR_TEXT_MAIN,
            selectbackground=COLOR_BG_ELEVATED,
            selectforeground=COLOR_TEXT_MAIN,
            arrowcolor=COLOR_TEXT_MUTED,
            bordercolor=COLOR_BORDER,
            lightcolor=COLOR_BORDER,
            darkcolor=COLOR_BORDER,
            padding=7,
        )
        self.style.map(
            "Telemetry.TCombobox",
            fieldbackground=[("readonly", COLOR_BG_ELEVATED), ("disabled", COLOR_BG_CARD)],
            foreground=[("disabled", COLOR_TEXT_SUBTLE)],
        )
        self.style.configure(
            "Telemetry.Vertical.TScrollbar",
            background=COLOR_BG_ELEVATED,
            troughcolor=COLOR_BG_SURFACE,
            bordercolor=COLOR_BG_SURFACE,
            arrowcolor=COLOR_TEXT_MUTED,
        )

    def _label(self, parent, text, size=10, color=COLOR_TEXT_MAIN, weight="normal", **kwargs):
        return tk.Label(
            parent,
            text=text,
            bg=kwargs.pop("bg", parent.cget("bg")),
            fg=color,
            font=(FONT_FAMILY, size, weight),
            **kwargs,
        )

    def _card(self, parent, title, subtitle=None):
        card = tk.Frame(
            parent,
            bg=COLOR_BG_CARD,
            highlightbackground=COLOR_BORDER,
            highlightthickness=1,
            padx=14,
            pady=12,
        )
        header = tk.Frame(card, bg=COLOR_BG_CARD)
        header.pack(fill=tk.X, pady=(0, 10))
        self._label(header, title.upper(), 9, COLOR_TEXT_MUTED, "bold").pack(side=tk.LEFT)
        if subtitle:
            self._label(header, subtitle, 8, COLOR_TEXT_SUBTLE).pack(side=tk.RIGHT)
        return card

    def _metric(self, parent, title, initial="—", accent=COLOR_TEXT_MAIN):
        frame = tk.Frame(parent, bg=COLOR_BG_ELEVATED, padx=10, pady=9)
        self._label(frame, title, 8, COLOR_TEXT_MUTED).pack(anchor=tk.W)
        value = tk.Label(
            frame,
            text=initial,
            bg=COLOR_BG_ELEVATED,
            fg=accent,
            font=(FONT_MONO, 14, "bold"),
            anchor=tk.W,
        )
        value.pack(anchor=tk.W, pady=(3, 0))
        return frame, value

    def _data_row(self, parent, label, initial="—", color=COLOR_TEXT_MAIN):
        row = tk.Frame(parent, bg=COLOR_BG_CARD)
        self._label(row, label, 9, COLOR_TEXT_MUTED).pack(side=tk.LEFT)
        value = tk.Label(
            row,
            text=initial,
            bg=COLOR_BG_CARD,
            fg=color,
            font=(FONT_MONO, 10, "bold"),
            anchor=tk.E,
        )
        value.pack(side=tk.RIGHT)
        return row, value

    def setup_ui(self):
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_columnconfigure(0, weight=1)
        self._build_header()
        self._build_workspace()
        self._build_charts()

    def _build_header(self):
        header = tk.Frame(self.root, bg=COLOR_BG_SURFACE, height=76)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(1, weight=1)

        identity = tk.Frame(header, bg=COLOR_BG_SURFACE, padx=20)
        identity.grid(row=0, column=0, sticky="nsw")
        self._label(identity, "LASE  /  ESTAÇÃO DE SOLO", 8, COLOR_TEXT_MUTED, "bold").pack(anchor=tk.W, pady=(14, 2))
        self.lbl_callsign = self._label(identity, "MISSÃO  —", 17, COLOR_TEXT_MAIN, "bold")
        self.lbl_callsign.pack(anchor=tk.W)

        status_area = tk.Frame(header, bg=COLOR_BG_SURFACE)
        status_area.grid(row=0, column=1, sticky="nsw", padx=(24, 10))
        self.lbl_connection = self._label(status_area, "●  DESCONECTADO", 9, COLOR_TEXT_MUTED, "bold")
        self.lbl_connection.pack(anchor=tk.W, pady=(17, 3))
        self.lbl_log_status = self._label(status_area, "Log inativo", 8, COLOR_TEXT_SUBTLE)
        self.lbl_log_status.pack(anchor=tk.W)

        controls = tk.Frame(header, bg=COLOR_BG_SURFACE, padx=20)
        controls.grid(row=0, column=2, sticky="nse")
        port_group = tk.Frame(controls, bg=COLOR_BG_SURFACE)
        port_group.pack(side=tk.LEFT, pady=14)
        self.lbl_port_status = self._label(
            port_group, "PORTA USB", 8, COLOR_TEXT_MUTED, "bold"
        )
        self.lbl_port_status.pack(anchor=tk.W, pady=(0, 4))
        port_row = tk.Frame(port_group, bg=COLOR_BG_SURFACE)
        port_row.pack()
        self.port_cb = ttk.Combobox(
            port_row, state="readonly", width=20, font=(FONT_FAMILY, 9),
            style="Telemetry.TCombobox",
        )
        self.port_cb.pack(side=tk.LEFT)
        self.port_cb.bind("<<ComboboxSelected>>", self._on_port_selected)
        self.btn_refresh = tk.Button(
            port_row, text="↻", command=self.refresh_ports,
            bg=COLOR_BG_ELEVATED, fg=COLOR_TEXT_MAIN,
            activebackground=COLOR_BORDER, activeforeground=COLOR_TEXT_MAIN,
            relief=tk.FLAT, width=3, font=(FONT_FAMILY, 11, "bold"), cursor="hand2",
        )
        self.btn_refresh.pack(side=tk.LEFT, padx=(6, 0), fill=tk.Y)
        self.btn_connect = tk.Button(
            controls, text="Conectar", command=self.toggle_connection,
            bg=COLOR_ACCENT_GREEN, fg="#07140d",
            activebackground="#61e59c", activeforeground="#07140d",
            disabledforeground=COLOR_TEXT_SUBTLE, relief=tk.FLAT,
            font=(FONT_FAMILY, 10, "bold"), padx=20, pady=10, cursor="hand2",
        )
        self.btn_connect.pack(side=tk.LEFT, padx=(12, 0), pady=(27, 14))
        self.refresh_ports()

    def _build_workspace(self):
        workspace = tk.Frame(self.root, bg=COLOR_BG_MAIN, padx=14, pady=12)
        workspace.grid(row=1, column=0, sticky="nsew")
        workspace.grid_rowconfigure(0, weight=1)
        workspace.grid_columnconfigure(0, weight=1)

        map_card = tk.Frame(
            workspace, bg=COLOR_BG_CARD,
            highlightbackground=COLOR_BORDER, highlightthickness=1,
        )
        map_card.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        map_card.grid_rowconfigure(1, weight=1)
        map_card.grid_columnconfigure(0, weight=1)

        map_header = tk.Frame(map_card, bg=COLOR_BG_CARD, height=52, padx=14)
        map_header.grid(row=0, column=0, sticky="ew")
        map_header.grid_propagate(False)
        map_header.grid_columnconfigure(1, weight=1)
        title_group = tk.Frame(map_header, bg=COLOR_BG_CARD)
        title_group.grid(row=0, column=0, sticky="w", pady=8)
        self._label(title_group, "TRAJETÓRIA EM TEMPO REAL", 9, COLOR_TEXT_MAIN, "bold").pack(anchor=tk.W)
        self.lbl_map_coordinates = self._label(title_group, "Posição inicial · Lago Norte, Brasília", 8, COLOR_TEXT_MUTED)
        self.lbl_map_coordinates.pack(anchor=tk.W, pady=(2, 0))

        layer_group = tk.Frame(map_header, bg=COLOR_BG_CARD)
        layer_group.grid(row=0, column=2, sticky="e", pady=8)
        self._label(layer_group, "MAPA", 8, COLOR_TEXT_MUTED, "bold").pack(side=tk.LEFT, padx=(0, 8))
        self.map_layer_cb = ttk.Combobox(
            layer_group, state="readonly", width=14, font=(FONT_FAMILY, 9),
            style="Telemetry.TCombobox", values=["Padrão", "Satélite", "Topográfico"],
        )
        self.map_layer_cb.pack(side=tk.LEFT)
        self.map_layer_cb.current(0)
        self.map_layer_cb.bind("<<ComboboxSelected>>", self.change_map_layer)

        self.map_widget = tkintermapview.TkinterMapView(map_card, corner_radius=0)
        self.map_widget.grid(row=1, column=0, sticky="nsew")
        self.map_widget.set_position(self.telemetry["Lat"], self.telemetry["Lon"])
        self.map_widget.set_zoom(14)

        sidebar_shell = tk.Frame(
            workspace, bg=COLOR_BG_SURFACE, width=370,
            highlightbackground=COLOR_BORDER, highlightthickness=1,
        )
        sidebar_shell.grid(row=0, column=1, sticky="nsew")
        sidebar_shell.grid_propagate(False)
        sidebar_shell.grid_rowconfigure(1, weight=1)
        sidebar_shell.grid_columnconfigure(0, weight=1)
        self.sidebar_shell = sidebar_shell

        side_header = tk.Frame(sidebar_shell, bg=COLOR_BG_SURFACE, padx=14, pady=11)
        side_header.grid(row=0, column=0, sticky="ew", columnspan=2)
        self._label(side_header, "TELEMETRIA DA MISSÃO", 10, COLOR_TEXT_MAIN, "bold").pack(side=tk.LEFT)
        self.lbl_sample_count = self._label(side_header, "0 amostras", 8, COLOR_TEXT_MUTED)
        self.lbl_sample_count.pack(side=tk.RIGHT)

        sidebar_canvas = tk.Canvas(sidebar_shell, bg=COLOR_BG_SURFACE, bd=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            sidebar_shell, orient=tk.VERTICAL, command=sidebar_canvas.yview,
            style="Telemetry.Vertical.TScrollbar",
        )
        self.sidebar_content = tk.Frame(sidebar_canvas, bg=COLOR_BG_SURFACE, padx=10, pady=2)
        sidebar_window = sidebar_canvas.create_window((0, 0), window=self.sidebar_content, anchor="nw")
        sidebar_canvas.configure(yscrollcommand=scrollbar.set)
        sidebar_canvas.grid(row=1, column=0, sticky="nsew")
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.sidebar_content.bind(
            "<Configure>", lambda event: sidebar_canvas.configure(scrollregion=sidebar_canvas.bbox("all"))
        )
        sidebar_canvas.bind(
            "<Configure>", lambda event: sidebar_canvas.itemconfigure(sidebar_window, width=event.width)
        )
        self.sidebar_canvas = sidebar_canvas
        self.root.bind_all("<MouseWheel>", self._on_sidebar_mousewheel, add="+")
        self.root.bind_all("<Button-4>", self._on_sidebar_mousewheel, add="+")
        self.root.bind_all("<Button-5>", self._on_sidebar_mousewheel, add="+")

        self._build_flight_card()
        self._build_link_card()
        self._build_environment_card()
        self._build_imu_card()

    def _on_sidebar_mousewheel(self, event):
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if widget == self.sidebar_shell:
                if getattr(event, "num", None) == 4:
                    direction = -1
                elif getattr(event, "num", None) == 5:
                    direction = 1
                else:
                    direction = -1 if event.delta > 0 else 1
                self.sidebar_canvas.yview_scroll(direction, "units")
                return "break"
            widget = widget.master
        return None

    def _build_flight_card(self):
        card = self._card(self.sidebar_content, "Status de voo", "dados derivados")
        card.pack(fill=tk.X, pady=(0, 9))
        metrics = tk.Frame(card, bg=COLOR_BG_CARD)
        metrics.pack(fill=tk.X)
        for column in range(2):
            metrics.grid_columnconfigure(column, weight=1, uniform="flight")
        frame, self.lbl_alt = self._metric(metrics, "ALTITUDE GPS", "— m", COLOR_ACCENT_CYAN)
        frame.grid(row=0, column=0, sticky="ew", padx=(0, 4), pady=(0, 7))
        frame, self.lbl_vert_speed = self._metric(metrics, "VELOCIDADE VERTICAL", "— m/s", COLOR_ACCENT_BLUE)
        frame.grid(row=0, column=1, sticky="ew", padx=(4, 0), pady=(0, 7))
        frame, self.lbl_wind_speed = self._metric(metrics, "VENTO ESTIMADO", "— m/s", COLOR_ACCENT_GREEN)
        frame.grid(row=1, column=0, sticky="ew", padx=(0, 4))
        frame, self.lbl_wind_dir = self._metric(metrics, "DIREÇÃO DO VENTO", "—°", COLOR_ACCENT_GREEN)
        frame.grid(row=1, column=1, sticky="ew", padx=(4, 0))

    def _build_link_card(self):
        card = self._card(self.sidebar_content, "GPS e link LoRa")
        card.pack(fill=tk.X, pady=(0, 9))
        metrics = tk.Frame(card, bg=COLOR_BG_CARD)
        metrics.pack(fill=tk.X, pady=(0, 10))
        for column in range(2):
            metrics.grid_columnconfigure(column, weight=1, uniform="link")
        frame, self.lbl_rssi = self._metric(metrics, "RSSI", "— dBm", COLOR_ACCENT_BLUE)
        frame.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        frame, self.lbl_snr = self._metric(metrics, "SNR", "— dB", COLOR_ACCENT_BLUE)
        frame.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.lbl_packet_age = self._label(card, "●  Aguardando primeiro pacote", 9, COLOR_TEXT_MUTED, "bold")
        self.lbl_packet_age.pack(anchor=tk.W, pady=(0, 10))

        rows = [
            ("Hora UTC", "lbl_time"),
            ("Fix / satélites", "lbl_sat"),
            ("Latitude", "lbl_lat"),
            ("Longitude", "lbl_lon"),
            ("Altitude barométrica", "lbl_altb"),
        ]
        for index, (title, attribute) in enumerate(rows):
            row, value = self._data_row(card, title)
            row.pack(fill=tk.X, pady=(0, 7 if index < len(rows) - 1 else 0))
            setattr(self, attribute, value)

    def _build_environment_card(self):
        card = self._card(self.sidebar_content, "Telemetria ambiental", "PTU")
        card.pack(fill=tk.X, pady=(0, 9))
        metrics = tk.Frame(card, bg=COLOR_BG_CARD)
        metrics.pack(fill=tk.X)
        for column in range(3):
            metrics.grid_columnconfigure(column, weight=1, uniform="environment")
        frame, self.lbl_temp = self._metric(metrics, "TEMP.", "— °C", COLOR_WARNING)
        frame.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        frame, self.lbl_press = self._metric(metrics, "PRESSÃO", "— hPa", COLOR_ACCENT_GREEN)
        frame.grid(row=0, column=1, sticky="ew", padx=3)
        frame, self.lbl_hum = self._metric(metrics, "UMIDADE", "— %", COLOR_ACCENT_CYAN)
        frame.grid(row=0, column=2, sticky="ew", padx=(3, 0))

    def _build_imu_card(self):
        card = self._card(self.sidebar_content, "Dinâmica de voo", "IMU bruta")
        card.pack(fill=tk.X, pady=(0, 9))
        table = tk.Frame(card, bg=COLOR_BG_CARD)
        table.pack(fill=tk.X)
        table.grid_columnconfigure(0, weight=2)
        for column in range(1, 4):
            table.grid_columnconfigure(column, weight=1, uniform="imu")
        for column, title in enumerate(("", "X / P", "Y / R", "Z / Y")):
            self._label(table, title, 8, COLOR_TEXT_SUBTLE, "bold").grid(
                row=0, column=column, sticky="w", pady=(0, 6)
            )
        rows = [
            ("Atitude", ("lbl_pitch", "lbl_roll", "lbl_yaw"), "—°"),
            ("Aceleração", ("lbl_ax", "lbl_ay", "lbl_az"), "—"),
            ("Giroscópio", ("lbl_gx", "lbl_gy", "lbl_gz"), "—"),
            ("Magnetômetro", ("lbl_mx", "lbl_my", "lbl_mz"), "—"),
        ]
        for row_index, (title, attributes, initial) in enumerate(rows, start=1):
            self._label(table, title, 8, COLOR_TEXT_MUTED).grid(
                row=row_index, column=0, sticky="w", pady=4
            )
            for column, attribute in enumerate(attributes, start=1):
                value = tk.Label(
                    table, text=initial, bg=COLOR_BG_CARD, fg=COLOR_TEXT_MAIN,
                    font=(FONT_MONO, 9, "bold"), anchor=tk.W,
                )
                value.grid(row=row_index, column=column, sticky="w", pady=4)
                setattr(self, attribute, value)

    def _build_charts(self):
        charts_card = tk.Frame(
            self.root, bg=COLOR_BG_CARD, height=250,
            highlightbackground=COLOR_BORDER, highlightthickness=1,
        )
        charts_card.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 14))
        charts_card.grid_propagate(False)
        charts_card.grid_rowconfigure(1, weight=1)
        charts_card.grid_columnconfigure(0, weight=1)
        header = tk.Frame(charts_card, bg=COLOR_BG_CARD, padx=14, pady=8)
        header.grid(row=0, column=0, sticky="ew")
        self._label(header, "TENDÊNCIAS DA MISSÃO", 9, COLOR_TEXT_MAIN, "bold").pack(side=tk.LEFT)
        self._label(header, "Eixo temporal: hora UTC do GPS", 8, COLOR_TEXT_MUTED).pack(side=tk.RIGHT)

        self.fig = Figure(figsize=(13.5, 2.2), dpi=100)
        self.fig.patch.set_facecolor(COLOR_BG_CARD)
        self.canvas = FigureCanvasTkAgg(self.fig, master=charts_card)
        self.canvas.get_tk_widget().configure(bg=COLOR_BG_CARD, highlightthickness=0)
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew", padx=7, pady=(0, 5))
        self._draw_empty_charts()

    def _draw_empty_charts(self):
        self.fig.clear()
        titles = ("Altitude · m", "Temperatura · °C  /  Umidade · %", "Pressão · hPa")
        for index, title in enumerate(titles, start=1):
            axis = self.fig.add_subplot(1, 3, index)
            self._style_chart_axis(axis, title)
            axis.text(
                0.5, 0.48, "Aguardando telemetria", transform=axis.transAxes,
                ha="center", va="center", color=COLOR_TEXT_SUBTLE, fontsize=8,
            )
            axis.set_xticks([])
            axis.set_yticks([])
        self.fig.tight_layout(pad=1.1, w_pad=2.0)
        self.canvas.draw_idle()

    def _style_chart_axis(self, axis, title):
        axis.set_facecolor(COLOR_BG_MAIN)
        axis.set_title(title, fontsize=9, fontweight="bold", color=COLOR_TEXT_MAIN, loc="left", pad=8)
        axis.tick_params(axis="both", colors=COLOR_TEXT_MUTED, labelsize=7)
        axis.grid(True, color=COLOR_GRAPH_GRID, linestyle="-", alpha=0.55)
        for spine in axis.spines.values():
            spine.set_visible(False)

    def change_map_layer(self, event=None):
        layer = self.map_layer_cb.get()
        if layer == "Padrão":
            self.map_widget.set_tile_server("https://a.tile.openstreetmap.org/{z}/{x}/{y}.png")
        elif layer == "Satélite":
            self.map_widget.set_tile_server("https://mt0.google.com/vt/lyrs=s&x={x}&y={y}&z={z}")
        elif layer == "Topográfico":
            self.map_widget.set_tile_server("https://a.tile.opentopomap.org/{z}/{x}/{y}.png")

    @staticmethod
    def _is_usb_serial_port(port):
        if port.vid is not None or port.pid is not None:
            return True

        metadata = " ".join(
            str(value or "")
            for value in (port.hwid, port.description, port.manufacturer)
        ).upper()
        if "USB" in metadata or "VID:PID" in metadata:
            return True

        device = port.device.lower()
        usb_device_markers = (
            "/dev/ttyusb",
            "/dev/ttyacm",
            "/dev/tty.usb",
            "/dev/cu.usb",
            "/dev/cu.slab",
            "/dev/cu.wchusb",
        )
        return device.startswith(usb_device_markers)

    @classmethod
    def _available_usb_ports(cls):
        return sorted(
            (
                port
                for port in serial.tools.list_ports.comports()
                if cls._is_usb_serial_port(port)
            ),
            key=lambda port: port.device,
        )

    @staticmethod
    def _port_display_name(port):
        description = (port.description or "").strip()
        if not description or description.lower() == "n/a":
            return port.device
        return f"{port.device} · {description}"

    def _on_port_selected(self, event=None):
        if not self.is_connected:
            selected = self.port_cb.get()
            self.btn_connect.config(
                state=tk.NORMAL if selected in self.port_devices else tk.DISABLED
            )

    def _poll_usb_ports(self):
        if not self.is_connected:
            self.refresh_ports()
        self.root.after(2000, self._poll_usb_ports)

    def refresh_ports(self):
        previous_device = self.port_devices.get(self.port_cb.get())
        ports = self._available_usb_ports()
        labels = [self._port_display_name(port) for port in ports]
        self.port_devices = {
            label: port.device for label, port in zip(labels, ports)
        }
        self.port_cb["values"] = labels

        previous_label = next(
            (
                label
                for label, device in self.port_devices.items()
                if device == previous_device
            ),
            None,
        )
        if previous_label:
            self.port_cb.set(previous_label)
        elif len(labels) == 1:
            self.port_cb.current(0)
        elif labels:
            self.port_cb.set("Selecione uma porta USB")
        else:
            self.port_cb.set("Nenhum dispositivo USB")

        if len(labels) == 1:
            self.lbl_port_status.config(text="PORTA USB · DETECTADA", fg=COLOR_ACCENT_GREEN)
        elif labels:
            self.lbl_port_status.config(
                text=f"PORTA USB · {len(labels)} DISPONÍVEIS", fg=COLOR_ACCENT_BLUE
            )
        else:
            self.lbl_port_status.config(text="PORTA USB · NÃO DETECTADA", fg=COLOR_TEXT_MUTED)
        self._on_port_selected()

    def toggle_connection(self):
        if not self.is_connected:
            port = self.port_devices.get(self.port_cb.get())
            if not port:
                messagebox.showerror("Erro", "Selecione uma porta USB disponível.")
                return
            try:
                self.serial_port = serial.Serial(port, 115200, timeout=1)
                self.is_connected = True
                self.btn_connect.config(
                    text="Desconectar", bg=COLOR_DANGER, fg="white",
                    activebackground="#ff8190", activeforeground="white",
                )
                self.port_cb.config(state="disabled")
                self.btn_refresh.config(state=tk.DISABLED)
                self.lbl_connection.config(text="●  CONECTADO", fg=COLOR_ACCENT_GREEN)

                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                self.log_path = os.path.abspath(f"telemetria_{timestamp}.txt")
                self.log_file = open(self.log_path, "a", encoding="utf-8")
                self.lbl_log_status.config(
                    text=f"● Gravando {os.path.basename(self.log_path)}", fg=COLOR_ACCENT_BLUE,
                )
                self.read_thread = threading.Thread(target=self.read_serial_data, daemon=True)
                self.read_thread.start()
            except Exception as error:
                self.disconnect_serial()
                messagebox.showerror("Falha de conexão", str(error))
        else:
            self.disconnect_serial()

    def disconnect_serial(self):
        self.is_connected = False
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
        if self.log_file and not self.log_file.closed:
            self.log_file.close()
        self.btn_connect.config(
            text="Conectar", bg=COLOR_ACCENT_GREEN, fg="#07140d",
            activebackground="#61e59c", activeforeground="#07140d",
        )
        self.port_cb.config(state="readonly")
        self.btn_refresh.config(state=tk.NORMAL)
        self.lbl_connection.config(text="●  DESCONECTADO", fg=COLOR_TEXT_MUTED)
        if self.log_path:
            self.lbl_log_status.config(
                text=f"Log salvo · {os.path.basename(self.log_path)}", fg=COLOR_TEXT_MUTED,
            )
        else:
            self.lbl_log_status.config(text="Log inativo", fg=COLOR_TEXT_SUBTLE)
        self.refresh_ports()

    def read_serial_data(self):
        while self.is_connected and self.serial_port.is_open:
            try:
                line = self.serial_port.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                if self.log_file and not self.log_file.closed:
                    self.log_file.write(line + "\n")
                    self.log_file.flush()

                if "Texto Bruto:" in line:
                    self.telemetry["Texto Bruto"] = line.split(":", 1)[1].strip()
                elif "-------" in line:
                    call_match = re.search(r"-------\s*([\w\d]+)\s*-------", line)
                    if call_match:
                        self.telemetry["Texto Bruto"] = call_match.group(1)
                elif "RSSI:" in line and "SNR:" in line:
                    match_rssi = re.search(r"RSSI:\s*([-\d]+)", line)
                    match_snr = re.search(r"SNR:\s*([-\d]+)", line)
                    if match_rssi:
                        self.telemetry["RSSI"] = match_rssi.group(1)
                    if match_snr:
                        self.telemetry["SNR"] = match_snr.group(1)
                elif ":" in line:
                    key, value = line.split(":", 1)
                    self.telemetry[key.strip()] = value.strip()
                    if key.strip() == "MZ":
                        self.append_to_history()
                        self.last_packet_time = datetime.datetime.now()
                        self.needs_gui_update = True
            except Exception as error:
                print(f"Erro no processador serial: {error}")
                break

    def calculate_flight_dynamics(self, lat2, lon2, alt2, time2_str):
        if not self.last_gps_data:
            return None
        try:
            lat1 = self.last_gps_data["lat"]
            lon1 = self.last_gps_data["lon"]
            alt1 = self.last_gps_data["alt"]
            time1 = datetime.datetime.strptime(self.last_gps_data["time"], "%H:%M:%S")
            time2 = datetime.datetime.strptime(time2_str, "%H:%M:%S")
            delta_time = (time2 - time1).total_seconds()
            if delta_time <= 0:
                delta_time = 1.0

            vertical_speed = (alt2 - alt1) / delta_time
            earth_radius = 6371000.0
            phi1, phi2 = math.radians(lat1), math.radians(lat2)
            delta_phi = math.radians(lat2 - lat1)
            delta_lon = math.radians(lon2 - lon1)
            haversine = (
                math.sin(delta_phi / 2) ** 2
                + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lon / 2) ** 2
            )
            central_angle = 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))
            wind_speed = (earth_radius * central_angle) / delta_time
            y_axis = math.sin(delta_lon) * math.cos(phi2)
            x_axis = (
                math.cos(phi1) * math.sin(phi2)
                - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lon)
            )
            heading = (math.degrees(math.atan2(y_axis, x_axis)) + 360) % 360
            return wind_speed, (heading + 180) % 360, vertical_speed
        except Exception:
            return None

    def append_to_history(self):
        current_time = self.telemetry.get("Time", datetime.datetime.now().strftime("%H:%M:%S"))
        try:
            temperature = float(self.telemetry.get("T", 0.0))
            altitude = float(self.telemetry.get("Alt", 0.0))
            pressure = float(self.telemetry.get("P", 0.0))
            humidity = float(self.telemetry.get("U", 0.0))
            latitude = float(self.telemetry.get("Lat", 0.0))
            longitude = float(self.telemetry.get("Lon", 0.0))

            if not self.history_time or self.history_time[-1] != current_time:
                self.history_time.append(current_time)
                self.history_temp.append(temperature)
                self.history_alt.append(altitude)
                self.history_press.append(pressure)
                self.history_hum.append(humidity)

            if latitude != 0.0 and longitude != 0.0:
                coordinate = (latitude, longitude)
                if not self.path_coordinates or self.path_coordinates[-1] != coordinate:
                    self.path_coordinates.append(coordinate)
                    dynamics = self.calculate_flight_dynamics(latitude, longitude, altitude, current_time)
                    if dynamics:
                        wind_speed, wind_direction, vertical_speed = dynamics
                        self.telemetry["WindSpeed"] = f"{wind_speed:.1f}"
                        self.telemetry["WindDir"] = f"{wind_direction:.0f}"
                        sign = "+" if vertical_speed >= 0 else ""
                        self.telemetry["VertSpeed"] = f"{sign}{vertical_speed:.1f}"
                    self.last_gps_data = {
                        "lat": latitude, "lon": longitude,
                        "alt": altitude, "time": current_time,
                    }
        except ValueError:
            pass

    def gui_updater_loop(self):
        try:
            if self.last_packet_time:
                elapsed = (datetime.datetime.now() - self.last_packet_time).total_seconds()
                if elapsed > 10:
                    color, status = COLOR_DANGER, "PACOTE ATRASADO"
                elif elapsed > 5:
                    color, status = COLOR_WARNING, "SINAL INSTÁVEL"
                else:
                    color, status = COLOR_ACCENT_GREEN, "TELEMETRIA ATIVA"
                self.lbl_packet_age.config(text=f"●  {status} · há {int(elapsed)} s", fg=color)
            else:
                self.lbl_packet_age.config(text="●  Aguardando primeiro pacote", fg=COLOR_TEXT_MUTED)

            if self.needs_gui_update:
                self.update_gui()
                self.update_charts()
                self.needs_gui_update = False
        except Exception as error:
            print(f"Instabilidade capturada no ciclo UI: {error}")
        finally:
            self.root.after(1000, self.gui_updater_loop)

    def update_charts(self):
        if not self.history_time:
            return
        try:
            self.fig.clear()
            altitude_axis = self.fig.add_subplot(1, 3, 1)
            environment_axis = self.fig.add_subplot(1, 3, 2)
            pressure_axis = self.fig.add_subplot(1, 3, 3)
            indices = list(range(len(self.history_time)))
            # Mantém no máximo quatro horários visíveis por gráfico, mesmo em janelas estreitas.
            step = max(1, math.ceil(len(self.history_time) / 4))
            tick_indices = indices[::step]
            tick_labels = [self.history_time[index] for index in tick_indices]

            for axis, title in (
                (altitude_axis, "Altitude · m"),
                (environment_axis, "Temperatura · °C  /  Umidade · %"),
                (pressure_axis, "Pressão · hPa"),
            ):
                self._style_chart_axis(axis, title)
                axis.set_xticks(tick_indices)
                axis.set_xticklabels(tick_labels, rotation=0, ha="center", fontsize=7)

            altitude_axis.plot(indices, self.history_alt, color=COLOR_ACCENT_CYAN, linewidth=1.8)
            altitude_axis.fill_between(indices, self.history_alt, color=COLOR_ACCENT_CYAN, alpha=0.08)
            environment_axis.plot(
                indices, self.history_temp, color=COLOR_WARNING,
                linewidth=1.7, label="Temperatura",
            )
            environment_axis.plot(
                indices, self.history_hum, color=COLOR_ACCENT_CYAN,
                linewidth=1.4, linestyle="--", label="Umidade",
            )
            environment_axis.legend(
                loc="upper right", fontsize=6, facecolor=COLOR_BG_CARD,
                edgecolor="none", labelcolor=COLOR_TEXT_MAIN,
            )
            pressure_axis.plot(indices, self.history_press, color=COLOR_ACCENT_GREEN, linewidth=1.8)
            pressure_axis.fill_between(indices, self.history_press, color=COLOR_ACCENT_GREEN, alpha=0.07)
            self.fig.tight_layout(pad=1.1, w_pad=2.0)
            self.canvas.draw_idle()
        except Exception as error:
            print(f"Falha na renderização do canvas: {error}")

    def update_gui(self):
        callsign = self.telemetry.get("Texto Bruto", "--")
        self.lbl_callsign.config(text=f"MISSÃO  {callsign}")
        self.lbl_rssi.config(text=f"{self.telemetry.get('RSSI', '—')} dBm")
        self.lbl_snr.config(text=f"{self.telemetry.get('SNR', '—')} dB")

        try:
            latitude = float(self.telemetry.get("Lat", 0))
            longitude = float(self.telemetry.get("Lon", 0))
            latitude_text = f"{latitude:.7f}" if latitude != 0.0 else "—"
            longitude_text = f"{longitude:.7f}" if longitude != 0.0 else "—"
        except ValueError:
            latitude = longitude = 0.0
            latitude_text = longitude_text = "—"

        fix_value = str(self.telemetry.get("Fix", "—"))
        fix_name = {"0": "sem fix", "2": "2D", "3": "3D", "5": "tempo"}.get(
            fix_value, "desconhecido"
        )
        self.lbl_time.config(text=self.telemetry.get("Time", "--:--:--"))
        self.lbl_lat.config(text=latitude_text)
        self.lbl_lon.config(text=longitude_text)
        self.lbl_alt.config(text=f"{self.telemetry.get('Alt', '—')} m")
        self.lbl_altb.config(text=f"{self.telemetry.get('AltB', '—')} m")
        self.lbl_sat.config(text=f"{fix_value} · {fix_name}  /  {self.telemetry.get('Sat', '—')} sats")
        self.lbl_wind_speed.config(text=f"{self.telemetry.get('WindSpeed', '—')} m/s")
        self.lbl_wind_dir.config(text=f"{self.telemetry.get('WindDir', '—')}°")
        self.lbl_vert_speed.config(text=f"{self.telemetry.get('VertSpeed', '—')} m/s")
        self.lbl_temp.config(text=f"{self.telemetry.get('T', '—')} °C")
        self.lbl_press.config(text=f"{self.telemetry.get('P', '—')} hPa")
        self.lbl_hum.config(text=f"{self.telemetry.get('U', '—')} %")
        self.lbl_pitch.config(text=f"{self.telemetry.get('Pitch', '—')}°")
        self.lbl_roll.config(text=f"{self.telemetry.get('Roll', '—')}°")
        self.lbl_yaw.config(text=f"{self.telemetry.get('Yaw', '—')}°")
        for attribute, key in (
            ("lbl_ax", "AX"), ("lbl_ay", "AY"), ("lbl_az", "AZ"),
            ("lbl_gx", "GX"), ("lbl_gy", "GY"), ("lbl_gz", "GZ"),
            ("lbl_mx", "MX"), ("lbl_my", "MY"), ("lbl_mz", "MZ"),
        ):
            getattr(self, attribute).config(text=self.telemetry.get(key, "—"))
        self.lbl_sample_count.config(text=f"{len(self.history_time)} amostras")

        if latitude != 0.0 and longitude != 0.0:
            self.lbl_map_coordinates.config(text=f"{latitude_text}, {longitude_text}")
            altitude = self.telemetry.get("Alt", "—")
            gps_time = self.telemetry.get("Time", "--:--:--")
            marker_text = (
                f"ID: {callsign}\nLat: {latitude:.7f}\nLon: {longitude:.7f}\n"
                f"Alt: {altitude}m\nHora: {gps_time}"
            )
            if self.current_marker is None:
                self.current_marker = self.map_widget.set_marker(latitude, longitude, text=marker_text)
            else:
                self.current_marker.set_position(latitude, longitude)
                self.current_marker.text = marker_text
            if len(self.path_coordinates) > 1:
                if self.track_line:
                    self.track_line.delete()
                self.track_line = self.map_widget.set_path(
                    self.path_coordinates, color=COLOR_TRACK_LINE, width=3
                )


if __name__ == "__main__":
    root = tk.Tk()
    app = SondeTrackerApp(root)

    def on_closing():
        app.disconnect_serial()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()
