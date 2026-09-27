import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from scipy.interpolate import interp1d
from scipy.stats import linregress

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
                    current_alt = None 
            except ValueError:
                pass

df_int = pd.DataFrame({'alt': alt_int, 'p': p_int}).sort_values('alt').dropna()

# 2. Processar arquivo da radiossonda externa
df_ext = pd.read_csv('20260919-141701_X1909261_RS41_403502_sonde.log.txt')
df_ext_valid = df_ext[df_ext['pressure'] > 0].copy()
df_ext_valid = df_ext_valid.sort_values('alt').dropna(subset=['alt', 'pressure'])
df_ext_valid = df_ext_valid.drop_duplicates(subset=['alt'])

# 3. Interpolar a pressão externa para as altitudes da interna
f_p_ext = interp1d(df_ext_valid['alt'], df_ext_valid['pressure'], kind='linear', fill_value='extrapolate')

min_alt = df_ext_valid['alt'].min()
max_alt = df_ext_valid['alt'].max()
df_int_filtered = df_int[(df_int['alt'] >= min_alt) & (df_int['alt'] <= max_alt)].copy()
df_int_filtered['p_ext_interp'] = f_p_ext(df_int_filtered['alt'])

# 4. Dados para o ajuste linear
x = df_int_filtered['p_ext_interp'].values
y = df_int_filtered['p'].values

slope, intercept, r_value, p_value, std_err = linregress(x, y)
line = slope * x + intercept

print(f"Equação: y = {slope:.4f}x + {intercept:.4f}")
print(f"R²: {r_value**2:.4f}")

# 5. Gráfico
plt.figure(figsize=(10, 8))
plt.scatter(x, y, color='royalblue', alpha=0.3, s=20, label='Dados Medidos (P_ext vs P_int)')
plt.plot(x, line, color='red', linewidth=2, label=f'Ajuste Linear: $y = {slope:.4f}x + {intercept:.2f}$\n$R^2 = {r_value**2:.6f}$')

plt.title('Correlação e Ajuste Linear', fontsize=14)
plt.xlabel('Pressão Externa RS41 (hPa)', fontsize=12)
plt.ylabel('Pressão Interna Carga Útil (hPa)', fontsize=12)
plt.legend(fontsize=12, loc='upper left')
plt.grid(True, linestyle='--', alpha=0.7)

plt.tight_layout()
plt.savefig('ajuste_linear_pressao.png')
plt.show()