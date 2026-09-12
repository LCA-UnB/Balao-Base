import tkinter as tk
from tkinter import ttk, messagebox
import serial
import serial.tools.list_ports
import threading
import tkintermapview
import re
import datetime
import math

# Importações para embutir gráficos de forma nativa e estável no Tkinter
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure  

# CONSTANTES DE DESIGN
COLOR_BG_MAIN = "#121214"      
COLOR_BG_CARD = "#1a1a1e"      
COLOR_TEXT_MAIN = "#e1e1e6"    
COLOR_TEXT_MUTED = "#8d8d99"   
COLOR_ACCENT_GREEN = "#04d361" 
COLOR_ACCENT_BLUE = "#4e94e7"  
COLOR_ACCENT_YELLOW = "#f5c518"
COLOR_GRAPH_GRID = "#29292e"   
COLOR_TRACK_LINE = "#ff5722"   # Rastro: Laranja Vibrante de Alta Visibilidade

class SondeTrackerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("LoRa Telemetry Ground Station — Monitor de Missão")
        self.root.geometry("1400x880")
        self.root.configure(bg=COLOR_BG_MAIN)
        
        # Variáveis de controle
        self.serial_port = None
        self.is_connected = False
        self.read_thread = None
        self.current_marker = None
        self.track_line = None  
        self.log_file = None
        self.needs_gui_update = False
        
        # Controle de tempo e dinâmica de voo
        self.last_packet_time = None  
        self.last_gps_data = None     
        
        # Históricos contínuos para os gráficos de linha do tempo
        self.history_time = []             # Eixo X com o horário real do GPS da sonda
        self.history_temp = []
        self.history_alt = []
        self.history_press = []
        self.history_bat = []
        self.path_coordinates = []
        
        # Campos alinhados ao pacote realmente enviado pelo bordo
        # (PT2UNB): Lat, Lon, Alt, AltB, Sat, Fix, T, P, Time, Pitch, Roll,
        # Yaw, AX, AY, AZ, AXavg, AYavg, AZavg, Bat, Ack.
        self.telemetry = {
            "Texto Bruto": "--", "Lat": -15.7641474, "Lon": -47.8691109,
            "Alt": 0.0, "AltB": 0.0, "Sat": 0, "Fix": 0,
            "T": 0.0, "P": 0.0, "Time": "--:--:--",
            "Pitch": 0.0, "Roll": 0.0, "Yaw": 0.0,
            "AX": 0.0, "AY": 0.0, "AZ": 0.0,
            "AXavg": 0.0, "AYavg": 0.0, "AZavg": 0.0,
            "Bat": 0.0, "Ack": 0,
            "RSSI": 0, "SNR": 0,
            "WindSpeed": "--", "WindDir": "--",
            "VertSpeed": "--"  
        }

        self.style = ttk.Style()
        self.style.theme_use('default')
        self.style.configure("TCombobox", fieldbackground=COLOR_BG_MAIN, background=COLOR_BG_CARD, foreground=COLOR_TEXT_MAIN, arrowcolor=COLOR_TEXT_MAIN)

        self.setup_ui()
        self.root.after(1000, self.gui_updater_loop)

    def create_card_frame(self, parent, title):
        frame = tk.LabelFrame(
            parent, text=title, bg=COLOR_BG_CARD, fg=COLOR_TEXT_MUTED,
            font=("Segoe UI", 9, "bold"), padx=8, pady=4,
            relief=tk.FLAT, bd=0
        )
        return frame

    def create_data_label(self, parent, text, fg_color=COLOR_TEXT_MAIN, font_size=9, bold=False):
        weight = "bold" if bold else "normal"
        lbl = tk.Label(parent, text=text, bg=COLOR_BG_CARD, fg=fg_color, font=("Segoe UI", font_size, weight))
        return lbl

    def setup_ui(self):
        # --- CONFIGURAÇÃO DE LAYOUT ULTRA-MAXIMIZADO PARA O MAPA ---
        
        # 1. Coluna Esquerda Otimizada (Largura enxuta para dados)
        self.left_frame = tk.Frame(self.root, width=310, bg=COLOR_BG_MAIN, padx=5, pady=10)
        self.left_frame.pack(side=tk.LEFT, fill=tk.Y)
        self.left_frame.pack_propagate(False)

        # 2. Coluna Direita Otimizada (Largura enxuta para os gráficos horizontais)
        self.right_graph_container = tk.Frame(self.root, width=310, bg=COLOR_BG_MAIN, padx=5, pady=10)
        self.right_graph_container.pack(side=tk.RIGHT, fill=tk.Y)
        self.right_graph_container.pack_propagate(False)

        # 3. Coluna Central Gigante (O Mapa se expande pegando toda a área nobre restante)
        self.center_map_frame = tk.Frame(self.root, bg=COLOR_BG_MAIN, padx=5, pady=10)
        self.center_map_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # --- CONTEÚDO DA COLUNA ESQUERDA (DADOS FLATS) ---
        serial_frame = self.create_card_frame(self.left_frame, "CONEXÃO HELTEC")
        serial_frame.pack(fill=tk.X, pady=(0, 6))

        self.port_cb = ttk.Combobox(serial_frame, state="readonly", font=("Segoe UI", 9))
        self.port_cb.pack(side=tk.LEFT, padx=(0, 4), pady=2, fill=tk.X, expand=True)
        self.refresh_ports()

        self.btn_refresh = tk.Button(serial_frame, text="↻", command=self.refresh_ports, bg=COLOR_BG_MAIN, fg=COLOR_TEXT_MAIN, activebackground=COLOR_BG_CARD, activeforeground=COLOR_TEXT_MAIN, relief=tk.FLAT, width=2, font=("Segoe UI", 9, "bold"))
        self.btn_refresh.pack(side=tk.LEFT, padx=1)

        self.btn_connect = tk.Button(serial_frame, text="Conectar", command=self.toggle_connection, bg="#202024", fg=COLOR_TEXT_MAIN, activebackground=COLOR_ACCENT_GREEN, activeforeground="black", relief=tk.FLAT, font=("Segoe UI", 9, "bold"), padx=8)
        self.btn_connect.pack(side=tk.LEFT, padx=(4, 0))

        map_layer_frame = self.create_card_frame(self.left_frame, "ESTILO DO MAPA")
        map_layer_frame.pack(fill=tk.X, pady=(0, 6))
        self.map_layer_cb = ttk.Combobox(map_layer_frame, state="readonly", font=("Segoe UI", 9), values=["Padrão", "Satélite", "Topográfico"])
        self.map_layer_cb.pack(fill=tk.X, pady=1)
        self.map_layer_cb.current(0)
        self.map_layer_cb.bind("<<ComboboxSelected>>", self.change_map_layer)

        callsign_card = tk.Frame(self.left_frame, bg=COLOR_BG_CARD, padx=12, pady=5)
        callsign_card.pack(fill=tk.X, pady=(0, 6))
        self.lbl_callsign = tk.Label(callsign_card, text="CALLSIGN: --", font=("Segoe UI", 12, "bold"), fg=COLOR_ACCENT_GREEN, bg=COLOR_BG_CARD)
        self.lbl_callsign.pack(anchor=tk.W)

        signal_frame = self.create_card_frame(self.left_frame, "LINK DE RÁDIO LORA")
        signal_frame.pack(fill=tk.X, pady=(0, 6))
        signal_frame.grid_columnconfigure(0, weight=1)
        signal_frame.grid_columnconfigure(1, weight=1)
        self.lbl_rssi = self.create_data_label(signal_frame, "RSSI: -- dBm", COLOR_ACCENT_BLUE, 9, True)
        self.lbl_rssi.grid(row=0, column=0, sticky=tk.W, pady=1)
        self.lbl_snr = self.create_data_label(signal_frame, "SNR: -- dB", COLOR_ACCENT_BLUE, 9, True)
        self.lbl_snr.grid(row=0, column=1, sticky=tk.W, pady=1)
        self.lbl_packet_age = self.create_data_label(signal_frame, "Último Pacote: --s atrás", COLOR_TEXT_MAIN, 9, False)
        self.lbl_packet_age.grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=1)

        gps_frame = self.create_card_frame(self.left_frame, "NAVEGAÇÃO GPS & VENTO")
        gps_frame.pack(fill=tk.X, pady=(0, 6))
        gps_frame.grid_columnconfigure(0, weight=1)
        gps_frame.grid_columnconfigure(1, weight=1)
        self.lbl_time = self.create_data_label(gps_frame, "Hora UTC: --:--:--")
        self.lbl_time.grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=1)
        self.lbl_lat = self.create_data_label(gps_frame, "Lat: --")
        self.lbl_lat.grid(row=1, column=0, sticky=tk.W, pady=1)
        self.lbl_lon = self.create_data_label(gps_frame, "Lon: --")
        self.lbl_lon.grid(row=1, column=1, sticky=tk.W, pady=1)
        self.lbl_alt = self.create_data_label(gps_frame, "Alt GPS: -- m", COLOR_TEXT_MAIN, 9, True)
        self.lbl_alt.grid(row=2, column=0, sticky=tk.W, pady=1)
        self.lbl_altb = self.create_data_label(gps_frame, "Alt Baro: -- m")
        self.lbl_altb.grid(row=2, column=1, sticky=tk.W, pady=1)
        self.lbl_sat = self.create_data_label(gps_frame, "Sats: -- (Fix: --)", COLOR_TEXT_MUTED)
        self.lbl_sat.grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=1)
        self.lbl_wind_speed = self.create_data_label(gps_frame, "Vel. Vento: -- m/s", COLOR_ACCENT_GREEN, 9, True)
        self.lbl_wind_speed.grid(row=4, column=0, sticky=tk.W, pady=1)
        self.lbl_wind_dir = self.create_data_label(gps_frame, "Dir. Vento: --°", COLOR_ACCENT_GREEN, 9, True)
        self.lbl_wind_dir.grid(row=4, column=1, sticky=tk.W, pady=1)
        self.lbl_vert_speed = self.create_data_label(gps_frame, "Vel. Vertical: -- m/s", COLOR_ACCENT_BLUE, 9, True)
        self.lbl_vert_speed.grid(row=5, column=0, columnspan=2, sticky=tk.W, pady=1)

        # PTU: agora só Temperatura e Pressão (sem sensor de umidade nesse conjunto)
        ptu_frame = self.create_card_frame(self.left_frame, "TELEMETRIA AMBIENTAL (PT)")
        ptu_frame.pack(fill=tk.X, pady=(0, 6))
        ptu_frame.grid_columnconfigure(0, weight=1)
        ptu_frame.grid_columnconfigure(1, weight=1)
        self.lbl_temp = self.create_data_label(ptu_frame, "T: -- °C")
        self.lbl_temp.grid(row=0, column=0, sticky=tk.W)
        self.lbl_press = self.create_data_label(ptu_frame, "P: -- hPa")
        self.lbl_press.grid(row=0, column=1, sticky=tk.W)

        # Bateria e status do telecomando (Bat / Ack)
        status_frame = self.create_card_frame(self.left_frame, "ENERGIA & TELECOMANDO")
        status_frame.pack(fill=tk.X, pady=(0, 6))
        status_frame.grid_columnconfigure(0, weight=1)
        status_frame.grid_columnconfigure(1, weight=1)
        self.lbl_bat = self.create_data_label(status_frame, "Bateria: -- V", COLOR_ACCENT_YELLOW, 9, True)
        self.lbl_bat.grid(row=0, column=0, sticky=tk.W, pady=1)
        self.lbl_ack = self.create_data_label(status_frame, "Ack: --", COLOR_TEXT_MUTED, 9, False)
        self.lbl_ack.grid(row=0, column=1, sticky=tk.W, pady=1)

        # Dinâmica de voo: atitude (Pitch/Roll/Yaw) + aceleração instantânea e média
        imu_frame = self.create_card_frame(self.left_frame, "DINÂMICA DE VOO")
        imu_frame.pack(fill=tk.X)
        for i in range(4): imu_frame.grid_columnconfigure(i, weight=1)
        self.create_data_label(imu_frame, "Atitude", COLOR_TEXT_MUTED, 8, True).grid(row=0, column=0, sticky=tk.W)
        self.lbl_pitch = self.create_data_label(imu_frame, "P: --°"); self.lbl_pitch.grid(row=0, column=1, sticky=tk.W)
        self.lbl_roll = self.create_data_label(imu_frame, "R: --°"); self.lbl_roll.grid(row=0, column=2, sticky=tk.W)
        self.lbl_yaw = self.create_data_label(imu_frame, "Y: --°"); self.lbl_yaw.grid(row=0, column=3, sticky=tk.W)
        self.create_data_label(imu_frame, "Acel. Inst.", COLOR_TEXT_MUTED, 8, True).grid(row=1, column=0, sticky=tk.W)
        self.lbl_ax = self.create_data_label(imu_frame, "X: --"); self.lbl_ax.grid(row=1, column=1, sticky=tk.W)
        self.lbl_ay = self.create_data_label(imu_frame, "Y: --"); self.lbl_ay.grid(row=1, column=2, sticky=tk.W)
        self.lbl_az = self.create_data_label(imu_frame, "Z: --"); self.lbl_az.grid(row=1, column=3, sticky=tk.W)
        self.create_data_label(imu_frame, "Acel. Média 1s", COLOR_TEXT_MUTED, 8, True).grid(row=2, column=0, sticky=tk.W)
        self.lbl_axavg = self.create_data_label(imu_frame, "X: --"); self.lbl_axavg.grid(row=2, column=1, sticky=tk.W)
        self.lbl_ayavg = self.create_data_label(imu_frame, "Y: --"); self.lbl_ayavg.grid(row=2, column=2, sticky=tk.W)
        self.lbl_azavg = self.create_data_label(imu_frame, "Z: --"); self.lbl_azavg.grid(row=2, column=3, sticky=tk.W)

        # --- CONTEÚDO DA COLUNA CENTRAL (MAPA EXPANDIDO) ---
        self.map_widget = tkintermapview.TkinterMapView(self.center_map_frame, corner_radius=6)
        self.map_widget.pack(fill=tk.BOTH, expand=True)
        self.map_widget.set_position(self.telemetry["Lat"], self.telemetry["Lon"])
        self.map_widget.set_zoom(14)

        # --- CONTEÚDO DA COLUNA DIREITA (3 GRÁFICOS VERTICAIS COMPACTOS) ---
        self.graph_card_frame = tk.Frame(self.right_graph_container, bg=COLOR_BG_CARD, highlightbackground=COLOR_GRAPH_GRID, highlightthickness=1)
        self.graph_card_frame.pack(fill=tk.BOTH, expand=True)
        
        self.fig = Figure(figsize=(4.0, 7.8))
        self.fig.patch.set_facecolor(COLOR_BG_CARD)
        
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.graph_card_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

    def change_map_layer(self, event):
        layer = self.map_layer_cb.get()
        if layer == "Padrão":
            self.map_widget.set_tile_server("https://a.tile.openstreetmap.org/{z}/{x}/{y}.png")
        elif layer == "Satélite":
            self.map_widget.set_tile_server("https://mt0.google.com/vt/lyrs=s&x={x}&y={y}&z={z}")
        elif layer == "Topográfico":
            self.map_widget.set_tile_server("https://a.tile.opentopomap.org/{z}/{x}/{y}.png")

    def refresh_ports(self):
        ports = serial.tools.list_ports.comports()
        self.port_cb['values'] = [port.device for port in ports]
        if ports: self.port_cb.current(0)

    def toggle_connection(self):
        if not self.is_connected:
            port = self.port_cb.get()
            if not port:
                messagebox.showerror("Erro", "Selecione uma porta COM ativa.")
                return
            try:
                self.serial_port = serial.Serial(port, 115200, timeout=1)
                self.is_connected = True
                self.btn_connect.config(text="Desconectar", bg="#e63946", fg="white")
                self.port_cb.config(state="disabled")
                
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                self.log_file = open(f"telemetria_{timestamp}.txt", "a", encoding="utf-8")
                
                self.read_thread = threading.Thread(target=self.read_serial_data, daemon=True)
                self.read_thread.start()
            except Exception as e:
                messagebox.showerror("Falha de Conexão", str(e))
        else:
            self.disconnect_serial()

    def disconnect_serial(self):
        self.is_connected = False
        if self.serial_port and self.serial_port.is_open: self.serial_port.close()
        if self.log_file and not self.log_file.closed: self.log_file.close()
        self.btn_connect.config(text="Conectar", bg="#202024", fg=COLOR_TEXT_MAIN)
        self.port_cb.config(state="readonly")

    def read_serial_data(self):
        while self.is_connected and self.serial_port.is_open:
            try:
                line = self.serial_port.readline().decode('utf-8', errors='ignore').strip()
                if not line: continue
                
                if self.log_file and not self.log_file.closed:
                    self.log_file.write(line + "\n")
                    self.log_file.flush()
                
                if "Texto Bruto:" in line:
                    parts = line.split(":", 1)
                    self.telemetry["Texto Bruto"] = parts[1].strip()
                elif "-------" in line:
                    call_match = re.search(r"-------\s*([\w\d]+)\s*-------", line)
                    if call_match: self.telemetry["Texto Bruto"] = call_match.group(1)
                elif "RSSI:" in line and "SNR:" in line:
                    match_rssi = re.search(r"RSSI:\s*([-\d]+)", line)
                    match_snr = re.search(r"SNR:\s*([-\d]+)", line)
                    if match_rssi: self.telemetry["RSSI"] = match_rssi.group(1)
                    if match_snr: self.telemetry["SNR"] = match_snr.group(1)
                elif ":" in line:
                    # Chaves compostas (AXavg, AYavg, AZavg) precisam ser
                    # comparadas ANTES das simples (AX, AY, AZ), senão o
                    # split gera uma chave "AXavg" que nunca bateria com a
                    # tentativa de achar "AX" via prefixo. Como usamos
                    # split(":", 1) isso na verdade nao e' um problema aqui
                    # (a chave inteira antes do ":" já vem certa do bordo/solo),
                    # mas mantemos o comentario para deixar claro que a ordem
                    # de leitura no firmware e' o que garante nao haver colisao.
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val = parts[1].strip()
                        self.telemetry[key] = val
                        
                        # "Ack:" e' o ultimo campo impresso pela estacao de
                        # solo em cada pacote -> gatilho seguro para "pacote
                        # completo, hora de atualizar a tela e o historico".
                        if key == "Ack":
                            self.append_to_history()
                            self.last_packet_time = datetime.datetime.now() 
                            self.needs_gui_update = True
                            
            except Exception as e:
                print(f"Erro no processador serial: {e}")
                break

    def calculate_flight_dynamics(self, lat2, lon2, alt2, time2_str):
        if not self.last_gps_data: return None
        try:
            lat1, lon1, alt1, time1_str = self.last_gps_data["lat"], self.last_gps_data["lon"], self.last_gps_data["alt"], self.last_gps_data["time"]
            t1 = datetime.datetime.strptime(time1_str, "%H:%M:%S")
            t2 = datetime.datetime.strptime(time2_str, "%H:%M:%S")
            dt = (t2 - t1).total_seconds()
            if dt <= 0: dt = 1.0
            
            v_vertical = (alt2 - alt1) / dt
            
            R = 6371000.0 
            phi1, phi2 = math.radians(lat1), math.radians(lat2)
            d_phi, d_lon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
            
            a = math.sin(d_phi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(d_lon/2)**2
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
            wind_speed_ms = (R * c) / dt
            
            y = math.sin(d_lon) * math.cos(phi2)
            x = math.cos(phi1)*math.sin(phi2) - math.sin(phi1)*math.cos(phi2)*math.cos(d_lon)
            heading = (math.degrees(math.atan2(y, x)) + 360) % 360
            wind_direction = (heading + 180) % 360
            
            return wind_speed_ms, wind_direction, v_vertical
        except Exception:
            return None

    def append_to_history(self):
        current_time = self.telemetry.get("Time", datetime.datetime.now().strftime("%H:%M:%S"))
        try:
            t_val = float(self.telemetry.get("T", 0.0))
            alt_val = float(self.telemetry.get("Alt", 0.0))
            p_val = float(self.telemetry.get("P", 0.0))
            bat_val = float(self.telemetry.get("Bat", 0.0))
            lat_val = float(self.telemetry.get("Lat", 0.0))
            lon_val = float(self.telemetry.get("Lon", 0.0))
            
            if not self.history_time or self.history_time[-1] != current_time:
                self.history_time.append(current_time)
                self.history_temp.append(t_val)
                self.history_alt.append(alt_val)
                self.history_press.append(p_val)
                self.history_bat.append(bat_val)
            
            if lat_val != 0.0 and lon_val != 0.0:
                nova_coord = (lat_val, lon_val)
                if not self.path_coordinates or self.path_coordinates[-1] != nova_coord:
                    self.path_coordinates.append(nova_coord)
                    dynamics = self.calculate_flight_dynamics(lat_val, lon_val, alt_val, current_time)
                    if dynamics:
                        v_speed, v_dir, v_vert = dynamics
                        self.telemetry["WindSpeed"] = f"{v_speed:.1f}"
                        self.telemetry["WindDir"] = f"{v_dir:.0f}"
                        self.telemetry["VertSpeed"] = f"{'+' if v_vert >= 0 else ''}{v_vert:.1f}"
                        
                    self.last_gps_data = {"lat": lat_val, "lon": lon_val, "alt": alt_val, "time": current_time}
        except ValueError:
            pass

    def gui_updater_loop(self):
        try:
            if self.last_packet_time:
                elapsed = (datetime.datetime.now() - self.last_packet_time).total_seconds()
                self.lbl_packet_age.config(text=f"Último Pacote: {int(elapsed)}s atrás", fg=COLOR_TEXT_MAIN)
                if elapsed > 10: self.lbl_packet_age.config(fg="#e63946")
            else:
                self.lbl_packet_age.config(text="Último Pacote: Aguardando...", fg=COLOR_TEXT_MUTED)

            if self.needs_gui_update:
                self.update_gui()
                self.update_charts()
                self.needs_gui_update = False
        except Exception as e:
            print(f"Instabilidade capturada no ciclo UI: {e}")
        finally:
            self.root.after(1000, self.gui_updater_loop)

    def update_charts(self):
        if not self.history_time:
            return
            
        try:
            self.fig.clear()
            
            ax_alt = self.fig.add_subplot(311)
            ax_temp = self.fig.add_subplot(312)
            ax_press = self.fig.add_subplot(313)

            # Mapeamento do Eixo X usando os índices para renderização otimizada
            indices = list(range(len(self.history_time)))
            step = max(1, len(self.history_time) // 4)
            tick_indices = indices[::step]
            # O rótulo recebe a String de hora do GPS da sonda
            tick_labels = [self.history_time[i] for i in tick_indices]

            for ax in [ax_alt, ax_temp, ax_press]:
                ax.set_facecolor(COLOR_BG_MAIN)
                ax.tick_params(axis='both', colors=COLOR_TEXT_MUTED, labelsize=7)
                ax.grid(True, color=COLOR_GRAPH_GRID, linestyle='-', alpha=0.5)
                ax.set_xticks(tick_indices)
                ax.set_xticklabels(tick_labels, rotation=25, ha='right', fontsize=6)
                for spine in ax.spines.values(): spine.set_visible(False)

            # Plot 1: Altitude (m) vs Hora GPS
            ax_alt.plot(indices, self.history_alt, color='#00b4d8', linewidth=1.5)
            ax_alt.set_title("Altitude (m)", fontsize=8, fontweight='bold', color=COLOR_TEXT_MAIN, pad=2)

            # Plot 2: Temperatura + Bateria (sem sensor de umidade neste conjunto)
            ax_temp.plot(indices, self.history_temp, color='#ff4757', linewidth=1.5, label="T (°C)")
            ax_temp.plot(indices, self.history_bat, color=COLOR_ACCENT_YELLOW, linewidth=1.1, linestyle='--', label="Bat (V)")
            ax_temp.set_title("Temperatura & Bateria", fontsize=8, fontweight='bold', color=COLOR_TEXT_MAIN, pad=2)
            ax_temp.legend(loc="upper right", fontsize=6, facecolor=COLOR_BG_CARD, edgecolor='none', labelcolor=COLOR_TEXT_MAIN)

            # Plot 3: Pressão (hPa) vs Hora GPS
            ax_press.plot(indices, self.history_press, color='#04d361', linewidth=1.5)
            ax_press.set_title("Pressão Atmosférica (hPa)", fontsize=8, fontweight='bold', color=COLOR_TEXT_MAIN, pad=2)
            ax_press.set_xlabel("Hora do GPS (Sonda)", color=COLOR_TEXT_MUTED, fontsize=8)

            try: self.fig.tight_layout()
            except Exception: pass 
                
            self.canvas.draw()
        except Exception as e:
            print(f"Falha na renderização do canvas: {e}")

    def update_gui(self):
        self.lbl_callsign.config(text=f"CALLSIGN: {self.telemetry.get('Texto Bruto', '--')}")
        self.lbl_rssi.config(text=f"RSSI: {self.telemetry.get('RSSI', '--')} dBm")
        self.lbl_snr.config(text=f"SNR: {self.telemetry.get('SNR', '--')} dB")

        try:
            lat_val, lon_val = float(self.telemetry.get('Lat', 0)), float(self.telemetry.get('Lon', 0))
            lat_str = f"{lat_val:.7f}" if lat_val != 0.0 else "--"
            lon_str = f"{lon_val:.7f}" if lon_val != 0.0 else "--"
        except ValueError:
            lat_str = lon_str = "--"

        self.lbl_time.config(text=f"Hora UTC: {self.telemetry.get('Time', '--:--:--')}")
        self.lbl_lat.config(text=f"Lat: {lat_str}")
        self.lbl_lon.config(text=f"Lon: {lon_str}")
        self.lbl_alt.config(text=f"Alt GPS: {self.telemetry.get('Alt', '--')} m")
        self.lbl_altb.config(text=f"Alt Baro: {self.telemetry.get('AltB', '--')} m")
        self.lbl_sat.config(text=f"Sats: {self.telemetry.get('Sat', '--')} (Fix: {self.telemetry.get('Fix', '--')})")
        self.lbl_wind_speed.config(text=f"Vel. Vento: {self.telemetry.get('WindSpeed')} m/s")
        self.lbl_wind_dir.config(text=f"Dir. Vento: {self.telemetry.get('WindDir')}°")
        self.lbl_vert_speed.config(text=f"Vel. Vertical: {self.telemetry.get('VertSpeed')} m/s")
        self.lbl_temp.config(text=f"T: {self.telemetry.get('T', '--')} °C")
        self.lbl_press.config(text=f"P: {self.telemetry.get('P', '--')} hPa")

        self.lbl_bat.config(text=f"Bateria: {self.telemetry.get('Bat', '--')} V")
        self.lbl_ack.config(text=f"Ack: {self.telemetry.get('Ack', '--')}")

        self.lbl_pitch.config(text=f"P: {self.telemetry.get('Pitch', '--')}°")
        self.lbl_roll.config(text=f"R: {self.telemetry.get('Roll', '--')}°")
        self.lbl_yaw.config(text=f"Y: {self.telemetry.get('Yaw', '--')}°")
        self.lbl_ax.config(text=f"X: {self.telemetry.get('AX', '--')}")
        self.lbl_ay.config(text=f"Y: {self.telemetry.get('AY', '--')}")
        self.lbl_az.config(text=f"Z: {self.telemetry.get('AZ', '--')}")
        self.lbl_axavg.config(text=f"X: {self.telemetry.get('AXavg', '--')}")
        self.lbl_ayavg.config(text=f"Y: {self.telemetry.get('AYavg', '--')}")
        self.lbl_azavg.config(text=f"Z: {self.telemetry.get('AZavg', '--')}")

        try:
            lat, lon, alt = float(self.telemetry.get('Lat', 0)), float(self.telemetry.get('Lon', 0)), self.telemetry.get('Alt', '--')
            callsign, horario = self.telemetry.get('Texto Bruto', 'Sonda'), self.telemetry.get('Time', '--:--:--')
            
            if lat != 0.0 and lon != 0.0:
                balloon_text = f"ID: {callsign}\nLat: {lat:.7f}\nLon: {lon:.7f}\nAlt: {alt}m\nHora: {horario}"
                if self.current_marker is None:
                    self.current_marker = self.map_widget.set_marker(lat, lon, text=balloon_text)
                else:
                    self.current_marker.set_position(lat, lon)
                    self.current_marker.text = balloon_text
                    
                if len(self.path_coordinates) > 1:
                    if self.track_line: self.track_line.delete()
                    self.track_line = self.map_widget.set_path(self.path_coordinates, color=COLOR_TRACK_LINE, width=3)
        except ValueError: pass

if __name__ == "__main__":
    root = tk.Tk()
    app = SondeTrackerApp(root)
    def on_closing():
        app.disconnect_serial()
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()