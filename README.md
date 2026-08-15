# Balao-Base

Repositório **base** dos balões estratosféricos do **Lab Céu Azul** (LASE / UnB).

Aqui ficam o firmware de bordo, o firmware da estação de solo, o rastreador de missão e os logs de voos já realizados. Cada missão nova parte deste repositório — ele é o tronco comum, não o código de um voo específico. A primeira derivação é o [`Balao-Abertura`](https://github.com/lase-unb/Balao-Abertura).

Toda a telemetria transmitida usa o indicativo de radioamadorismo **`PT2UNB`** como primeira linha do pacote.

---

## Índice

- [Estrutura do repositório](#estrutura-do-repositório)
- [Hardware](#hardware)
- [Firmware: as duas linhas](#firmware-as-duas-linhas)
- [Parâmetros de rádio](#parâmetros-de-rádio)
- [Formato dos pacotes](#formato-dos-pacotes)
- [Altitude barométrica (`AltB`)](#altitude-barométrica-altb)
- [Campo `Fix` — validade dos dados GPS](#campo-fix--validade-dos-dados-gps)
- [Estação de solo: Rastreador Sonda](#estação-de-solo-rastreador-sonda)
- [Cartão SD via OpenLog](#cartão-sd-via-openlog)
- [Logs de missão](#logs-de-missão)
- [Bibliotecas necessárias](#bibliotecas-necessárias)
- [Observações e pendências conhecidas](#observações-e-pendências-conhecidas)

---

## Estrutura do repositório

```
Balao-Base/
├── src/
│   ├── LoraBordo/                        Transmissor de bordo (BME280 + BNO086)
│   ├── LoraSolo/                         Receptor de solo (par do LoraBordo)
│   ├── Lora_Bordo_Com_Telecomando/       Transmissor de bordo (GY-86 + telecomando TDM)
│   ├── Lora_Solo_com_Telecomando/        Receptor/transmissor de solo (par do anterior)
│   ├── LiberacaoCarga/                   Balança HX711 + acionamento de relé
│   └── gy80testado/                      Bancada de teste do IMU 10DOF
├── Rastreador Sonda/
│   ├── tracker.py                        Estação de solo gráfica (Python/Tkinter)
│   ├── tracker.spec                      Receita do PyInstaller
│   ├── build/                            Artefatos do PyInstaller (versionados)
│   └── dist/                             tracker.exe + logs de missão (versionados)
├── logs/                                 Logs de telemetria de voos realizados
├── config.txt                            Configuração do OpenLog (copiar para o SD)
└── README.md
```

## Hardware

| Item | Componente | Barramento / Pinos |
|---|---|---|
| Placa | Heltec WiFi LoRa 32 **V3** (ESP32-S3 + SX1262) | — |
| GPS | u-blox **SAM-M10Q** | I²C `Wire` — SDA 41, SCL 42 |
| Clima (linha A) | **BME280** (temp, pressão, umidade) | I²C `Wire` @ 0x77 |
| IMU (linha A) | **BNO086** (fusão interna, quaternion) | I²C `Wire1` — SDA 48, SCL 47 @ 100 kHz, addr 0x4B |
| IMU/clima (linha B) | **GY-86 / 10DOF**: MPU6050 (0x68) + HMC5883L + MS5611 (0x77) | I²C — SDA 48, SCL 47 |
| Cartão SD | SparkFun **OpenLog** | UART2 — RX 3, TX 2 |
| Balança | **HX711** + célula de carga | DOUT 2, SCK 15; relé no pino 25 |

> O `Wire1` do BNO086 roda a **100 kHz** (e não 400 kHz) de propósito: evita quedas de I²C quando os cabos balançam durante o voo.

---

## Firmware: as duas linhas

O repositório carrega **duas linhas de firmware independentes e não intercambiáveis**. Elas usam sensores diferentes, pacotes diferentes e estratégias de rádio diferentes. Escolha uma linha e use o par bordo+solo correspondente.

### Linha A — Telemetria completa (BME280 + BNO086)

Par: [`src/LoraBordo/LoraBordo.ino`](src/LoraBordo/LoraBordo.ino) + [`src/LoraSolo/LoraSolo.ino`](src/LoraSolo/LoraSolo.ino)

**Bordo.** Lê GPS (lat, lon, altitude MSL, satélites, tipo de fix, hora UTC), BME280 (temperatura com correção de −2 °C, pressão, umidade, altitude barométrica derivada) e BNO086 (acelerômetro, giroscópio e magnetômetro brutos + pitch/roll/yaw da fusão interna). Transmite tudo por LoRa em texto multi-linha a **1 Hz**, no ritmo do PVT do GPS.

Em paralelo, o **BNO086 é amostrado a ~40 Hz** e cada amostra vai para o cartão SD numa linha `I,...`. Isso existe para achar o instante exato do estouro do balão pelo pico de aceleração na análise pós-voo — a telemetria de 1 Hz é grossa demais para isso.

O rádio é usado de forma simples e bloqueante: transmite e espera. A cada lote o código força um flush do OpenLog (`forcarFlushOpenLog()`), custando ~130 ms, para garantir que nada se perca se a alimentação cair.

**Solo.** Escuta o canal continuamente e faz o parse campo a campo com `strstr`, e não com um `sscanf` único. Isso é proposital: cada campo é lido de forma independente, então o receptor tolera pacotes parcialmente corrompidos no ar e campos fora de ordem. Ao fim, reporta quantos dos **22 campos** foram extraídos e avisa se vieram incompletos.

### Linha B — Telecomando com TDM (GY-86)

Par: [`src/Lora_Bordo_Com_Telecomando/`](src/Lora_Bordo_Com_Telecomando/Lora_Bordo_Com_Telecomando.ino) + [`src/Lora_Solo_com_Telecomando/`](src/Lora_Solo_com_Telecomando/Lora_Solo_com_Telecomando.ino)

Esta linha troca o BME280+BNO086 pelo **GY-86 (10DOF)** e adiciona **enlace de subida**: a estação de solo consegue mandar comandos para o balão.

O canal é compartilhado por **TDM (divisão no tempo)**, sincronizado pelo relógio do GPS:

| Segundo do GPS | Bordo | Solo |
|---|---|---|
| **Par** (`% 2 == 0`) | transmite telemetria | escuta |
| **Ímpar** | escuta | pode transmitir `CMD:<n>` |

O fluxo de um comando:

1. O operador digita um número no Serial Monitor da estação de solo; ele fica enfileirado (`pending_cmd`).
2. Assim que a solo **recebe** um pacote de telemetria, ela espera **150 ms** — margem para o balão fechar o TX e abrir a escuta — e só então dispara `CMD:<n>`.
3. O bordo recebe, guarda o valor em `ack_val` e passa a ecoá-lo no campo `Ack:` de todo pacote seguinte.
4. A solo imprime o `ACK` recebido, fechando o laço de confirmação.

Duas diferenças estruturais em relação à linha A, ambas para não travar o RTOS do rádio:

- **Arquitetura de flags.** Os callbacks `OnTxDone`/`OnRxDone` só levantam `volatile bool`; todo o trabalho pesado acontece no `loop()`. Nada de processamento dentro da interrupção.
- **Sem flush forçado do SD.** O `forcarFlushOpenLog()` foi deliberadamente removido daqui — o bloqueio de ~130 ms atrasava a janela de TDM. O custo é que um corte de energia abrupto perde o buffer pendente.

O IMU aqui não tem fusão em hardware: pitch/roll saem de um **filtro complementar** (α = 0.98) entre acelerômetro e giroscópio, e o yaw vem do magnetômetro com **compensação de inclinação** (tilt compensation).

### Utilitários

**[`src/LiberacaoCarga/LiberacaoCarga.ino`](src/LiberacaoCarga/LiberacaoCarga.ino)** — Lê uma célula de carga pelo ADC **HX711** (fator de calibração `set_scale(251.25)`, tara no boot) e aciona o relé do pino 25 quando o peso lido fica **≤ 300**. Serve para liberar carga útil / paraquedas quando a tração na linha cai.

**[`src/gy80testado/gy80testado.ino`](src/gy80testado/gy80testado.ino)** — Bancada de teste do IMU 10DOF, independente do LoRa. Roda o mesmo filtro complementar da linha B e imprime CSV pronto para o Serial Plotter (`Temp,Pressao,Pitch,Roll,Yaw`). Nos primeiros **4 segundos** ele deixa o filtro estabilizar e captura a atitude como offset de tara, zerando pitch/roll/yaw — só depois começa a imprimir.

---

## Parâmetros de rádio

Idênticos nas duas linhas. Bordo e solo precisam bater exatamente, ou não há enlace.

| Parâmetro | Valor |
|---|---|
| Frequência | **910.5 MHz** (`910500000`) |
| Largura de banda | 125 kHz (`LORA_BANDWIDTH 0`) |
| Spreading factor | **SF7** |
| Coding rate | 4/5 (`LORA_CODINGRATE 1`) |
| Preâmbulo | 8 símbolos |
| Potência de TX | 18 dBm |
| Buffer de pacote | 256 bytes |
| Serial de depuração | 115200 baud |

---

## Formato dos pacotes

Texto multi-linha, um `chave:valor` por linha, separados por `\n`, sempre começando por `PT2UNB`.

| Campo | Significado | Linha A | Linha B |
|---|---|:---:|:---:|
| `Lat` / `Lon` | Graus decimais, 7 casas | ✅ | ✅ |
| `Alt` | Altitude GPS (MSL), metros | ✅ | ✅ |
| `AltB` | Altitude barométrica, metros | ✅ | ✅ |
| `Sat` | Satélites em vista | ✅ | ✅ |
| `Fix` | Tipo de fix do GPS | ✅ | ✅ |
| `T` / `P` | Temperatura (°C) / pressão (hPa) | ✅ | ✅ |
| `U` | Umidade relativa (%) | ✅ | ❌ |
| `Time` | Hora UTC `HH:MM:SS` | ✅ | ✅ |
| `Pitch` / `Roll` / `Yaw` | Atitude em **graus** | ✅ | ✅ |
| `AX`…`AZ`, `GX`…`GZ`, `MX`…`MZ` | Accel / giro / mag brutos | ✅ | ❌ |
| `Ack` | Eco do último telecomando recebido | ❌ | ✅ |
| **Total** | | **22 campos** | **13 campos** |

Exemplo (linha A):

```
PT2UNB
Lat:-15.7641109
Lon:-47.8690318
Alt:1031.3
AltB:973.7
Sat:16
Fix:3
T:23.7
P:901.6
U:45.4
Time:16:40:48
Pitch:-89.14
Roll:152.52
Yaw:-128.20
AX:10.59
AY:0.03
AZ:-0.11
GX:0.00
GY:0.00
GZ:0.00
MX:-69.12
MY:3.94
MZ:2.94
```

## Altitude barométrica (`AltB`)

Calculada pela atmosfera padrão (ISA), com **P₀ = 1013,25 hPa** ao nível do mar:

```
AltB = 44330 * (1 - (P / 1013.25) ^ 0.1903)
```

A aproximação vale até **~11 km**; acima disso ela diverge. Ainda assim é útil como referência independente do GPS — em particular para substituir o `Alt` quando o fix cai no meio do voo.

## Campo `Fix` — validade dos dados GPS

| Valor | Significado | O que é confiável |
|---|---|---|
| `Fix:0` | Sem fix | **Nada** — Lat/Lon/Time são lixo de cold start |
| `Fix:2` | Fix 2D | Lat/Lon (sem altitude) |
| `Fix:3` | Fix 3D | Lat/Lon/Alt |
| `Fix:5` | Somente tempo | Time (mas sem posição) |

> ⚠️ **Na análise pós-voo, descarte tudo com `Fix:0`** antes de usar Lat/Lon/Time. Caso contrário você vai plotar dados inválidos do cold start (tipicamente `Lat:0, Lon:0, Time:00:00:34`).

---

## Estação de solo: Rastreador Sonda

[`Rastreador Sonda/tracker.py`](Rastreador%20Sonda/tracker.py) — aplicação gráfica em Python/Tkinter que serve de monitor de missão.

Importante: o tracker **não fala LoRa**. Ele lê pela porta serial (115200 baud) a saída do **Heltec de solo** rodando o `LoraSolo.ino`, e faz o parse das linhas `chave:valor` que aquele sketch imprime.

O que ele oferece:

- **Mapa ao vivo** (`tkintermapview`) com marcador da sonda e rastro do trajeto percorrido.
- **Gráficos** (`matplotlib`, embutidos via `TkAgg`) de altitude, temperatura + umidade e pressão, com o eixo X no horário real do GPS.
- **Dinâmica de voo** derivada de pontos GPS consecutivos: velocidade e direção do vento (deslocamento horizontal por haversine) e velocidade vertical.
- **Qualidade do enlace**: RSSI e SNR extraídos do cabeçalho de cada pacote.
- **Log automático**: ao conectar, abre `telemetria_<AAAAMMDD>_<HHMMSS>.txt` e grava cada linha recebida com flush imediato.

### Rodando

```bash
pip install pyserial tkintermapview matplotlib
python "Rastreador Sonda/tracker.py"
```

Há também um executável Windows pré-compilado em `Rastreador Sonda/dist/tracker.exe`. Para regerá-lo:

```bash
pyinstaller "Rastreador Sonda/tracker.spec"
```

---

## Cartão SD via OpenLog

O firmware de bordo grava no cartão SD por um módulo **OpenLog** na UART2 (GPIO 3 = RX, GPIO 2 = TX). O código faz **auto-detecção de baud**: tenta 57600 primeiro e cai para 9600 (default de fábrica) se não houver resposta.

| Cenário | Baud | Taxa do IMU | Precisão do estouro |
|---|---|---|---|
| `config.txt` aplicado no SD | **57600** | **40 Hz** | ~25 ms |
| `config.txt` **não** aplicado | 9600 | 5 Hz | ~200 ms |

Em ambos os casos a telemetria LoRa continua a 1 Hz — muda só a resolução do log do IMU. A taxa cai para 5 Hz a 9600 porque a linha `I` carrega 15 campos (accel + giro + mag + PRY) e estouraria a UART.

Se o OpenLog não responder em nenhum baud, o firmware **não trava**: segue o voo só com LoRa, sem log.

### Habilitando 40 Hz

1. Formate o microSD em **FAT32**.
2. Copie o [`config.txt`](config.txt) da raiz deste repositório para a **raiz do cartão**.
3. Conteúdo do arquivo:
   ```
   57600,26,3,0,1,1,0
   baud,escape,esc#,mode,verb,echo,ignoreRX
   ```
4. Insira o cartão e ligue o OpenLog uma vez. No boot ele lê o `config.txt`, aplica `baud=57600` e está pronto.

### Diagnóstico

O Serial Monitor (115200) informa qual baud foi detectado:

```
OpenLog: aguardando boot (2s)...
OpenLog: testando 57600 baud...
  -> OK a 57600 baud (config.txt aplicado). IMU em 40 Hz.
```

Ou, sem o `config.txt`:

```
OpenLog: testando 57600 baud...
OpenLog: 57600 sem resposta. Testando 9600 baud...
  -> OK a 9600 baud (config.txt NAO aplicado). IMU em 5 Hz.
```

A rotina de escape também ecoa os bytes crus em HEX, o que distingue três falhas diferentes:

| Sintoma no `[diag] RX:` | Diagnóstico |
|---|---|
| Texto com `<` ou `>` | Entrou em modo comando — sucesso |
| Lixo tipo `[FD][00]...` | O sinal chega, mas o baud ou o nível elétrico está errado |
| `(silencio - nenhum byte)` | Nada chega ao RX do ESP — GND, pino, fio ou OpenLog desligado |

### Formato do arquivo de log

Cada boot cria um arquivo novo, `log_<millis>.txt`, com dois tipos de registro intercalados:

- **`LOTE_<seq>,<millis>`** seguido do pacote completo — telemetria a **1 Hz**, o mesmo conteúdo que foi ao ar. O lote termina numa linha em branco.
- **`I,<millis>,AX:…,AY:…,AZ:…,GX:…,GY:…,GZ:…,MX:…,MY:…,MZ:…,P:…,R:…,Y:…`** — amostra do IMU a **40 Hz** (ou 5 Hz), com timestamp `millis()` próprio para correlação sub-segundo.

```
# Logs_balao | LOTE_<seq>,<millis>=telemetria 1Hz | I,<millis>=IMU | lote separado por linha em branco
LOTE_2,6385
PT2UNB
Lat:-15.7939100
Lon:-47.8823000
Alt:25340.0
...
MZ:-41.18

I,6388,AX:0.19,AY:-9.40,AZ:0.73,GX:0.18,GY:-0.06,GZ:0.22,MX:23.48,MY:-12.28,MZ:-41.19,P:0.00,R:-1.50,Y:-0.32
I,6421,AX:0.21,AY:-9.40,AZ:0.75,GX:0.16,GY:-0.07,GZ:0.21,MX:23.46,MY:-12.27,MZ:-41.20,P:0.00,R:-1.50,Y:-0.32
```

### Localizando o estouro do balão

1. No gráfico de altitude GPS, identifique o horário aproximado do estouro (ex.: `Time:12:34:56`).
2. Ache o lote que contém essa linha `Time:` — o cabeçalho `LOTE_N,<millis>` dá o `millis` de referência.
3. A partir desse `millis`, varra as linhas `I` numa janela de ±500 ms.
4. O pico de `AX/AY/AZ` marca o instante com precisão de **~25 ms** (40 Hz) ou **~200 ms** (5 Hz).

> ⚠️ **Não remova o cartão com o OpenLog ligado.** Ele mantém buffer interno e perde dados se for desligado abruptamente. Corte a alimentação antes de retirar o cartão.

---

## Logs de missão

`logs/` guarda telemetria capturada em voos já realizados, no formato de saída do Rastreador Sonda:

| Arquivo | Linhas |
|---|---|
| `logs/telemetria_lucas.txt` | 82.051 |
| `logs/telemetria_matheus.txt` | 15.750 |
| `logs/telemetria_matheus_2.txt` | 41.172 |

---

## Bibliotecas necessárias

**Placa (Arduino IDE):** pacote Heltec ESP32 — selecione *WiFi LoRa 32(V3)*.

| Sketch | Bibliotecas |
|---|---|
| `LoraBordo` | `LoRaWan_APP` (Heltec), `SparkFun_u-blox_GNSS_v3`, `SparkFunBME280`, `SparkFun_BNO08x_Arduino_Library` |
| `LoraSolo` | `LoRaWan_APP` |
| `Lora_Bordo_Com_Telecomando` | `LoRaWan_APP`, `SparkFun_u-blox_GNSS_v3`, `Adafruit_Sensor`, `Adafruit_MPU6050`, `Adafruit_HMC5883_U`, `MS5611` |
| `Lora_Solo_com_Telecomando` | `LoRaWan_APP` |
| `LiberacaoCarga` | `HX711` (bogde) |
| `gy80testado` | `Adafruit_Sensor`, `Adafruit_MPU6050`, `Adafruit_HMC5883_U`, `MS5611` |

**Python:** `pyserial`, `tkintermapview`, `matplotlib` (o `tkinter` já vem com o Python).

---

## Observações e pendências conhecidas

Pontos levantados na revisão do código atual. Estão registrados aqui para quem for derivar uma missão deste repositório.

1. **`gy80testado` não usa um GY-80.** Apesar do nome, o sketch instancia MPU6050 + HMC5883L + MS5611, que é a combinação do **GY-86/GY-87 (10DOF)** — o mesmo conjunto da linha B. O GY-80 traz ADXL345 + L3G4200D + BMP085. O nome do diretório está enganoso.

2. **O Rastreador Sonda só funciona com a linha A.** O tracker fecha cada pacote e atualiza os históricos quando vê a chave `MZ` (`if key == "MZ"`), e plota umidade a partir de `U`. O pacote da linha B não tem nenhum dos dois, então os gráficos e o rastro não avançam com o firmware de telecomando. Adaptar exige mudar a chave de fechamento do pacote.

3. **Logs duplicados no versionamento.** `logs/telemetria_matheus.txt` e `logs/telemetria_matheus_2.txt` são byte a byte idênticos a `Rastreador Sonda/dist/telemetria_20260624_134115.txt` e `.../telemetria_20260625_150455.txt`, respectivamente. São as mesmas capturas guardadas em dois lugares.

4. **Artefatos de build versionados.** `Rastreador Sonda/build/` e `Rastreador Sonda/dist/` (incluindo o `tracker.exe`) estão no repositório. São regeráveis a partir do `tracker.spec` e seriam candidatos naturais a um `.gitignore`.

5. **O relé da liberação de carga fica acionado em repouso.** A condição é `peso <= 300 → relé HIGH`. Como a balança é tarada no boot, o peso parte de ~0 e o relé sobe imediatamente na bancada. O comportamento pretendido depende de haver carga aplicada desde o início — vale confirmar a polaridade antes de integrar ao voo.
