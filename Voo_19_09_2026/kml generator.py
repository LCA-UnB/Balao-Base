import os

def extrair_coordenadas(caminho_arquivo):
    coordenadas = []
    lat = lon = alt = None
    
    if not os.path.exists(caminho_arquivo):
        print(f"Aviso: O arquivo {caminho_arquivo} não foi encontrado.")
        return coordenadas

    with open(caminho_arquivo, 'r', encoding='utf-8', errors='ignore') as f:
        for linha in f:
            linha = linha.strip()
            
            # Extrai os valores das chaves relevantes ignorando linhas de erro/aviso
            if linha.startswith("Lat:"):
                try:
                    lat = float(linha.split(":")[1])
                except ValueError:
                    pass
            elif linha.startswith("Lon:"):
                try:
                    lon = float(linha.split(":")[1])
                except ValueError:
                    pass
            elif linha.startswith("Alt:"):
                try:
                    alt = float(linha.split(":")[1])
                except ValueError:
                    pass
            elif linha == "----------------------":
                # Ao final de cada pacote, salva se tiver coordenadas válidas
                if lat is not None and lon is not None and alt is not None:
                    coordenadas.append((lon, lat, alt))
                lat = lon = alt = None
                
    return coordenadas

def gerar_kml(coordenadas, arquivo_saida):
    if not coordenadas:
        print("Nenhuma coordenada foi extraída. O KML não será gerado.")
        return

    # Cabeçalho padrão do KML com estilo de linha (amarela e espessa)
    kml = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Voo do Balão</name>
    <Style id="linhaTrajeto">
      <LineStyle>
        <color>ff00ffff</color> <!-- Amarelo sólido -->
        <width>4</width>
      </LineStyle>
      <PolyStyle>
        <color>7f00ffff</color> <!-- Sombra de extrusão semi-transparente -->
      </PolyStyle>
    </Style>
    <Placemark>
      <name>Trajeto Completo</name>
      <styleUrl>#linhaTrajeto</styleUrl>
      <LineString>
        <extrude>1</extrude>
        <tessellate>1</tessellate>
        <altitudeMode>absolute</altitudeMode>
        <coordinates>
"""
    # Adiciona as coordenadas no formato lon,lat,alt
    for lon, lat, alt in coordenadas:
        kml += f"          {lon},{lat},{alt}\n"

    # Rodapé do KML
    kml += """        </coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>
"""
    with open(arquivo_saida, 'w', encoding='utf-8') as f:
        f.write(kml)
    print(f"Arquivo KML gerado com sucesso: {arquivo_saida}")

# Nomes dos arquivos de entrada e saída
arquivos_entrada = ["telemetria_carro1.txt", "telemetria_carro2.txt"]
arquivo_saida = "voo_balao_completo.kml"

coordenadas_totais = []

print("Lendo arquivos de telemetria...")
for arquivo in arquivos_entrada:
    pts = extrair_coordenadas(arquivo)
    print(f"- {arquivo}: {len(pts)} pontos extraídos.")
    coordenadas_totais.extend(pts)

print(f"Total de pontos mesclados: {len(coordenadas_totais)}")
gerar_kml(coordenadas_totais, arquivo_saida)