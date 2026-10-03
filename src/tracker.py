import math
import tkinter as tk
from tkinter import messagebox, ttk
from mission_ui import MissionControls
from zoom import ZoomControls, scaled

import matplotlib
import numpy as np
import serial
import serial.tools.list_ports

matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from antenna import Position, calculate_pointing, position_from_packet, shortest_rotation
from attitude import (
    PROBE_RADIUS, attitude_from_packet, probe_cylinder, probe_nose, rotate, rotation_matrix, tilt_from_vertical,
)
from map_cache import (
    REGION_MAX_TILES, REGION_MIN_ZOOM, TILE_SIZE_KB, OfflineMapView, RegionDownload,
    open_tile_cache, region_tile_count, region_tiles,
)


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

# Camada do mapa: (servidor de tiles, zoom máximo do download de região; None = não permite baixar).
# O servidor do Google não autoriza download em massa, então o Satélite fica offline só no que já foi visto.
MAP_LAYERS = {
    "Padrão": ("https://a.tile.openstreetmap.org/{z}/{x}/{y}.png", 15),
    "Satélite": ("https://mt0.google.com/vt/lyrs=s&x={x}&y={y}&z={z}", None),
    "Topográfico": ("https://a.tile.opentopomap.org/{z}/{x}/{y}.png", 13),
}
OFFLINE_BUTTON_TEXT = "Baixar área offline"
HOME_POSITION = (-15.7641474, -47.8691109)  # posição inicial do mapa: Lago Norte, Brasília


def _thousands(number):
    return f"{number:,}".replace(",", ".")

