import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import os

# Função para carregar os arquivos e extrair telemetrias com ACK
def parse_telemetry_with_acks(filepath):
    data = []
    current_record = {}
    if not os.path.exists(filepath):
        return data
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line == "------- PT2UNB -------":
                current_record = {}
            elif line == "----------------------":
                if current_record:
                    data.append(current_record)
                    current_record = {}
            else:
                if line.startswith("RSSI:"):
                    parts = line.split("|")
                    if len(parts) == 2:
                        try:
                            current_record["RSSI"] = float(parts[0].replace("RSSI:", "").replace("dBm", "").strip())
                            current_record["SNR"] = float(parts[1].replace("SNR:", "").replace("dB", "").strip())
                        except ValueError:
                            pass
                else:
                    parts = line.split(":")
                    if len(parts) >= 2:
                        key = parts[0].strip()
                        val = ":".join(parts[1:]).strip()
                        if key == "Time":
                            current_record[key] = val
                        else:
                            try:
                                current_record[key] = float(val)
                            except ValueError:
                                pass
    return data

# Lendo os arquivos
arquivos = [
    'telemetria_20260919_111143.txt', 
    'telemetria_20260919_125336.txt',
    'telemetria_20260919_125336_2.txt'
]
data = []
for f in arquivos:
    data.extend(parse_telemetry_with_acks(f))

# Montando o DataFrame
df = pd.DataFrame(data)
df['DateTime'] = pd.to_datetime('2026-09-19 ' + df['Time'])
df = df.sort_values('DateTime').reset_index(drop=True)

# Filtrando apenas comandos com ACK retornado
df_acks = df[df['Ack'] > 0].copy()

# Fórmula de Haversine
def haversine(lat1, lon1, lat2, lon2):
    R = 6371000 # Raio da Terra em metros
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlam = np.radians(lon2 - lon1)
    a = np.sin(dphi/2)**2 + np.cos(phi1)*np.cos(phi2)*np.sin(dlam/2)**2
    c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1-a))
    return R * c

# Posição da Base
station_lat = -15.763780609932054
station_lon = -47.86832605485871
station_alt_gps = 1021.9

# Cálculos de Distância
df_acks['Dist_Solo_m'] = haversine(station_lat, station_lon, df_acks['Lat'], df_acks['Lon'])
df_acks['Dist_Real_Linha_Reta_km'] = np.sqrt(df_acks['Dist_Solo_m']**2 + (df_acks['Alt'] - station_alt_gps)**2) / 1000.0

# Plotagem
plt.style.use('default')
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))

# Gráfico 1: Distância vs RSSI colorido pela Altitude
sc = ax1.scatter(df_acks['Dist_Real_Linha_Reta_km'], df_acks['RSSI'], 
                 c=df_acks['Alt'], cmap='plasma', s=80, alpha=0.8, edgecolors='k')
ax1.set_title('Força do Sinal (RSSI) vs Distância do Telecomando', fontsize=12)
ax1.set_xlabel('Distância em Linha Reta (km)', fontsize=10)
ax1.set_ylabel('RSSI (dBm)', fontsize=10)
ax1.grid(True, linestyle='--', alpha=0.7)
cbar = plt.colorbar(sc, ax=ax1)
cbar.set_label('Altitude do Balão (m)', fontsize=10)

# Gráfico 2: Distância e Altitude ao longo do Tempo (Eixos Gêmeos)
ax2.plot(df_acks['DateTime'], df_acks['Dist_Real_Linha_Reta_km'], marker='o', linestyle='-', color='purple', label='Distância Real (km)')
ax2.set_xlabel('Hora do Comando', fontsize=10)
ax2.set_ylabel('Distância Real (km)', fontsize=10, color='purple')
ax2.tick_params(axis='y', labelcolor='purple')
ax2.grid(True, linestyle='--', alpha=0.7)

ax2_twin = ax2.twinx()
ax2_twin.plot(df_acks['DateTime'], df_acks['Alt'], marker='s', linestyle='--', color='teal', alpha=0.6, label='Altitude (m)')
ax2_twin.set_ylabel('Altitude (m)', fontsize=10, color='teal')
ax2_twin.tick_params(axis='y', labelcolor='teal')

ax2.set_title('Evolução da Distância e Altitude dos Telecomandos no Tempo', fontsize=12)
ax2.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
ax2.tick_params(axis='x', rotation=45)

# Ajuste fino das legendas do gráfico 2
lines_1, labels_1 = ax2.get_legend_handles_labels()
lines_2, labels_2 = ax2_twin.get_legend_handles_labels()
ax2.legend(lines_1 + lines_2, labels_1 + labels_2, loc='upper left')

plt.tight_layout(w_pad=3.0)
plt.show()