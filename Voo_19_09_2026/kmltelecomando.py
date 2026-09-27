import math

# Fórmula para calcular distância entre duas coordenadas
def haversine(lat1, lon1, lat2, lon2):
    R = 6371000 # Raio da Terra (metros)
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi/2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda/2.0)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

all_points = []
cmd_points = []

# Lendo a telemetria
with open('telemetria_voo_completo.txt', 'r', encoding='utf-8', errors='ignore') as f:
    clat = clon = calt = ctime = None
    for line in f:
        line = line.strip()
        if line.startswith('Lat:'): clat = float(line.split(':')[1])
        elif line.startswith('Lon:'): clon = float(line.split(':')[1])
        elif line.startswith('Alt:'): calt = float(line.split(':')[1])
        elif line.startswith('Time:'): ctime = line.split('Time:')[1]
        elif line.startswith('Ack:'):
            ack_val = int(line.split(':')[1])
            if clat is not None and clon is not None and calt is not None:
                all_points.append((clon, clat, calt))
                if ack_val > 0:
                    cmd_points.append({'lat': clat, 'lon': clon, 'alt': calt, 'ack': ack_val, 'time': ctime})
            clat = clon = calt = ctime = None

base_lon, base_lat, base_alt = all_points[0]

# Estrutura inicial do arquivo KML
kml_content = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Telecomandos e Trajetoria PT2UNB</name>
    <description>Mapa 3D de telecomandos recebidos e trajetória do voo</description>
    
    <Style id="trajStyle">
      <LineStyle>
        <color>ff00ffff</color> <!-- Amarelo sólido no KML -->
        <width>4</width>
      </LineStyle>
    </Style>
    
    <Style id="cmdStyle">
      <IconStyle>
        <Icon>
          <href>http://maps.google.com/mapfiles/kml/paddle/red-circle.png</href>
        </Icon>
      </IconStyle>
    </Style>
    
    <Style id="baseStyle">
      <IconStyle>
        <Icon>
          <href>http://maps.google.com/mapfiles/kml/paddle/ylw-star.png</href>
        </Icon>
      </IconStyle>
    </Style>

    <!-- Estação Base (Térrea) -->
    <Placemark>
      <name>Estação de Rastreio (Base)</name>
      <description>Local de lançamento e controle.</description>
      <styleUrl>#baseStyle</styleUrl>
      <Point>
        <altitudeMode>absolute</altitudeMode>
        <coordinates>{},{},{}</coordinates>
      </Point>
    </Placemark>

    <!-- Linha da Trajetória 3D -->
    <Placemark>
      <name>Trajetória do Voo</name>
      <styleUrl>#trajStyle</styleUrl>
      <LineString>
        <extrude>1</extrude>
        <tessellate>1</tessellate>
        <altitudeMode>absolute</altitudeMode>
        <coordinates>
""".format(base_lon, base_lat, base_alt)

# Injetar os milhares de pontos de voo para criar a linha 3D contínua
for lon, lat, alt in all_points:
    kml_content += f"          {lon},{lat},{alt}\n"

kml_content += """        </coordinates>
      </LineString>
    </Placemark>

    <!-- Marcadores de Telecomando Recebidos -->
"""

# Injetar cada telecomando como um "Placemark" (Bandeira)
for cmd in cmd_points:
    dist_km = haversine(base_lat, base_lon, cmd['lat'], cmd['lon']) / 1000.0
    
    kml_content += f"""    <Placemark>
      <name>{dist_km:.1f} km | {cmd['alt']} m</name>
      <description><![CDATA[
        <h3>Comando Recebido</h3>
        <b>Horário:</b> {cmd['time']}<br>
        <b>Altitude Exata:</b> {cmd['alt']} m<br>
        <b>Dist. Horizontal da Base:</b> {dist_km:.2f} km<br>
        <b>Ack ID:</b> {cmd['ack']}
      ]]></description>
      <styleUrl>#cmdStyle</styleUrl>
      <Point>
        <altitudeMode>absolute</altitudeMode>
        <coordinates>{cmd['lon']},{cmd['lat']},{cmd['alt']}</coordinates>
      </Point>
    </Placemark>
"""

# Fechar as tags do KML
kml_content += """  </Document>
</kml>
"""

# Salvar o arquivo final
with open('telecomandos.kml', 'w', encoding='utf-8') as f:
    f.write(kml_content)

print("Pronto! Arquivo 'telecomandos.kml' gerado. Dê um duplo clique nele para abrir no Google Earth.")