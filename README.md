# Balao-Base

Repositório **base** dos balões estratosféricos do projeto **Distrito Espacial** (LCA / UnB).

---

## Missões derivadas

| Missão | Repositório | Descrição |
|---|---|---|
| Balão 1 (Abertura) | [`lase-unb/Balao-Abertura`](https://github.com/lase-unb/Balao-Abertura) | Cópia do `Balao-Base` até o commit `274e2ee` |
| Balão 2 | [`lase-unb/Balao-2`](https://github.com/lase-unb/Balao-2) | Cópia do `Balao-Base` até o commit `755379a` |

---

## Índice

- [Missões derivadas](#missões-derivadas)
- [Estrutura do repositório](#estrutura-do-repositório)
- [Hardware](#hardware)
- [Firmware: as duas linhas](#firmware-as-duas-linhas)
- [Parâmetros de rádio](#parâmetros-de-rádio)
- [Formato dos pacotes](#formato-dos-pacotes)
- [Altitude barométrica (`AltB`)](#altitude-barométrica-altb)
- [Campo `Fix` — validade dos dados GPS](#campo-fix--validade-dos-dados-gps)
- [Estação de solo: Rastreador Sonda](#estação-de-solo-rastreador-sonda)
- [Estação RS41 com RTL-SDR](#estação-rs41-com-rtl-sdr)
- [Cartão SD via OpenLog](#cartão-sd-via-openlog)
- [Logs de missão](#logs-de-missão)
- [Bibliotecas necessárias](#bibliotecas-necessárias)
- [Observações e pendências conhecidas](#observações-e-pendências-conhecidas)

---

## Estrutura do repositório

```
Balao-Base/
├── src/
│   ├── tracker.py                         Nova interface, missões e reprodução
│   ├── trackerV1.2.py                     Interface legada
│   ├── mission.py / mission_ui.py         Gravação e controles de missão
│   ├── telemetry.py / station.py          Parser e recepção serial
│   ├── replay.py / antenna.py             Reprodução e geometria da antena
│   ├── config.txt                         Configuração do OpenLog (copiar para o cartão SD)
│   ├── Lora_Bordo_Com_Telecomando/        Transmissor GY-86 + telecomando
│   └── Lora_Solo_com_Telecomando/         Receptor/transmissor da linha B
├── docs/
│   ├── MISSION_LOGS.md                    Operação, formatos e recuperação
│   └── logs/                              Logs históricos versionados
├── tests/                                 Testes automatizados
├── tools/                                 Ferramentas auxiliares (ver tools/README.md)
│   ├── calibrar_bateria/                  Sketch + guia de calibração do ADC da bateria
│   ├── bancada_imu_gy86/                  Sketch de bancada do IMU GY-86 (Serial Plotter)
│   ├── liberacao_carga/                   Protótipo HX711 + relé (não integrado ao voo)
│   ├── rastreador_sonda/                  Interface anterior do rastreador (legada, PR 17)
│   ├── tracker_win64x/                    Artefatos de build do trackerV1.2 para Windows (legado)
│   ├── rtl_sdr/                           Estação RS41 com RTL-SDR e SondeHub
│   └── ensaio_missao/                     Ensaio de missão prolongada
├── requirements-tracker.txt               Dependências da nova interface
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
| Balança (protótipo, fora do voo) | **HX711** + célula de carga | DOUT 2, SCK 15; relé no pino 25 |

> O `Wire1` do BNO086 roda a **100 kHz** (e não 400 kHz) de propósito: evita quedas de I²C quando os cabos balançam durante o voo.

---

## Firmware: as duas linhas

O repositório carrega **duas linhas de firmware independentes e não intercambiáveis**. Elas usam sensores diferentes, pacotes diferentes e estratégias de rádio diferentes. Escolha uma linha e use o par bordo+solo correspondente.

### Linha A — Telemetria completa (BME280 + BNO086)

Par: `LoraBordo.ino` + `LoraSolo.ino`

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
| **Ímpar** | transmite telemetria (ou uma mensagem repetida) | escuta |
| **Par** (`% 2 == 0`) | escuta por 1000 ms | pode transmitir `CMD:<n>` ou `MSG:<id>:<texto>` |

O fluxo de um comando:

1. O operador digita um número no Serial Monitor da estação de solo; ele fica enfileirado (`pending_cmd`).
2. Quando a solo **recebe** um pacote do bordo, ela estima o início daquele segundo ímpar pelo tempo no ar do pacote e dispara `CMD:<n>` no **centro** da janela par seguinte.
3. O bordo recebe, guarda o valor em `ack_val` e passa a ecoá-lo no campo `Ack:` de todo pacote seguinte.
4. A solo imprime o `ACK` recebido, fechando o laço de confirmação.

O bordo interpreta o número pela faixa; o `Ack` sempre volta como `comando + 1`:

| Comando | Efeito no bordo |
|---|---|
| 1 a 50 | Nenhuma ação além do `Ack` e do registro `CMD` no SD |
| 900 a 1100 | Troca o QNH (hPa) usado na altitude barométrica |
| Acima de 2000 (a interface usa 2500) | Calibra o IMU: refaz o zero de pitch/roll/yaw na posição atual; bloqueia rádio, GPS e SD por ~4 s e só dá resultado correto com a carga parada |
| Demais valores | Ignorado |

Duas diferenças estruturais em relação à linha A, ambas para não travar o RTOS do rádio:

- **Arquitetura de flags.** Os callbacks `OnTxDone`/`OnRxDone` só levantam `volatile bool`; todo o trabalho pesado acontece no `loop()`. Nada de processamento dentro da interrupção.
- **Sem flush forçado do SD.** O `forcarFlushOpenLog()` foi deliberadamente removido daqui — o bloqueio de ~130 ms atrasava a janela de TDM. O custo é que um corte de energia abrupto perde o buffer pendente.

O IMU aqui não tem fusão em hardware: pitch/roll saem de um **filtro complementar** (α = 0.98) entre acelerômetro e giroscópio, e o yaw vem do magnetômetro com **compensação de inclinação** (tilt compensation).

### Repetidor de mensagens (várias estações)

O bordo da linha B repete mensagens de texto entre estações de solo — por exemplo, da estação principal para a equipe de recuperação, que no voo pode estar sem visada direta com ela. Todas as estações usam o **mesmo firmware de solo** e a mesma interface.

```
Interface A ─▶ Solo A ──(seg. par: MSG:A7:<texto>)──▶ BORDO ──(seg. ímpar: PT2UNB-R)──▶ Solo A, B, C… ─▶ Interfaces
                                                       └─ monta e grava o LOTE no SD normalmente, mas não o transmite
```

**Rodízio das janelas.** Cada estação tem uma letra (A, B, C…) e todas são configuradas com o mesmo total `N`. A janela par do segundo `s` pertence à estação `(s / 2) mod N`; a solo sabe o segundo pelo `Time:` de todo pacote do bordo (que vem do relógio TDM do bordo, válido mesmo sem fix). Assim duas estações nunca transmitem na mesma janela. `N` precisa dividir 30 (**1, 2, 3, 5 ou 6**), senão o rodízio quebra na virada do minuto. Com `N = 3`, cada estação pode falar a cada 6 s.

| Segundo | 00 | 01 | 02 | 03 | 04 | 05 | 06 | … |
|---|---|---|---|---|---|---|---|---|
| Bordo (`N = 3`) | escuta **A** | TX | escuta **B** | TX | escuta **C** | TX | escuta **A** | |

**Pacotes.**

| Sentido | Formato |
|---|---|
| Subida (solo → bordo) | `MSG:<id>:<texto>` — `id` = letra da estação + contador gravado na flash (ex.: `B7`) |
| Descida (bordo → todas) | `PT2UNB-R` / `Id:<id>` / `Time:hh:mm:ss` / `Ack:<n>` / `Msg:<texto>` |

O texto tem no máximo **100 caracteres** ASCII imprimíveis (a interface remove acentos). Isso mantém a subida em ~190 ms e a descida em ~240 ms no ar, com folga na janela de 1000 ms.

**Regras no bordo.**

- No segundo ímpar com mensagem na fila, **a telemetria não vai ao ar**, mas é montada e gravada no SD como sempre; o cabeçalho do lote ganha a marca `RELAY:<id>`.
- No máximo uma mensagem desce a cada dois segundos ímpares: a telemetria nunca fica mais de 4 s fora do ar.
- Fila de até 4 mensagens. Um `id` que já está na fila não ocupa outra posição; com a fila cheia a mensagem é descartada (`MSG_DESCARTADA` no SD) e a estação de origem retransmite.
- O `Ack:` segue no pacote de relay, então a confirmação de telecomando não se perde.

**Regras na solo.**

- O pacote `PT2UNB-R` é a confirmação para quem enviou: sem ouvi-lo, a estação retransmite na próxima janela própria, até 5 tentativas.
- Cada estação lembra os últimos 16 ids recebidos e ignora repetições (o bordo repete o relay quando a origem retransmite).
- Comando tem prioridade sobre mensagem na mesma janela; **telecomandos numéricos só saem da estação A**.

**Serial da solo** (115200 baud), usada pela interface e utilizável no Serial Monitor:

| Entrada | Efeito |
|---|---|
| `M <texto>` | Enfileira uma mensagem (uma por vez) |
| `ID <letra> <N>` | Configura a estação; fica gravado na flash |
| `ID?` | Imprime `[ESTACAO] ID:<letra> N:<N>` |
| `<número>` | Telecomando (só estação A) |

A solo responde com linhas `[MSG] ENFILEIRADA|ENVIADA|ENTREGUE|FALHOU|RECUSADA|RECEBIDA Id:… [De:…] [Hora:…] [Texto:…]`, que a interface interpreta. Quando o `Ack:` de um telecomando chega dentro de um relay, a solo também imprime `[ACK] Ack:<n>`, e a interface o mostra em "ACK recebido" (o bordo zera o Ack depois de transmiti-lo, então a telemetria seguinte já vem com 0).

**Cartão SD do bordo.** Além de `LOTE_` e `I,`, o arquivo passa a ter:

```
MSG_RX,<millis>,<id>,rssi:<dBm>,snr:<dB>,<texto>   mensagem recebida e enfileirada
LOTE_<seq>,<millis>,RELAY:<id>                     lote gravado mas não transmitido
MSG_TX,<millis>,<id>                               mensagem descida no lugar daquele lote
MSG_DESCARTADA,<millis>,<id>,fila_cheia
```

### Sketches de bancada (`tools/`)

Os dois sketches abaixo ficam fora de `src/` porque não fazem parte de nenhuma linha de voo. Nenhum deles roda na Heltec V3 sem ajuste de pinos.

**[`tools/liberacao_carga/liberacao_carga.ino`](tools/liberacao_carga/liberacao_carga.ino)** — Protótipo derivado do exemplo da biblioteca HX711. Lê uma célula de carga pelo ADC **HX711** (fator de calibração `set_scale(251.25)`, tara no boot) e aciona o relé do pino 25 quando o peso lido fica **≤ 300**. Serve para liberar carga útil / paraquedas quando a tração na linha cai. Não está integrado a nenhum firmware de voo, e os pinos não servem na Heltec V3: o GPIO 2 é o TX do OpenLog e o GPIO 25 não existe no ESP32-S3.

**[`tools/bancada_imu_gy86/bancada_imu_gy86.ino`](tools/bancada_imu_gy86/bancada_imu_gy86.ino)** — Bancada de teste do IMU GY-86 (10DOF), antes chamada `gy80testado`, independente do LoRa. Roda o mesmo filtro complementar da linha B e imprime CSV pronto para o Serial Plotter (`Temp,Pressao,Pitch,Roll,Yaw`). Nos primeiros **4 segundos** ele deixa o filtro estabilizar e captura a atitude como offset de tara, zerando pitch/roll/yaw — só depois começa a imprimir. Usa I²C em SDA 19 / SCL 18; na Heltec V3 o GY-86 fica em SDA 48 / SCL 47. O bordo da linha B já incorpora esse filtro com calibração mais completa.

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

A nova interface está em [`src/tracker.py`](src/tracker.py). Ela lê a serial USB do receptor Heltec a 115200 baud e oferece mapa, gráficos, distância tracker–sonda, apontamento 3D e telecomando. Aceita as linhas A e B, preservando campos ausentes como indisponíveis. O zoom da interface começa em 150% (menos, se a tela for pequena) e muda com **Ctrl +** e **Ctrl -**, de 100% a 300%; **Ctrl 0** volta ao padrão. A janela de "Configurar tracker" abre grande e centralizada sobre a janela principal.

A aba **Sonda 3D**, ao lado do mapa e da Antena 3D, traz de volta a atitude 3D da interface antiga (`trackerV1.2.py`): um cilindro com o nariz vermelho em +X, girado a cada pacote por `Pitch`, `Roll` e `Yaw`, também na reprodução. Uma seta azul marca o topo (+Z) para o roll ficar visível. A rotação segue a convenção do bordo da linha B (Z para cima em repouso, roll em X, pitch em Y e yaw no sentido horário visto de cima, como uma bússola; ver [`src/attitude.py`](src/attitude.py)). Como o firmware tara os três ângulos no boot, os eixos de referência são a atitude da sonda ao ligar, e não o norte. A aba também mostra a inclinação do topo em relação à vertical e avisa quando a sonda está de cabeça para baixo.

O botão **Centralizar**, no cabeçalho do mapa, volta o mapa para a última posição da sonda; sem posição dela, para o tracker, e sem nenhum dos dois, para a posição inicial, mantendo o zoom atual.

O mapa funciona sem internet. Toda imagem de mapa baixada é gravada em `src/mapa_cache.db` (ignorado pelo git) e, das próximas vezes, vem desse arquivo, com ou sem rede. Antes da missão, enquadre a região do voo e da queda e clique em **Baixar área offline**: a área visível é baixada do zoom 3 ao 15 na camada Padrão (ao 13 na Topográfica), com progresso no botão, que também cancela. O limite é 20.000 imagens por vez; para áreas maiores, aproxime o mapa e baixe em partes. A camada Satélite não permite baixar regiões e fica offline só nas áreas já vistas. Para levar o mapa para outro computador, copie `src/mapa_cache.db`; para limpá-lo, apague o arquivo.

O sistema de logs agora organiza a aquisição em **missões**, com criação, encerramento e retomada. Cada missão guarda captura serial exata, telemetria estruturada e eventos em SQLite. A serial também é copiada automaticamente para `telemetria.txt` na pasta da missão. A gravação é independente da recepção, com sincronização aproximadamente a cada segundo, fila limitada que preserva os dados recentes em caso de falha e indicadores de perda/recuperação.

O cartão **Mensagens via balão** configura a letra desta estação e o total de estações, envia mensagens de até 100 caracteres e mostra as mensagens repetidas pelo balão com hora, estação de origem e texto. A situação da última mensagem enviada (na fila, enviada, repetida pelo balão ou sem confirmação) aparece logo abaixo. Veja [Repetidor de mensagens](#repetidor-de-mensagens-várias-estações).

Também há exportação CSV, KML e captura bruta, e reprodução das novas missões com pausa, velocidade e busca temporal. A reprodução usa a configuração histórica do tracker e funciona com o rádio desconectado.

O botão **Exportar KML** gera um arquivo para o Google Earth com o trajeto da sonda (altitude MSL, como o GPS), os marcadores do primeiro ponto, do ponto mais alto e do último ponto, e a posição do tracker, se estiver configurada. Só entram pacotes com GPS 3D válido; se a missão não tiver nenhum, a interface avisa e não cria o arquivo.

No cartão "Energia e telecomando", o botão **Calibrar IMU** envia o comando `2500` depois de uma confirmação que lembra que a carga precisa estar parada e que a sonda fica ~4 s sem rádio. A confirmação chega no campo "ACK recebido" como `2501`.

```bash
python -m pip install -r requirements-tracker.txt
python src/tracker.py
```

Requer Python 3.10+ e Tkinter. Leia o [guia de missões, recuperação, formatos e testes](docs/MISSION_LOGS.md) antes da operação de campo. Os executáveis Windows anteriores não foram recompilados.

> **Material legado.** Estes itens continuam no repositório apenas como referência e não recebem novos recursos:
> - [`tools/rastreador_sonda/tracker.py`](tools/rastreador_sonda/tracker.py): a interface redesenhada do PR 17, com mapa, cartões de telemetria, gráficos e log em `.txt`, mas sem missões em SQLite, reprodução, apontamento 3D nem telecomando;
> - [`src/trackerV1.2.py`](src/trackerV1.2.py): a primeira interface;
> - [`tools/tracker_win64x/`](tools/tracker_win64x/): resíduos de um build do PyInstaller para Windows da `trackerV1.2.py` (sem o `.exe`), guardados só como registro.

---

## Estação RS41 com RTL-SDR

[`tools/rtl_sdr/`](tools/rtl_sdr/) contém uma estação independente para receber radiossondas meteorológicas comerciais com RTL-SDR, incluindo a Vaisala RS41. Ela varre 400,05–406 MHz, decodifica a telemetria, mantém logs por sonda, oferece um painel em `http://localhost:5000` e envia os pontos ao SondeHub com o indicativo `LCA-UNB`.

Essa estação não recebe os pacotes LoRa de 910,5 MHz do balão deste projeto. Para a telemetria própria, continue usando o Heltec de solo e o `Rastreador Sonda` descrito acima.

Instalação e execução:

```bash
bash tools/rtl_sdr/concluir-instalacao.sh
bash tools/rtl_sdr/iniciar.sh
```

Consulte [`tools/rtl_sdr/LEIA-ME.md`](tools/rtl_sdr/LEIA-ME.md) para dependências, configuração e observações sobre a publicação da posição da estação.

---

## Cartão SD via OpenLog

O firmware de bordo da linha A (o `LoraBordo`, hoje só no histórico do git) grava no cartão SD por um módulo **OpenLog** na UART2 (GPIO 3 = RX, GPIO 2 = TX). O código faz **auto-detecção de baud**: tenta 57600 primeiro e cai para 9600 (default de fábrica) se não houver resposta.

| Cenário | Baud | Taxa do IMU | Precisão do estouro |
|---|---|---|---|
| `config.txt` aplicado no SD | **57600** | **40 Hz** | ~25 ms |
| `config.txt` **não** aplicado | 9600 | 5 Hz | ~200 ms |

Em ambos os casos a telemetria LoRa continua a 1 Hz — muda só a resolução do log do IMU. A taxa cai para 5 Hz a 9600 porque a linha `I` carrega 15 campos (accel + giro + mag + PRY) e estouraria a UART.

Se o OpenLog não responder em nenhum baud, o firmware **não trava**: segue o voo só com LoRa, sem log.

### Habilitando 40 Hz

1. Formate o microSD em **FAT32**.
2. Copie o [`src/config.txt`](src/config.txt) deste repositório para a **raiz do cartão**.
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

`docs/logs/` guarda telemetria capturada no voo de 19/09/2026, no formato de saída do Rastreador Sonda. O nome do arquivo é `telemetria_AAAAMMDD_HHMMSS.txt`, com a data e a hora (do computador de solo) em que a captura começou; cada reconexão da interface gera um arquivo novo:

| Arquivo | Linhas |
|---|---|
| `docs/logs/telemetria_20260919_082905.txt` | 6.660 |
| `docs/logs/telemetria_20260919_084252.txt` | 308 |
| `docs/logs/telemetria_20260919_095827.txt` | 0 (vazio) |
| `docs/logs/telemetria_20260919_110107.txt` | 13.958 |
| `docs/logs/telemetria_20260919_112448.txt` | 2.809 |
| `docs/logs/telemetria_20260919_113050.txt` | 60.713 |

---

## Bibliotecas necessárias

**Placa (Arduino IDE):** pacote Heltec ESP32 — selecione *WiFi LoRa 32(V3)*.

| Sketch | Bibliotecas |
|---|---|
| `Lora_Bordo_Com_Telecomando` | `LoRaWan_APP`, `SparkFun_u-blox_GNSS_v3`, `Adafruit_Sensor`, `Adafruit_MPU6050`, `Adafruit_HMC5883_U`, `MS5611` |
| `Lora_Solo_com_Telecomando` | `LoRaWan_APP` |
| `tools/liberacao_carga` | `HX711` (bogde) |
| `tools/bancada_imu_gy86` | `Adafruit_Sensor`, `Adafruit_MPU6050`, `Adafruit_HMC5883_U`, `MS5611` |

**Python:** `pyserial`, `tkintermapview`, `matplotlib` (o `tkinter` já vem com o Python).

---

## Observações e pendências conhecidas

Pontos levantados na revisão do código atual. Estão registrados aqui para quem for derivar uma missão deste repositório.

1. **As interfaces de rastreamento aceitam as duas linhas de telemetria.** O parser em `src/telemetry.py` reconhece `MZ` e `Ack`, preserva campos ausentes como indisponíveis e evita duplicar a cópia formatada da linha A. A interface redesenhada em `tools/rastreador_sonda/tracker.py` (legada) também fecha pacotes em `MZ` ou `Ack`; a interface legada `src/trackerV1.2.py` permanece disponível.

2. **Os logs históricos continuam versionados em `docs/logs/`.** As novas missões usam pastas locais escolhidas pelo operador e não substituem essas capturas. A reprodução de TXT antigos está fora desta etapa.

3. **Artefatos antigos de build (legados) continuam em `tools/tracker_win64x/build/`.** São só arquivos intermediários do PyInstaller para a `src/trackerV1.2.py`, sem o `.exe`. Eles não representam a nova interface `src/tracker.py`; o executável Windows ainda precisa ser gerado para uma distribuição dessa versão.

4. **O relé da liberação de carga fica acionado em repouso.** A condição é `peso <= 300 → relé HIGH`. Como a balança é tarada no boot, o peso parte de ~0 e o relé sobe imediatamente na bancada. O comportamento pretendido depende de haver carga aplicada desde o início — vale confirmar a polaridade antes de integrar ao voo.