class SondeTrackerApp(MissionControls, ZoomControls):
    def __init__(self, root):
        self.root = root
        self.root.title("LoRa Telemetry Ground Station — Monitor de Missão")
        self.root.configure(bg=COLOR_BG_MAIN)
        self.init_zoom()

        self.serial_port = None
        self.is_connected = False
        self.current_marker = None
        self.track_line = None
        self.needs_gui_update = False
        self.last_gps_data = None
        self.port_devices = {}
        self.tracker_position = None
        self.tracker_marker = None
        self.antenna_packet = None
        self.sonde_fix = None
        self.antenna_orientation = None
        self._pointing_view_key = None
        self._probe_view_key = ()

        self.history_time = []
        self.history_temp = []
        self.history_alt = []
        self.history_press = []
        self.history_hum = []
        self.path_coordinates = []
        self.tile_cache = open_tile_cache()
        self.region_download = None

        self.telemetry = {
            "Texto Bruto": "--", "Lat": HOME_POSITION[0], "Lon": HOME_POSITION[1],
            "Alt": 0.0, "AltB": 0.0, "Sat": 0, "Fix": 0,
            "T": 0.0, "P": 0.0, "U": 0.0, "Time": "--:--:--",
            "Pitch": 0.0, "Roll": 0.0, "Yaw": 0.0,
            "AX": 0.0, "AY": 0.0, "AZ": 0.0,
            "GX": 0.0, "GY": 0.0, "GZ": 0.0,
            "MX": 0.0, "MY": 0.0, "MZ": 0.0,
            "RSSI": 0, "SNR": 0,
            "WindSpeed": "--", "WindDir": "--", "VertSpeed": "--"
        }

        self.init_missions()
        self._configure_styles()
        self.setup_ui()
        self.apply_zoom()
        self.root.after(500, self.gui_updater_loop)
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
            padding=scaled(7, self.zoom),
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
        self.style.configure("Telemetry.TNotebook", background=COLOR_BG_CARD, borderwidth=0)
        self.style.configure(
            "Telemetry.TNotebook.Tab", background=COLOR_BG_ELEVATED,
            foreground=COLOR_TEXT_MUTED, padding=(scaled(16, self.zoom), scaled(8, self.zoom)),
            font=(FONT_FAMILY, scaled(10, self.zoom), "bold"),
        )
        self.style.map(
            "Telemetry.TNotebook.Tab", background=[("selected", COLOR_BG_CARD)],
            foreground=[("selected", COLOR_ACCENT_CYAN)],
        )

    def _label(self, parent, text, size=10, color=COLOR_TEXT_MAIN, weight="normal", **kwargs):
        label = tk.Label(
            parent,
            text=text,
            bg=kwargs.pop("bg", parent.cget("bg")),
            fg=color,
            font=(FONT_FAMILY, scaled(size, self.zoom), weight),
            **kwargs,
        )
        self.register_zoom_font(label, FONT_FAMILY, size, (weight,))
        return label

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
        self.root.grid_rowconfigure(2, weight=1)
        self.root.grid_columnconfigure(0, weight=1)
        self._build_header()
        self._build_mission_bar()
        self._build_workspace()
        self._build_charts()

    def _build_header(self):
        header = tk.Frame(self.root, bg=COLOR_BG_SURFACE, height=92)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(1, weight=1)

        identity = tk.Frame(header, bg=COLOR_BG_SURFACE, padx=20)
        identity.grid(row=0, column=0, sticky="nsw")
        self._label(identity, "LCA  /  ESTAÇÃO DE SOLO", 8, COLOR_TEXT_MUTED, "bold").pack(anchor=tk.W, pady=(14, 2))
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
            port_row, state="readonly", width=20, font=(FONT_FAMILY, 11),
            style="Telemetry.TCombobox",
        )
        self.port_cb.pack(side=tk.LEFT)
        self.port_cb.bind("<<ComboboxSelected>>", self._on_port_selected)
        self.btn_refresh = tk.Button(
            port_row, text="↻", command=self.refresh_ports,
            bg=COLOR_BG_ELEVATED, fg=COLOR_TEXT_MAIN,
            activebackground=COLOR_BORDER, activeforeground=COLOR_TEXT_MAIN,
            relief=tk.FLAT, width=3, font=(FONT_FAMILY, 13, "bold"), cursor="hand2",
        )
        self.btn_refresh.pack(side=tk.LEFT, padx=(6, 0), fill=tk.Y)
        self.btn_connect = tk.Button(
            controls, text="Conectar", command=self.toggle_connection,
            bg=COLOR_ACCENT_GREEN, fg="#07140d",
            activebackground="#61e59c", activeforeground="#07140d",
            disabledforeground=COLOR_TEXT_SUBTLE, relief=tk.FLAT,
            font=(FONT_FAMILY, 12, "bold"), padx=28, pady=12, cursor="hand2",
        )
        self.btn_connect.pack(side=tk.LEFT, padx=(12, 0), pady=(27, 14))
        self.refresh_ports()

    def _build_workspace(self):
        workspace = tk.Frame(self.root, bg=COLOR_BG_MAIN, padx=14, pady=12)
        workspace.grid(row=2, column=0, sticky="nsew")
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
            layer_group, state="readonly", width=14, font=(FONT_FAMILY, 11),
            style="Telemetry.TCombobox", values=list(MAP_LAYERS),
        )
        self.map_layer_cb.pack(side=tk.LEFT)
        self.map_layer_cb.current(0)
        self.map_layer_cb.bind("<<ComboboxSelected>>", self.change_map_layer)
        self.btn_recenter = tk.Button(
            layer_group, text="Centralizar", command=self.recenter_map,
            bg=COLOR_BG_ELEVATED, fg=COLOR_TEXT_MAIN, relief=tk.FLAT, cursor="hand2",
            activebackground=COLOR_BORDER, activeforeground=COLOR_TEXT_MAIN,
            font=(FONT_FAMILY, 10), padx=12, pady=4,
        )
        self.btn_recenter.pack(side=tk.LEFT, padx=(8, 0))
        self.btn_offline = tk.Button(
            layer_group, text=OFFLINE_BUTTON_TEXT, command=self.toggle_region_download,
            bg=COLOR_BG_ELEVATED, fg=COLOR_TEXT_MAIN, relief=tk.FLAT, cursor="hand2",
            activebackground=COLOR_BORDER, activeforeground=COLOR_TEXT_MAIN,
            font=(FONT_FAMILY, 10), padx=12, pady=4,
        )
        self.btn_offline.pack(side=tk.LEFT, padx=(8, 0))

        self.navigation_tabs = ttk.Notebook(map_card, style="Telemetry.TNotebook")
        self.navigation_tabs.grid(row=1, column=0, sticky="nsew")
        self.navigation_tabs.bind("<<NotebookTabChanged>>", self._on_navigation_tab_changed)
        map_tab = tk.Frame(self.navigation_tabs, bg=COLOR_BG_CARD)
        self.navigation_tabs.add(map_tab, text="Mapa da missão")
        self.map_widget = OfflineMapView(map_tab, corner_radius=0, cache=self.tile_cache)
        self.map_widget.pack(fill=tk.BOTH, expand=True)
        self.map_widget.set_position(self.telemetry["Lat"], self.telemetry["Lon"])
        self.map_widget.set_zoom(14)
        self.map_widget.add_right_click_menu_command(
            label="Definir posição do tracker aqui",
            command=self.configure_tracker, pass_coords=True,
        )
        self._build_antenna_view()
        self._build_probe_view()

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

        self._build_tracker_card()
        self._build_flight_card()
        self._build_link_card()
        self._build_environment_card()
        self._build_imu_card()
        self._build_command_card()
        self._build_message_card()
        self.update_antenna()

    def _on_navigation_tab_changed(self, event=None):
        # As abas 3D usam a altura dos gráficos para manter a geometria legível
        # inclusive na janela mínima. Voltar ao mapa restaura as tendências.
        if hasattr(self, "charts_card"):
            if self.navigation_tabs.index(self.navigation_tabs.select()) != 0:
                self.charts_card.grid_remove()
            else:
                self.charts_card.grid()

    def _build_tracker_card(self):
        card = self._card(self.sidebar_content, "Tracker → sonda")
        card.pack(fill=tk.X, pady=(0, 9))
        frame, self.lbl_distance = self._metric(
            card, "DISTÂNCIA EM LINHA RETA", "— m", COLOR_ACCENT_CYAN,
        )
        frame.pack(fill=tk.X, pady=(0, 8))
        self.lbl_distance_status = self._label(
            card, "", 8, COLOR_TEXT_MUTED, justify=tk.LEFT, wraplength=290,
        )
        self.lbl_distance_status.pack(anchor=tk.W, pady=(0, 8))
        for title, attribute in (
            ("Distância na superfície", "lbl_surface_distance"),
            ("Diferença de altitude", "lbl_altitude_difference"),
        ):
            row, value = self._data_row(card, title)
            row.pack(fill=tk.X, pady=(0, 6))
            setattr(self, attribute, value)
        self.lbl_tracker_position = self._label(
            card, "Defina a posição da antena em solo.", 8, COLOR_TEXT_MUTED,
            justify=tk.LEFT, wraplength=290,
        )
        self.lbl_tracker_position.pack(anchor=tk.W, pady=(2, 8))
        self.btn_tracker = tk.Button(
            card, text="Configurar tracker", command=self.configure_tracker,
            bg=COLOR_BG_ELEVATED, fg=COLOR_TEXT_MAIN, relief=tk.FLAT,
            activebackground=COLOR_BORDER, activeforeground=COLOR_TEXT_MAIN,
            font=(FONT_FAMILY, 12), padx=18, pady=12, cursor="hand2",
        )
        self.btn_tracker.pack(fill=tk.X)

    def configure_tracker(self, coordinates=None):
        if self.replay:
            messagebox.showinfo("Reprodução", "A posição do tracker vem da missão gravada.")
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Posição do tracker / antena")
        margin = scaled(32, self.zoom)
        dialog.configure(bg=COLOR_BG_CARD, padx=margin, pady=margin)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        width = self.dialog_size(720, 0)[0]
        dialog.grid_columnconfigure(1, weight=1)
        self._label(
            dialog, "Informe a posição da antena em solo.\n"
            "Altitude em metros sobre o nível do mar (MSL), como o GPS da sonda.",
            14, justify=tk.LEFT, wraplength=width - 2 * margin,
        ).grid(row=0, column=0, columnspan=2, pady=(0, scaled(20, self.zoom)), sticky="w")
        position = self.tracker_position
        values = [position.latitude, position.longitude, position.altitude] if position else ["", "", ""]
        if coordinates is not None:
            values[:2] = [round(value, 7) for value in coordinates]
        entries = []
        for row, (label, value) in enumerate(zip(
            ("Latitude (°)", "Longitude (°)", "Altitude MSL (m)"), values,
        ), start=1):
            self._label(dialog, label, 14).grid(row=row, column=0, sticky="w", pady=scaled(8, self.zoom))
            entry = ttk.Entry(dialog, width=24, font=(FONT_FAMILY, scaled(15, self.zoom)))
            entry.insert(0, str(value))
            entry.grid(row=row, column=1, sticky="ew", padx=(scaled(20, self.zoom), 0),
                       pady=scaled(8, self.zoom), ipady=scaled(6, self.zoom))
            entries.append(entry)

        def apply_position():
            try:
                new_position = Position(*(float(entry.get().strip().replace(",", ".")) for entry in entries))
            except ValueError as error:
                messagebox.showerror("Posição inválida", f"Preencha os três campos.\n{error}", parent=dialog)
                return
            self.tracker_position = new_position
            latitude, longitude = new_position.latitude, new_position.longitude
            if self.tracker_marker is None:
                self.tracker_marker = self.map_widget.set_marker(
                    latitude, longitude, text="Tracker / antena",
                    marker_color_circle=COLOR_ACCENT_CYAN, marker_color_outside=COLOR_ACCENT_BLUE,
                )
            else:
                self.tracker_marker.set_position(latitude, longitude)
            self.lbl_tracker_position.config(text=(
                f"Tracker: {latitude:.6f}, {longitude:.6f}\n"
                f"Altitude MSL: {new_position.altitude:.1f} m"
            ))
            self._notify_configuration()
            self.update_antenna()
            dialog.destroy()

        tk.Button(
            dialog, text="Aplicar posição", command=apply_position,
            bg=COLOR_ACCENT_GREEN, fg=COLOR_BG_MAIN, relief=tk.FLAT, padx=scaled(18, self.zoom),
            pady=scaled(12, self.zoom), font=(FONT_FAMILY, scaled(15, self.zoom), "bold"), cursor="hand2",
        ).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(scaled(24, self.zoom), 0))
        self.center_dialog(dialog, 720, 300)
        dialog.bind("<Return>", lambda event: apply_position())
        dialog.bind("<Escape>", lambda event: dialog.destroy())
        entries[2 if coordinates is not None else 0].focus_set()

    def _build_antenna_view(self):
        tab = tk.Frame(self.navigation_tabs, bg=COLOR_BG_CARD, padx=12, pady=8)
        self.navigation_tabs.add(tab, text="Antena 3D")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(3, weight=1)
        metrics = tk.Frame(tab, bg=COLOR_BG_CARD)
        metrics.grid(row=0, column=0, sticky="ew")
        for column in range(2):
            metrics.grid_columnconfigure(column, weight=1, uniform="antenna")
        frame, self.lbl_azimuth = self._metric(metrics, "AZIMUTE · NORTE VERDADEIRO", "—°", COLOR_ACCENT_CYAN)
        frame.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        frame, self.lbl_elevation = self._metric(metrics, "ELEVAÇÃO · HORIZONTE", "—°", COLOR_ACCENT_GREEN)
        frame.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        orientation = tk.Frame(tab, bg=COLOR_BG_CARD)
        orientation.grid(row=1, column=0, sticky="ew", pady=8)
        self.lbl_orientation_prompt = self._label(orientation, "Antena atual (opcional):", 11, COLOR_TEXT_MUTED)
        self.lbl_orientation_prompt.pack(side=tk.LEFT)
        self.orientation_entries = []
        for label in ("Az °", "El °"):
            self._label(orientation, label, 11).pack(side=tk.LEFT, padx=(8, 3))
            entry = ttk.Entry(orientation, width=7, font=(FONT_FAMILY, 12))
            entry.pack(side=tk.LEFT, ipady=4)
            entry.bind("<Return>", lambda event: self.apply_antenna_orientation())
            self.orientation_entries.append(entry)
        self.btn_orientation = tk.Button(
            orientation, text="Aplicar", command=self.apply_antenna_orientation,
            bg=COLOR_BG_ELEVATED, fg=COLOR_TEXT_MAIN, relief=tk.FLAT, cursor="hand2", font=(FONT_FAMILY, 11), padx=16, pady=6,
        )
        self.btn_orientation.pack(side=tk.LEFT, padx=(8, 0))

        self.lbl_pointing_status = self._label(
            tab, "", 10, COLOR_TEXT_MUTED, justify=tk.LEFT, anchor="w", wraplength=580,
        )
        self.lbl_pointing_status.grid(row=2, column=0, sticky="ew")
        tab.bind("<Configure>", lambda event: self.lbl_pointing_status.configure(
            wraplength=max(200, event.width - 28)
        ))
        self.antenna_fig = Figure(figsize=(7, 3), dpi=100, facecolor=COLOR_BG_CARD)
        self.antenna_axis = self.antenna_fig.add_subplot(111, projection="3d")
        self.antenna_axis.view_init(elev=24, azim=-58)
        self.antenna_canvas = FigureCanvasTkAgg(self.antenna_fig, master=tab)
        self.antenna_canvas.get_tk_widget().configure(highlightthickness=0)
        self.antenna_canvas.get_tk_widget().grid(row=3, column=0, sticky="nsew")
        self.lbl_pythagoras = self._label(tab, "d² = h² + v²", 9, COLOR_ACCENT_CYAN)
        self.lbl_pythagoras.grid(row=4, column=0, sticky="w")
        self._label(
            tab, "Arraste para girar a vista • h: projeção horizontal • v: vertical local\n"
            "Terra esférica; v inclui curvatura. Azimute: N 0° · L 90° · S 180° · O 270°.\n"
            "Sem telemetria recente, a antena aponta para a última posição GPS 3D da sonda.",
            8, COLOR_TEXT_MUTED, justify=tk.LEFT,
        ).grid(row=5, column=0, sticky="w", pady=(3, 0))

    def apply_antenna_orientation(self):
        if self.replay:
            messagebox.showinfo("Reprodução", "A orientação da antena vem da missão gravada.")
            return
        values = [entry.get().strip().replace(",", ".") for entry in self.orientation_entries]
        if not any(values):
            self.antenna_orientation = None
        else:
            try:
                azimuth, elevation = map(float, values)
                if not (math.isfinite(azimuth) and math.isfinite(elevation)
                        and 0 <= azimuth <= 360 and -90 <= elevation <= 90):
                    raise ValueError
            except ValueError:
                messagebox.showerror("Orientação inválida", "Use azimute de 0 a 360° e elevação de -90 a 90°.\n"
                                     "Esvazie ambos os campos para remover a orientação atual.")
                return
            self.antenna_orientation = (azimuth % 360, elevation)
        self._notify_configuration()
        self.update_antenna()

    @staticmethod
    def _format_distance(value):
        return f"{value / 1000:.2f} km" if abs(value) >= 1000 else f"{value:.1f} m"

    def update_antenna(self):
        pointing, stale = None, False
        status = "Configure a posição do tracker para calcular o apontamento."
        if self.tracker_position is not None:
            if self.sonde_fix is None:
                status = "Aguardando pacote GPS completo da sonda."
                if self.antenna_packet is not None:
                    try:
                        position_from_packet(self.antenna_packet[0])
                    except ValueError as error:
                        status = str(error)
            else:
                pointing = calculate_pointing(self.tracker_position, self.sonde_fix[0])
                age = self._sonde_fix_age()
                stale = age is None or age > 10
        view_key = (pointing, stale, status, self.antenna_orientation)
        if view_key == self._pointing_view_key:
            return
        self._pointing_view_key = view_key
        if pointing is None:
            distance_status = status
        elif stale:
            distance_status = "Última posição GPS 3D conhecida · telemetria atrasada"
        else:
            distance_status = "GPS 3D · posição gravada" if self.replay else "GPS 3D · posição recente"
        self.lbl_distance_status.config(
            text=distance_status,
            fg=COLOR_TEXT_MUTED if pointing is None else (COLOR_WARNING if stale else COLOR_ACCENT_GREEN),
        )
        self.lbl_distance.config(text=self._format_distance(pointing.distance) if pointing else "— m")
        self.lbl_surface_distance.config(text=self._format_distance(pointing.surface_distance) if pointing else "—")
        self.lbl_altitude_difference.config(text=f"{pointing.altitude_difference:+.1f} m" if pointing else "—")
        self.lbl_azimuth.config(text=f"{pointing.azimuth:.1f}°" if pointing and pointing.azimuth is not None else "—°")
        self.lbl_elevation.config(text=f"{pointing.elevation:+.1f}°" if pointing and pointing.elevation is not None else "—°")
        color = COLOR_TEXT_MUTED
        if pointing is not None:
            color = COLOR_WARNING if stale else COLOR_ACCENT_GREEN
            if pointing.elevation is None:
                status = "Tracker e sonda coincidem; direção de apontamento indefinida."
            elif pointing.azimuth is None:
                status = f"Sonda na vertical: elevação {pointing.elevation:+.1f}°. Azimute indefinido."
            else:
                status = f"Aponte para azimute {pointing.azimuth:.1f}° e elevação {pointing.elevation:+.1f}°."
            if stale:
                status = "Telemetria atrasada há mais de 10 s; apontando para a última posição conhecida. " + status
            if pointing.elevation is not None and pointing.elevation < 0:
                status += " Sonda abaixo do horizonte local."
                color = COLOR_WARNING
            if self.antenna_orientation and pointing.elevation is not None:
                azimuth, elevation = self.antenna_orientation
                corrections = []
                if pointing.azimuth is not None:
                    turn = shortest_rotation(azimuth, pointing.azimuth)
                    corrections.append(f"gire {abs(turn):.1f}° à {'direita' if turn >= 0 else 'esquerda'}")
                tilt = pointing.elevation - elevation
                corrections.append(f"{'eleve' if tilt >= 0 else 'abaixe'} {abs(tilt):.1f}°")
                status += " Ajuste: " + "; ".join(corrections) + "."
        self.lbl_pointing_status.config(text=status, fg=color)
        self._draw_antenna(pointing, stale)

    @staticmethod
    def _direction(azimuth, elevation):
        """Vetor unitário leste/norte/cima para azimute (0° = norte) e elevação em graus."""
        azimuth, elevation = math.radians(azimuth), math.radians(elevation)
        return (math.cos(elevation) * math.sin(azimuth), math.cos(elevation) * math.cos(azimuth), math.sin(elevation))

    def _draw_yagi(self, axis, azimuth, elevation, color, length=.75, alpha=1.0):
        """Antena Yagi: gôndola ao longo da direção e elementos horizontais perpendiculares."""
        direction = self._direction(azimuth, elevation)
        side = (math.cos(math.radians(azimuth)), -math.sin(math.radians(azimuth)), 0)
        axis.plot(*([0, length * value] for value in direction), color=color, linewidth=3, alpha=alpha)
        for step, half in ((.08, .17), (.3, .14), (.48, .12), (.66, .1), (.84, .085)):
            center = [length * step * value for value in direction]
            axis.plot(*([c - half * s, c + half * s] for c, s in zip(center, side)),
                      color=color, linewidth=2, alpha=alpha)
        tip = [length * 1.04 * value for value in direction]
        axis.quiver(*tip, *(.14 * value for value in direction), color=color, linewidth=2,
                    arrow_length_ratio=.6, alpha=alpha)

    def _draw_antenna(self, pointing, stale=False):
        axis = self.antenna_axis
        elevation, azimuth = axis.elev, axis.azim
        axis.clear()
        axis.view_init(elev=elevation, azim=azimuth)
        axis.set_facecolor(COLOR_BG_CARD)
        axis.set_axis_off()
        self.antenna_fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
        self.lbl_pythagoras.config(text="Pitágoras: d² = h² + v² • Elevação = atan2(v, h)")
        if self.tracker_position is None:
            axis.set_box_aspect((1, 1, 1))
            axis.text2D(0.5, 0.5, "Configure o tracker para ver o apontamento da antena",
                        transform=axis.transAxes, ha="center", color=COLOR_TEXT_MUTED, fontsize=10)
            self.antenna_canvas.draw_idle()
            return

        # Horizonte local do tracker: disco com rosa dos ventos, mastro e zênite.
        ring = [math.radians(angle) for angle in range(0, 361, 5)]
        axis.add_collection3d(Poly3DCollection(
            [[(math.sin(angle), math.cos(angle), 0) for angle in ring]],
            facecolor=COLOR_ACCENT_BLUE, alpha=.07, edgecolor=COLOR_BORDER,
        ))
        for radius in (.5, 1):
            axis.plot([radius * math.sin(a) for a in ring], [radius * math.cos(a) for a in ring], 0,
                      color=COLOR_GRAPH_GRID, linewidth=1)
        for angle in range(0, 360, 30):
            x, y = math.sin(math.radians(angle)), math.cos(math.radians(angle))
            axis.plot([.94 * x, x], [.94 * y, y], [0, 0], color=COLOR_TEXT_SUBTLE, linewidth=1)
        for angle, label in ((0, "N"), (90, "L"), (180, "S"), (270, "O")):
            x, y = math.sin(math.radians(angle)), math.cos(math.radians(angle))
            axis.plot([0, x], [0, y], [0, 0], color=COLOR_GRAPH_GRID, linewidth=1)
            axis.text(1.14 * x, 1.14 * y, 0, label, ha="center", va="center", fontsize=10, fontweight="bold",
                      color=COLOR_DANGER if label == "N" else COLOR_TEXT_MUTED)
        axis.plot([0, 0], [0, 0], [0, 1.05], color=COLOR_GRAPH_GRID, linewidth=1, linestyle=":")
        axis.text(0, 0, 1.1, "Zênite", color=COLOR_TEXT_SUBTLE, fontsize=8, ha="center")
        axis.plot([0, 0], [0, 0], [-.35, 0], color=COLOR_TEXT_MUTED, linewidth=4)
        axis.plot([-.12, 0, .12], [0, 0, 0], [-.35, -.2, -.35], color=COLOR_TEXT_MUTED, linewidth=2)
        axis.text(0, 0, -.47, "Tracker", color=COLOR_ACCENT_GREEN, fontsize=9, ha="center")

        low = -.55
        if self.antenna_orientation:
            self._draw_yagi(axis, *self.antenna_orientation, COLOR_TEXT_MUTED, length=.6, alpha=.75)
            low = min(low, .75 * math.sin(math.radians(self.antenna_orientation[1])) - .1)

        if pointing is None or pointing.elevation is None:
            message = "Aguardando posição GPS 3D da sonda" if pointing is None else "Direção indefinida"
            axis.text2D(.02, .95, message, transform=axis.transAxes, color=COLOR_TEXT_MUTED, fontsize=11,
                        fontweight="bold")
            axis.text2D(.02, .89, "Cinza: antena atual (se informada)", transform=axis.transAxes,
                        color=COLOR_TEXT_MUTED, fontsize=8)
        else:
            target_color = COLOR_WARNING if stale else COLOR_ACCENT_GREEN
            target_azimuth = pointing.azimuth if pointing.azimuth is not None else 0.0
            # Escala isotrópica: o vetor d tem comprimento 1 e preserva o ângulo real.
            east, north, up = (value / pointing.distance for value in (pointing.east, pointing.north, pointing.up))
            low = min(low, up - .15)
            axis.plot([0, east], [0, north], [0, 0], color=COLOR_ACCENT_BLUE, linestyle="--", linewidth=1.5)
            axis.plot([east, east], [north, north], [0, up], color=COLOR_WARNING, linestyle="--", linewidth=1.5)
            axis.plot([0, east], [0, north], [0, up], color=COLOR_ACCENT_CYAN, linewidth=1.2, linestyle=":")
            axis.scatter([east], [north], [up], color=COLOR_ACCENT_CYAN, s=70, depthshade=False)
            axis.text(east, north, up + .1, "Sonda", color=COLOR_ACCENT_CYAN, fontsize=9, ha="center")
            axis.text(east / 2, north / 2, -.08, "h", color=COLOR_ACCENT_BLUE, fontsize=10)
            axis.text(east, north, up / 2, "v", color=COLOR_WARNING, fontsize=10)
            self._draw_yagi(axis, target_azimuth, pointing.elevation, target_color)

            if pointing.azimuth is not None:
                # Arco de azimute no horizonte, do norte no sentido horário até a direção alvo.
                arc = [math.radians(pointing.azimuth * step / 40) for step in range(41)]
                axis.plot([.42 * math.sin(a) for a in arc], [.42 * math.cos(a) for a in arc], 0,
                          color=COLOR_ACCENT_CYAN, linewidth=2.5)
                middle = math.radians(pointing.azimuth / 2)
                axis.text(.55 * math.sin(middle), .55 * math.cos(middle), .02, f"Az {pointing.azimuth:.1f}°",
                          color=COLOR_ACCENT_CYAN, fontsize=9, fontweight="bold", ha="center")
            # Arco de elevação no plano vertical do azimute alvo.
            arc = [self._direction(target_azimuth, pointing.elevation * step / 30) for step in range(31)]
            axis.plot(*([.3 * point[index] for point in arc] for index in range(3)),
                      color=COLOR_ACCENT_GREEN, linewidth=2.5)
            label = [.38 * value for value in self._direction(target_azimuth, pointing.elevation / 2)]
            axis.text(*label, f"El {pointing.elevation:+.1f}°", color=COLOR_ACCENT_GREEN, fontsize=9,
                      fontweight="bold")

            azimuth_text = f"{pointing.azimuth:.1f}°" if pointing.azimuth is not None else "indefinido"
            axis.text2D(.02, .95, f"Azimute {azimuth_text}   ·   Elevação {pointing.elevation:+.1f}°",
                        transform=axis.transAxes, color=target_color, fontsize=12, fontweight="bold")
            axis.text2D(.02, .89, ("Laranja: última posição conhecida" if stale else "Verde: direção alvo")
                        + "  |  Cinza: antena atual (se informada)",
                        transform=axis.transAxes, color=COLOR_TEXT_MUTED, fontsize=8)
            self.lbl_pythagoras.config(text=(
                f"d = √(h² + v²) = {self._format_distance(pointing.distance)}  |  "
                f"h = {self._format_distance(pointing.horizontal)}  |  v = {self._format_distance(pointing.up)}"
            ))
        high = 1.2
        axis.set_xlim(-1.2, 1.2)
        axis.set_ylim(-1.2, 1.2)
        axis.set_zlim(low, high)
        axis.set_box_aspect((2.4, 2.4, high - low))
        self.antenna_canvas.draw_idle()

    def _build_probe_view(self):
        tab = tk.Frame(self.navigation_tabs, bg=COLOR_BG_CARD, padx=12, pady=8)
        self.navigation_tabs.add(tab, text="Sonda 3D")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)
        metrics = tk.Frame(tab, bg=COLOR_BG_CARD)
        metrics.grid(row=0, column=0, sticky="ew")
        # Cada ângulo usa a cor do eixo do IMU em torno do qual ele gira.
        for column, (title, attribute, accent) in enumerate((
            ("PITCH · EIXO Y", "lbl_probe_pitch", COLOR_ACCENT_GREEN),
            ("ROLL · EIXO X", "lbl_probe_roll", COLOR_DANGER),
            ("YAW · EIXO Z", "lbl_probe_yaw", COLOR_ACCENT_BLUE),
        )):
            metrics.grid_columnconfigure(column, weight=1, uniform="probe")
            frame, value = self._metric(metrics, title, "—°", accent)
            frame.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 4, 0 if column == 2 else 4))
            setattr(self, attribute, value)
        self.lbl_probe_status = self._label(tab, "", 10, COLOR_TEXT_MUTED, anchor="w")
        self.lbl_probe_status.grid(row=1, column=0, sticky="ew", pady=8)
        self.probe_fig = Figure(figsize=(7, 3), dpi=100, facecolor=COLOR_BG_CARD)
        self.probe_axis = self.probe_fig.add_subplot(111, projection="3d")
        self.probe_axis.view_init(elev=22, azim=-60)
        self.probe_canvas = FigureCanvasTkAgg(self.probe_fig, master=tab)
        self.probe_canvas.get_tk_widget().configure(highlightthickness=0)
        self.probe_canvas.get_tk_widget().grid(row=2, column=0, sticky="nsew")
        self._label(
            tab, "Arraste para girar a vista • Vermelho: nariz (+X do IMU) • Azul: topo (+Z), mostra o roll.\n"
            "Ângulos tarados no boot: os eixos pontilhados são a atitude ao ligar a sonda, não o norte.",
            8, COLOR_TEXT_MUTED, justify=tk.LEFT,
        ).grid(row=3, column=0, sticky="w", pady=(3, 0))
        self.update_probe()

    def update_probe(self):
        attitude = attitude_from_packet(self.telemetry) if self.current_record else None
        if attitude == self._probe_view_key:
            return
        self._probe_view_key = attitude
        for label, angle in zip((self.lbl_probe_pitch, self.lbl_probe_roll, self.lbl_probe_yaw), attitude or (None,) * 3):
            label.config(text=f"{angle:+.1f}°" if angle is not None else "—°")
        matrix = rotation_matrix(*attitude) if attitude else None
        if matrix is None:
            self.lbl_probe_status.config(text="Aguardando Pitch, Roll e Yaw da sonda.", fg=COLOR_TEXT_MUTED)
        else:
            tilt = tilt_from_vertical(matrix)
            upside_down = tilt > 90
            self.lbl_probe_status.config(
                text=f"Inclinação do topo (+Z) em relação à vertical: {tilt:.1f}°"
                     + (" · sonda de cabeça para baixo" if upside_down else ""),
                fg=COLOR_WARNING if upside_down else COLOR_ACCENT_GREEN,
            )
        self._draw_probe(matrix)

    def _draw_probe(self, matrix):
        axis = self.probe_axis
        elevation, azimuth = axis.elev, axis.azim
        axis.clear()
        axis.view_init(elev=elevation, azim=azimuth)
        axis.set_facecolor(COLOR_BG_CARD)
        axis.set_axis_off()
        axis.set_box_aspect((1, 1, 1))
        self.probe_fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
        if matrix is None:
            axis.text2D(0.5, 0.5, "Aguardando atitude da sonda", transform=axis.transAxes,
                        ha="center", color=COLOR_TEXT_MUTED, fontsize=10)
            self.probe_canvas.draw_idle()
            return
        # Mesmo desenho da interface antiga: eixos pontilhados, cilindro translúcido e nariz vermelho.
        # Desenha na ordem de inserção para o nariz e o topo ficarem visíveis através do cilindro.
        axis.computed_zorder = False
        reference = 1.9
        for end, label in (((reference, 0, 0), "X₀"), ((0, reference, 0), "Y₀"), ((0, 0, reference), "Z₀ (cima)")):
            axis.plot(*([-value, value] for value in end), color=COLOR_TEXT_MUTED, linestyle="dotted", linewidth=1)
            axis.text(*(value * 1.05 for value in end), label, color=COLOR_TEXT_MUTED, fontsize=8)
        axis.plot_surface(*map(np.array, probe_cylinder(matrix)), color=COLOR_ACCENT_BLUE, alpha=.5,
                          edgecolor=COLOR_BG_MAIN, linewidth=.4)
        nose = probe_nose(matrix)
        axis.plot(*([0, value] for value in nose), color=COLOR_DANGER, linewidth=2.5)
        axis.scatter(*nose, color=COLOR_DANGER, s=45)
        axis.text(nose[0], nose[1], nose[2] + .3, "nariz", color=COLOR_DANGER, fontsize=9)
        base, tip = rotate(matrix, (0, 0, PROBE_RADIUS)), rotate(matrix, (0, 0, PROBE_RADIUS + .7))
        axis.quiver(*base, *(t - b for t, b in zip(tip, base)), color=COLOR_ACCENT_BLUE, linewidth=2.5,
                    arrow_length_ratio=.25)
        axis.text(*(value * 1.1 for value in tip), "topo", color=COLOR_ACCENT_BLUE, fontsize=9)
        for set_limit in (axis.set_xlim, axis.set_ylim, axis.set_zlim):
            set_limit(-2.0, 2.0)
        self.probe_canvas.draw_idle()

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
        self.charts_card = charts_card
        charts_card.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 14))
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
        self.map_widget.set_tile_server(MAP_LAYERS[self.map_layer_cb.get()][0])

    def recenter_map(self):
        """Centraliza o mapa na sonda; sem posição dela, no tracker; sem nenhum dos dois, na posição inicial."""
        marker = self.current_marker or self.tracker_marker
        self.map_widget.set_position(*(marker.position if marker else HOME_POSITION))

    def toggle_region_download(self):
        """Baixa para o cache a área visível do mapa, na camada atual; com download em curso, cancela."""
        if self.region_download:
            self.region_download.cancel()
            return
        layer = self.map_layer_cb.get()
        server, max_zoom = MAP_LAYERS[layer]
        if self.tile_cache is None:
            messagebox.showerror("Mapa offline", "Não foi possível abrir o arquivo de cache do mapa.")
            return
        if max_zoom is None:
            messagebox.showinfo(
                "Mapa offline",
                f"A camada {layer} não permite baixar regiões; ela fica disponível sem internet "
                "só nas áreas já vistas. Use a camada Padrão ou Topográfico.",
            )
            return
        canvas = self.map_widget.canvas
        top_left = self.map_widget.convert_canvas_coords_to_decimal_coords(0, 0)
        bottom_right = self.map_widget.convert_canvas_coords_to_decimal_coords(canvas.winfo_width(), canvas.winfo_height())
        total = region_tile_count(top_left, bottom_right, REGION_MIN_ZOOM, max_zoom)
        if total > REGION_MAX_TILES:
            messagebox.showwarning(
                "Área grande demais",
                f"A área visível precisa de {_thousands(total)} imagens até o zoom {max_zoom}; o limite é "
                f"{_thousands(REGION_MAX_TILES)}. Aproxime o mapa e tente de novo.",
            )
            return
        megabytes = max(1, round(total * TILE_SIZE_KB / 1024))
        if not messagebox.askyesno(
            "Baixar área offline",
            f"Baixar a área visível do mapa na camada {layer}, do zoom {REGION_MIN_ZOOM} ao {max_zoom}?\n\n"
            f"São {_thousands(total)} imagens, cerca de {megabytes} MB. As já salvas são puladas.",
        ):
            return
        tiles = list(region_tiles(top_left, bottom_right, REGION_MIN_ZOOM, max_zoom))
        self.region_download = RegionDownload(self.tile_cache, server, tiles).start()
        self._poll_region_download()

    def _poll_region_download(self):
        download = self.region_download
        if download.running:
            percent = download.done * 100 // max(download.total, 1)
            self.btn_offline.configure(text=f"Cancelar · {percent}%")
            self.root.after(500, self._poll_region_download)
            return
        self.region_download = None
        self.btn_offline.configure(text=OFFLINE_BUTTON_TEXT)
        saved = download.done - download.failed
        if download.offline:
            messagebox.showwarning(
                "Mapa offline",
                f"O download parou: {download.failed} falhas seguidas (sem internet ou o servidor recusou).\n"
                f"{saved} de {download.total} imagens estão salvas; tente de novo para completar.",
            )
        elif download.cancelled:
            messagebox.showinfo("Mapa offline", f"Download cancelado. {saved} de {download.total} imagens estão salvas.")
        elif download.failed:
            messagebox.showwarning(
                "Mapa offline",
                f"{saved} de {download.total} imagens salvas; {download.failed} falharam. Tente de novo para completar.",
            )
        else:
            messagebox.showinfo("Mapa offline", "Área salva: o mapa dessa região funciona sem internet.")

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
                state=tk.NORMAL if selected in self.port_devices and self.mission and not self.mission.end_requested and not self.replay else tk.DISABLED
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







    def update_charts(self):
        if not self.history_time:
            return
        try:
            self.fig.clear()
            altitude_axis = self.fig.add_subplot(1, 3, 1)
            environment_axis = self.fig.add_subplot(1, 3, 2)
            pressure_axis = self.fig.add_subplot(1, 3, 3)
            count = len(self.history_time)
            indices = list(range(0, count, max(1, math.ceil(count / 1000))))
            if indices[-1] != count - 1:
                indices.append(count - 1)
            def sampled(values):
                values = list(values)
                return [values[index] for index in indices]
            # Mantém no máximo quatro horários visíveis por gráfico, mesmo em janelas estreitas.
            step = max(1, math.ceil(len(indices) / 4))
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

            altitude_axis.plot(indices, sampled(self.history_alt), color=COLOR_ACCENT_CYAN, linewidth=1.8)
            altitude_axis.fill_between(indices, sampled(self.history_alt), color=COLOR_ACCENT_CYAN, alpha=0.08)
            environment_axis.plot(
                indices, sampled(self.history_temp), color=COLOR_WARNING,
                linewidth=1.7, label="Temperatura",
            )
            environment_axis.plot(
                indices, sampled(self.history_hum), color=COLOR_ACCENT_CYAN,
                linewidth=1.4, linestyle="--", label="Umidade",
            )
            environment_axis.legend(
                loc="upper right", fontsize=6, facecolor=COLOR_BG_CARD,
                edgecolor="none", labelcolor=COLOR_TEXT_MAIN,
            )
            pressure_axis.plot(indices, sampled(self.history_press), color=COLOR_ACCENT_GREEN, linewidth=1.8)
            pressure_axis.fill_between(indices, sampled(self.history_press), color=COLOR_ACCENT_GREEN, alpha=0.07)
            self.fig.tight_layout(pad=1.1, w_pad=2.0)
            self.canvas.draw_idle()
        except Exception as error:
            print(f"Falha na renderização do canvas: {error}")

    def update_gui(self):
        def value(key, suffix=""):
            item = self.telemetry.get(key)
            return f"{item}{suffix}" if item is not None else "—"
        self.lbl_callsign.config(text=f"MISSÃO  {value('Texto Bruto')}")
        self.lbl_rssi.config(text=value("RSSI", " dBm"))
        self.lbl_snr.config(text=value("SNR", " dB"))
        self.lbl_time.config(text=value("Time"))
        valid = bool(self.current_record and self.current_record["gps_valid"])
        latitude, longitude = self.telemetry.get("Lat"), self.telemetry.get("Lon")
        for label, coordinate in ((self.lbl_lat, latitude), (self.lbl_lon, longitude)):
            label.config(text=f"{coordinate:.7f}" if coordinate is not None else "—")
        self.lbl_alt.config(text=value("Alt", " m"))
        self.lbl_altb.config(text=value("AltB", " m"))
        fix = self.telemetry.get("Fix")
        fix_name = {0:"sem fix", 2:"2D", 3:"3D", 5:"tempo"}.get(fix, "desconhecido")
        self.lbl_sat.config(text=f"{value('Fix')} · {fix_name} / {value('Sat')} sats")
        for attribute, key, suffix in (
            ("lbl_wind_speed", "WindSpeed", " m/s"), ("lbl_wind_dir", "WindDir", "°"),
            ("lbl_vert_speed", "VertSpeed", " m/s"), ("lbl_temp", "T", " °C"),
            ("lbl_press", "P", " hPa"), ("lbl_hum", "U", " %"),
            ("lbl_pitch", "Pitch", "°"), ("lbl_roll", "Roll", "°"), ("lbl_yaw", "Yaw", "°"),
            ("lbl_gx", "GX", ""), ("lbl_gy", "GY", ""), ("lbl_gz", "GZ", ""),
            ("lbl_mx", "MX", ""), ("lbl_my", "MY", ""), ("lbl_mz", "MZ", ""),
            ("lbl_bat", "Bat", " V"), ("lbl_ack", "Ack", ""),
        ):
            getattr(self, attribute).config(text=value(key, suffix))
        for attribute, key in (("lbl_ax", "AX"), ("lbl_ay", "AY"), ("lbl_az", "AZ")):
            getattr(self, attribute).config(text=value(key + "avg" if key + "avg" in self.telemetry else key))
        self.lbl_sample_count.config(text=f"{self.sample_count} amostras")
        if valid:
            self.lbl_map_coordinates.config(text=f"{latitude:.7f}, {longitude:.7f}")
            marker_text = f"{value('Texto Bruto')}\nAlt: {value('Alt', ' m')}\nGPS: {value('Time')}"
            if self.current_marker is None:
                self.current_marker = self.map_widget.set_marker(latitude, longitude, text=marker_text)
                self.map_widget.set_position(latitude, longitude)
            else:
                self.current_marker.set_position(latitude, longitude)
                self.current_marker.set_text(marker_text)
        else:
            self.lbl_map_coordinates.config(text="Posição GPS indisponível neste pacote")
            if self.current_marker:
                self.current_marker.delete()
                self.current_marker = None
        if len(self.path_coordinates) > 1:
            if self.track_line:
                self.track_line.delete()
            coordinates = list(self.path_coordinates)
            visible = coordinates[::max(1, math.ceil(len(coordinates) / 1000))]
            if visible[-1] != coordinates[-1]:
                visible.append(coordinates[-1])
            self.track_line = self.map_widget.set_path(visible, color=COLOR_TRACK_LINE, width=3)
        self.update_probe()


if __name__ == "__main__":
    root = tk.Tk()
    app = SondeTrackerApp(root)
    root.protocol("WM_DELETE_WINDOW", app.close_application)
    root.mainloop()
