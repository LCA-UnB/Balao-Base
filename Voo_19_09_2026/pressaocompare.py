import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from scipy.interpolate import interp1d

# 1. Processar arquivo de telemetria interna
alt_int = []
p_int = []

with open('telemetria_voo_completo.txt', 'r', encoding='utf-8', errors='ignore') as f:
    current_alt = None
    for line in f:
        line = line.strip()
        if line.startswith('Alt:'):
            try:
                current_alt = float(line.split(':')[1])
            except ValueError:
                pass
        elif line.startswith('P:'):
            try:
                p_val = float(line.split(':')[1])
                if current_alt is not None:
                    alt_int.append(current_alt)
                    p_int.append(p_val)
                    current_alt = None # Reseta para o próximo bloco
            except ValueError:
                pass

df_int = pd.DataFrame({'alt': alt_int, 'p': p_int}).sort_values('alt').dropna()

# 2. Processar arquivo da radiossonda externa
df_ext = pd.read_csv('20260919-141701_X1909261_RS41_403502_sonde.log.txt')
# Filtrar pressões inválidas (neste log, ausência de dados pode estar como -1.0)
df_ext_valid = df_ext[df_ext['pressure'] > 0].copy()
df_ext_valid = df_ext_valid.sort_values('alt').dropna(subset=['alt', 'pressure'])

# Remover altitudes duplicadas para permitir a interpolação
df_ext_valid = df_ext_valid.drop_duplicates(subset=['alt'])

# 3. Interpolar a pressão externa para as altitudes da interna
# Isso permite comparar "maçãs com maçãs" (Pressão no exato mesmo nível de altitude)
f_p_ext = interp1d(df_ext_valid['alt'], df_ext_valid['pressure'], kind='linear', fill_value='extrapolate')

# Filtrar dados internos para ficar dentro do limite de altitude do dado externo
min_alt = df_ext_valid['alt'].min()
max_alt = df_ext_valid['alt'].max()
df_int_filtered = df_int[(df_int['alt'] >= min_alt) & (df_int['alt'] <= max_alt)].copy()

# Calcular o erro
df_int_filtered['p_ext_interp'] = f_p_ext(df_int_filtered['alt'])
df_int_filtered['erro'] = df_int_filtered['p'] - df_int_filtered['p_ext_interp']

# Estatísticas do erro
erro_medio = df_int_filtered['erro'].mean()
erro_max = df_int_filtered['erro'].abs().max()
std_erro = df_int_filtered['erro'].std()

print(f"Erro Médio: {erro_medio:.2f} hPa")
print(f"Erro Absoluto Máximo: {erro_max:.2f} hPa")
print(f"Desvio Padrão do Erro: {std_erro:.2f} hPa")

# 4. Configurar e Gerar o Gráfico
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

# Plot 1: Curvas de Pressão vs Altitude (Para ver o paralelismo)
ax1.plot(df_ext_valid['pressure'], df_ext_valid['alt'], color='blue', linewidth=2, label='Externa (RS41 - Referência)')
ax1.scatter(df_int_filtered['p'], df_int_filtered['alt'], color='red', alpha=0.5, s=15, label='Interna (MS5611)')
ax1.set_title('Pressão vs Altitude', fontsize=13)
ax1.set_ylabel('Altitude (metros)', fontsize=11)
ax1.set_xlabel('Pressão (hPa)', fontsize=11)
ax1.invert_xaxis() # Pressão diminui com a altitude, então invertemos o eixo x para ficar mais intuitivo visualmente
ax1.legend(fontsize=10)
ax1.grid(True, linestyle='--', alpha=0.7)

# Plot 2: Erro de Pressão vs Altitude
ax2.scatter(df_int_filtered['erro'], df_int_filtered['alt'], color='purple', alpha=0.6, s=15)
ax2.axvline(x=0, color='black', linewidth=1.5, linestyle='--')
ax2.axvline(x=erro_medio, color='green', linewidth=1.5, linestyle=':', label=f'Erro Médio ({erro_medio:.2f} hPa)')
ax2.set_title('Erro do Sensor Interno vs Altitude', fontsize=13)
ax2.set_ylabel('Altitude (metros)', fontsize=11)
ax2.set_xlabel('Erro de Pressão (Interna - Externa) em hPa', fontsize=11)
ax2.legend(fontsize=10)
ax2.grid(True, linestyle='--', alpha=0.7)

plt.tight_layout()
plt.savefig('pressao_analise.png')
plt.show()