import os
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

def analisar_arquivos_telemetria(arquivos):
    """Lê os arquivos de texto e extrai os blocos de dados de telemetria."""
    dados_consolidados = []
    
    for arquivo in arquivos:
        if not os.path.exists(arquivo):
            print(f"Aviso: Arquivo '{arquivo}' não encontrado.")
            continue
            
        registro_atual = {}
        print(f"Processando: {arquivo}...")
        
        with open(arquivo, 'r', encoding='utf-8') as f:
            for linha in f:
                linha = linha.strip()
                
                # Delimitadores de início e fim de bloco
                if linha == "------- PT2UNB -------":
                    registro_atual = {}
                elif linha == "----------------------":
                    if registro_atual:
                        dados_consolidados.append(registro_atual)
                        registro_atual = {}
                
                # Tratamento da linha de sinal (RSSI e SNR)
                elif linha.startswith("RSSI:"):
                    partes = linha.split("|")
                    if len(partes) == 2:
                        rssi = partes[0].replace("RSSI:", "").replace("dBm", "").strip()
                        snr = partes[1].replace("SNR:", "").replace("dB", "").strip()
                        try:
                            registro_atual["RSSI"] = float(rssi)
                            registro_atual["SNR"] = float(snr)
                        except ValueError:
                            pass
                
                # Tratamento das demais variáveis no formato Chave:Valor
                else:
                    partes = linha.split(":")
                    if len(partes) >= 2:
                        chave = partes[0].strip()
                        # Junta novamente caso o valor tenha ':' (ex: tempo "15:58:33")
                        valor = ":".join(partes[1:]).strip() 
                        
                        if chave == "Time":
                            registro_atual[chave] = valor
                        else:
                            try:
                                registro_atual[chave] = float(valor)
                            except ValueError:
                                pass
                                
    return dados_consolidados

def plotar_painel_telemetria(df):
    """Gera um painel com 8 gráficos baseados no DataFrame de telemetria."""
    # Configuração de estilo visual
    plt.style.use('default')
    fig, axes = plt.subplots(4, 2, figsize=(16, 20), sharex=True)
    
    tempo = df['DateTime']

    # 1. Altitude
    axes[0,0].plot(tempo, df.get('Alt'), label='Altitude GPS (m)', color='blue')
    axes[0,0].plot(tempo, df.get('AltB'), label='Altitude Baro (m)', color='cyan')
    axes[0,0].set_title('Altitude')
    axes[0,0].set_ylabel('Metros (m)')
    axes[0,0].legend()
    axes[0,0].grid(True, linestyle='--', alpha=0.7)

    # 2. Temperatura
    axes[0,1].plot(tempo, df.get('T'), label='Temperatura (°C)', color='red')
    axes[0,1].set_title('Temperatura Externa')
    axes[0,1].set_ylabel('°C')
    axes[0,1].legend()
    axes[0,1].grid(True, linestyle='--', alpha=0.7)

    # 3. Pressão
    axes[1,0].plot(tempo, df.get('P'), label='Pressão (hPa)', color='purple')
    axes[1,0].set_title('Pressão Atmosférica')
    axes[1,0].set_ylabel('hPa')
    axes[1,0].legend()
    axes[1,0].grid(True, linestyle='--', alpha=0.7)

    # 4. Bateria
    axes[1,1].plot(tempo, df.get('Bat'), label='Bateria (V)', color='green')
    axes[1,1].set_title('Tensão da Bateria')
    axes[1,1].set_ylabel('Volts (V)')
    axes[1,1].legend()
    axes[1,1].grid(True, linestyle='--', alpha=0.7)

    # 5. IMU - Ângulos
    axes[2,0].plot(tempo, df.get('Pitch'), label='Pitch', alpha=0.8)
    axes[2,0].plot(tempo, df.get('Roll'), label='Roll', alpha=0.8)
    axes[2,0].plot(tempo, df.get('Yaw'), label='Yaw', alpha=0.8)
    axes[2,0].set_title('Movimento Inercial - Ângulos')
    axes[2,0].set_ylabel('Graus (°)')
    axes[2,0].legend()
    axes[2,0].grid(True, linestyle='--', alpha=0.7)

    # 6. IMU - Aceleração
    axes[2,1].plot(tempo, df.get('AXavg'), label='AX Avg', alpha=0.8)
    axes[2,1].plot(tempo, df.get('AYavg'), label='AY Avg', alpha=0.8)
    axes[2,1].plot(tempo, df.get('AZavg'), label='AZ Avg', alpha=0.8)
    axes[2,1].set_title('Movimento Inercial - Aceleração')
    axes[2,1].set_ylabel('g (m/s²)')
    axes[2,1].legend()
    axes[2,1].grid(True, linestyle='--', alpha=0.7)

    # 7. Sinal de Rádio (Dois eixos Y)
    ax7 = axes[3,0]
    ax7_2 = ax7.twinx()
    linha1 = ax7.plot(tempo, df.get('RSSI'), label='RSSI (dBm)', color='darkblue', alpha=0.7)
    linha2 = ax7_2.plot(tempo, df.get('SNR'), label='SNR (dB)', color='orange', alpha=0.7)
    
    ax7.set_title('Qualidade do Sinal (LoRa)')
    ax7.set_ylabel('RSSI (dBm)')
    ax7_2.set_ylabel('SNR (dB)')
    
    linhas = linha1 + linha2
    labels = [l.get_label() for l in linhas]
    ax7.legend(linhas, labels, loc='lower right')
    ax7.grid(True, linestyle='--', alpha=0.7)

    # 8. Satélites
    axes[3,1].plot(tempo, df.get('Sat'), label='Satélites Visíveis', color='magenta')
    axes[3,1].set_title('Cobertura GNSS')
    axes[3,1].set_ylabel('Quantidade')
    axes[3,1].legend()
    axes[3,1].grid(True, linestyle='--', alpha=0.7)

    # Formatação do eixo X (Tempo)
    for ax in axes.flat:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        ax.tick_params(axis='x', rotation=45)

    plt.tight_layout()
    plt.show()

# ---------------------------------------------------------
# Execução Principal
# ---------------------------------------------------------
if __name__ == "__main__":
    # Lista com os arquivos que deseja analisar
    arquivos_entrada = [
        "telemetria_20260919_110107.txt",
        "telemetria_20260919_112448.txt",
        "telemetria_20260919_113050.txt",
    ]
    
    # Processa os arquivos
    dados_brutos = analisar_arquivos_telemetria(arquivos_entrada)
    
    if not dados_brutos:
        print("Nenhum dado válido encontrado. Verifique o nome dos arquivos.")
    else:
        # Converte a lista de dicionários em um DataFrame Pandas
        df = pd.DataFrame(dados_brutos)
        
        # Cria uma coluna datetime para o eixo X, assumindo a data do arquivo
        # Isso evita que o matplotlib plote o horário de forma dessincronizada
        df['DateTime'] = pd.to_datetime('2026-09-19 ' + df['Time'])
        
        # Ordena cronologicamente e reseta o índice
        df = df.sort_values('DateTime').reset_index(drop=True)
        
        print(f"Total de {len(df)} registros processados com sucesso.")
        
        # Gera os gráficos
        plotar_painel_telemetria(df)