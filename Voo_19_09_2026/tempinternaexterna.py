import pandas as pd
import matplotlib.pyplot as plt

# 1. Processar arquivo de telemetria interna
alt_int = []
temp_int = []

with open('telemetria_voo_completo.txt', 'r', encoding='utf-8', errors='ignore') as f:
    current_alt = None
    for line in f:
        line = line.strip()
        if line.startswith('Alt:'):
            try:
                current_alt = float(line.split(':')[1])
            except ValueError:
                pass
        elif line.startswith('T:'):
            try:
                t_val = float(line.split(':')[1])
                if current_alt is not None:
                    alt_int.append(current_alt)
                    temp_int.append(t_val)
                    current_alt = None
            except ValueError:
                pass

# 2. Processar arquivo da radiossonda externa
df_sonde = pd.read_csv('20260919-141701_X1909261_RS41_403502_sonde.log.txt')
df_sonde_valid = df_sonde[df_sonde['temp'] > -100]
alt_ext = df_sonde_valid['alt']
temp_ext = df_sonde_valid['temp']

# 3. Extrair Mínimas e Máximas
min_int, max_int = min(temp_int), max(temp_int)
min_ext, max_ext = temp_ext.min(), temp_ext.max()

# 4. Configurar e Gerar o Gráfico
plt.figure(figsize=(10, 8))

# Criando as labels dinâmicas
label_ext = f'Externa (Mín: {min_ext:.1f}°C | Máx: {max_ext:.1f}°C)'
label_int = f'Interna (Mín: {min_int:.1f}°C | Máx: {max_int:.1f}°C)'

plt.scatter(temp_ext, alt_ext, color='blue', alpha=0.5, s=15, label=label_ext)
plt.scatter(temp_int, alt_int, color='red', alpha=0.5, s=15, label=label_int)

plt.title('Temperatura vs Altitude (Voo 19/09/2026)', fontsize=14)
plt.ylabel('Altitude (metros)', fontsize=12)
plt.xlabel('Temperatura (°C)', fontsize=12)

# Adicionando a legenda configurada
plt.legend(fontsize=11, loc='upper right', framealpha=0.9, edgecolor='black')

plt.grid(True, linestyle='--', alpha=0.7)
plt.axvline(x=0, color='black', linewidth=1, linestyle='--')

plt.tight_layout()
plt.show()