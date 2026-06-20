# Balao-I

Projeto de balão estratosférico com telemetria LoRa. Contém 3 códigos Arduino (Plataforma Heltec ESP32 LoRa V3):

## `src/LoraBordo.ino` — Transmissor (a bordo do balão)

Código embarcado no balão. Realiza leituras de **GPS u-blox SAM-M10Q** (latitude, longitude, satélites, tempo UTC), **BME280** (temperatura, pressão, umidade) e **BNO086** (acelerômetro, giroscópio e magnetômetro brutos + pitch/roll/yaw via sensor fusion interno) e transmite tudo via rádio **LoRa a 910.5 MHz** em formato texto multi-linha, com o indicativo de radioamadorismo **`PT2UNB`** na primeira linha do pacote.

A telemetria LoRa é transmitida a **1 Hz** (no ritmo do GPS PVT). Além disso, o **BNO086 é amostrado a ~40 Hz** e cada amostra é gravada no cartão SD para análise pós-voo (ex: detectar o instante exato do estouro do balão pelo pico de aceleração).

## `src/LoraSolo.ino` — Receptor (estação terrestre)

Código da estação de solo. Escuta o canal LoRa continuamente, recebe os pacotes transmitidos pelo balão, faz o parsing da string e exibe os dados no Serial Monitor.

## `src/LiberacaoCarga.ino` — Balança de liberação

Controla uma célula de carga com **ADC HX711** para medir peso. Quando o peso lido é **≤ 300g**, aciona um relé (pino 25) — utilizado para liberar uma carga útil (paraquedas) do balão.

---

## Configuração do OpenLog (cartão SD)

O `LoraBordo.ino` grava dados no cartão SD via módulo **OpenLog** conectado à UART2 (GPIO 2 = RX, GPIO 3 = TX). O código faz **auto-detecção de baud**: tenta 57600 primeiro e cai para 9600 (default de fábrica) se necessário.

| Cenário | Baud | Taxa do IMU | Precisão do estouro |
|---|---|---|---|
| `config.txt` aplicado no SD | **57600** | **40 Hz** | ~25 ms |
| `config.txt` NÃO aplicado (default) | 9600 | 5 Hz | ~200 ms |

Em ambos os casos a telemetria LoRa funciona a 1 Hz. A única diferença é a resolução do log do IMU no cartão SD. (Em 9600 a taxa caiu para 5 Hz porque a linha `I` agora carrega 15 campos — accel + gyro + mag + PRY.)

### Para habilitar 40 Hz (recomendado para análise do estouro)

1. Formate o cartão microSD em **FAT32**.
2. Copie o arquivo **[`config.txt`](./config.txt)** da raiz deste repositório para a **raiz do cartão SD**.
3. O conteúdo é:
   ```
   57600,26,3,0,1,1,0
   baud,escape,esc#,mode,verb,echo,ignoreRX
   ```
4. Insira o cartão no OpenLog e ligue-o uma vez. No boot ele lê o `config.txt`, aplica `baud=57600` e está pronto.

> ℹ️ **Se você esquecer o `config.txt`, o código detecta automaticamente o baud 9600 e reduz a taxa do IMU para 5 Hz.** O sistema continua funcionando, só com menor resolução no log.

### Diagnóstico no Serial Monitor

Ao ligar, o Serial Monitor (115200 baud) mostra qual baud foi detectado:
```
OpenLog: aguardando boot (2s)...
OpenLog: testando 57600 baud...
  -> OK a 57600 baud (config.txt aplicado). IMU em 40 Hz.
```
Ou, se o `config.txt` não foi aplicado:
```
OpenLog: testando 57600 baud...
OpenLog: 57600 sem resposta. Testando 9600 baud...
  -> OK a 9600 baud (config.txt NAO aplicado). IMU em 10 Hz.
```

### Formato do arquivo de log (`logs_balao.txt`)

O OpenLog grava um único arquivo `logs_balao.txt` com dois tipos de registro:

