import pandas as pd
import math
import matplotlib.pyplot as plt

# Função de Haversine para calcular a distância terrestre real entre duas coordenadas
def haversine(lat1, lon1, lat2, lon2):
    R = 6371000 # Raio da Terra em metros
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi/2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda/2.0)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

all_lats, all_lons = [], []
lat_int, lon_int, alt_int, ack_int = [], [], [], []

# Escanear a telemetria do voo
with open('telemetria_voo_completo.txt', 'r', encoding='utf-8', errors='ignore') as f:
    clat = clon = calt = None
    for line in f:
        line = line.strip()
        if line.startswith('Lat:'): clat = float(line.split(':')[1])
        elif line.startswith('Lon:'): clon = float(line.split(':')[1])
        elif line.startswith('Alt:'): calt = float(line.split(':')[1])
        elif line.startswith('Ack:'):
            ack_val = int(line.split(':')[1])
            if clat is not None and clon is not None:
                all_lats.append(clat)
                all_lons.append(clon)
                
                # Se o Ack for maior que zero, o telecomando foi recebido
                if ack_val > 0:
                    lat_int.append(clat)
                    lon_int.append(clon)
                    alt_int.append(calt)
                    ack_int.append(ack_val)
            # Reseta as variáveis para o próximo pacote
            clat = clon = calt = None

# A primeira coordenada válida do voo é definida como a Estação de Rastreio Fixa
base_lat = all_lats[0]
base_lon = all_lons[0]

# Organizar os comandos recebidos em um DataFrame e calcular as distâncias
df_cmds = pd.DataFrame({'Lat': lat_int, 'Lon': lon_int, 'Alt': alt_int, 'Ack': ack_int})
df_cmds['Dist_km'] = df_cmds.apply(lambda row: haversine(base_lat, base_lon, row['Lat'], row['Lon']) / 1000.0, axis=1)

# Configuração do Mapa e Plotagem
plt.figure(figsize=(12, 10))
plt.plot(all_lons, all_lats, color='lightgray', linestyle='--', linewidth=1.5, label='Trajetória do Voo')
plt.scatter([base_lon], [base_lat], color='black', marker='*', s=300, label='Estação de Rastreio Fixa')

# Plotagem dos pontos de telecomando baseados na altitude
scatter = plt.scatter(df_cmds['Lon'], df_cmds['Lat'], c=df_cmds['Alt'], cmap='plasma', s=60, edgecolor='black', zorder=5)
cbar = plt.colorbar(scatter)
cbar.set_label('Altitude (metros)', fontsize=12)

# Adicionando rótulos de distância e altitude em cada ponto
for i, row in df_cmds.iterrows():
    plt.annotate(f"{row['Dist_km']:.1f}km\n{row['Alt']}m", 
                 (row['Lon'], row['Lat']), 
                 fontsize=8, 
                 xytext=(5, 5), 
                 textcoords='offset points')

plt.title('Mapa de Telecomandos Recebidos\nDistância Horizontal e Altitude em relação à Estação Fixa', fontsize=14)
plt.xlabel('Longitude', fontsize=12)
plt.ylabel('Latitude', fontsize=12)
plt.legend(fontsize=11)
plt.grid(True, linestyle=':', alpha=0.6)
plt.tight_layout()
plt.show()