- **`LOTE_<seq>,<millis>`** + linhas seguintes — telemetria completa a **1 Hz** (mesmo pacote enviado via LoRa). A primeira linha identifica o lote (sequencial + timestamp `millis()`), a partir da segunda linha vem o pacote PT2UNB completo, e o lote termina com uma linha em branco.
- **`I,<millis>,AX,AY,AZ,GX,GY,GZ,MX,MY,MZ,P,R,Y`** — amostra do IMU a **40 Hz** (ou 5 Hz se OpenLog a 9600), intercalada entre os lotes. Contém accel + gyro + mag brutos + PRY.

Exemplo:
```
# Logs_balao | LOTE_<seq>,<millis>=telemetria 1Hz | I,<millis>=IMU | lote separado por linha em branco
LOTE_1,5402
PT2UNB
Lat:0
Lon:0
Sat:0
Fix:0
T:23.0
P:903.2
U:48.0
Time:00:00:34
Pitch:0.00
Roll:0.00
Yaw:0.00
AX:0.00
AY:0.00
AZ:0.00
GX:0.00
GY:0.00
GZ:0.00
MX:0.00
MY:0.00
MZ:0.00

I,5448,AX:-0.10,AY:-9.15,AZ:0.06,GX:0.12,GY:0.05,GZ:-0.03,MX:23.45,MY:-12.30,MZ:-41.20,P:0.00,R:0.00,Y:0.00
LOTE_2,6385
PT2UNB
Lat:0
Lon:0
Sat:0
Fix:0
T:23.0
P:903.2
U:47.6
Time:00:00:35
Pitch:0.00
Roll:-1.50
Yaw:-0.32
AX:-0.10
AY:-9.15
AZ:0.06
GX:0.15
GY:-0.08
GZ:0.20
MX:23.50
MY:-12.25
MZ:-41.18

I,6388,AX:0.19,AY:-9.40,AZ:0.73,GX:0.18,GY:-0.06,GZ:0.22,MX:23.48,MY:-12.28,MZ:-41.19,P:0.00,R:-1.50,Y:-0.32
I,6421,AX:0.21,AY:-9.40,AZ:0.75,GX:0.16,GY:-0.07,GZ:0.21,MX:23.46,MY:-12.27,MZ:-41.20,P:0.00,R:-1.50,Y:-0.32
```

Cada lote é auto-contido: começa com `LOTE_N,<millis>`, tem `PT2UNB` na primeira linha dos dados, e termina com uma linha em branco. As amostras `I` do IMU (~40 Hz ou ~5 Hz) ficam intercaladas entre os lotes, com timestamp `millis()` próprio para correlação sub-segundo.

### Campo `Fix:` — validade dos dados GPS

O campo `Fix:N` indica se os dados do GPS (Lat, Lon, Sat, Time) são confiáveis:

| Valor | Significado | O que é confiável |
|---|---|---|
| `Fix:0` | Sem fix | **Nada** — Lat/Lon/Time são lixo de cold start |
| `Fix:2` | Fix 2D | Lat/Lon (sem altitude) |
| `Fix:3` | Fix 3D | Lat/Lon/Alt |
| `Fix:5` | Somente tempo | Time (mas sem posição) |

> ⚠️ **Na análise pós-voo, filtre tudo onde `Fix:0`** antes de usar Lat/Lon/Time. Caso contrário você vai trabalhar com dados inválidos do cold start (ex: `Lat:0, Lon:0, Time:00:00:34`).

### Como localizar o estouro do balão pós-voo

1. No gráfico de altitude GPS, identifique o horário do estouro (ex: `Time:12:34:56`).
2. Procure o lote cuja linha `Time:12:34:56` aparece → o cabeçalho `LOTE_N,<millis>` desse lote dá o `millis` de referência.
3. A partir desse `millis`, procure linhas `I` numa janela de ±500 ms.
4. O pico de `AX/AY/AZ` marca o instante do estouro com precisão de **~25 ms** (40 Hz) ou **~200 ms** (5 Hz).

> ⚠️ **Não remova o cartão SD com o OpenLog ligado** — ele mantém um buffer interno e pode perder dados se for desligado abruptamente. Desligue a alimentação antes de retirar o cartão.
