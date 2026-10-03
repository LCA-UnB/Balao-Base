# tools/

Ferramentas auxiliares do Balao-Base. Cada subpasta é independente e tem seu próprio guia.

| Pasta | O que é | Como começar |
|---|---|---|
| [`calibrar_bateria/`](calibrar_bateria/) | Sketch Arduino (`calibrar_bateria.ino`) para levantar a curva do ADC da bateria com fonte de bancada, mais o guia e o modelo de planilha | [`CALIBRACAO_BATERIA.md`](calibrar_bateria/CALIBRACAO_BATERIA.md) |
| [`bancada_imu_gy86/`](bancada_imu_gy86/) | Sketch Arduino de bancada do IMU GY-86 (MPU6050 + HMC5883L + MS5611), antes `src/gy80testado`. Filtro complementar com tara nos 4 s iniciais e CSV para o Serial Plotter. Usa SDA 19 / SCL 18, não os pinos da Heltec V3 | abrir `bancada_imu_gy86.ino` no Arduino IDE |
| [`liberacao_carga/`](liberacao_carga/) | **Protótipo.** Sketch Arduino, antes `src/LiberacaoCarga`, que lê uma célula de carga via HX711 e aciona um relé com peso ≤ 300. Não integrado ao voo; pinos incompatíveis com a Heltec V3 | abrir `liberacao_carga.ino` no Arduino IDE |
| [`rastreador_sonda/`](rastreador_sonda/) | **Legada.** Interface Tkinter anterior do rastreador (`tracker.py`), sem missões, reprodução nem telecomando. A atual é `src/tracker.py` | `python tools/rastreador_sonda/tracker.py` |
| [`tracker_win64x/`](tracker_win64x/) | **Legado.** Arquivos intermediários de um build do PyInstaller (Windows) da `src/trackerV1.2.py`, sem o `.exe`. Só servem de registro; não são reproduzíveis nem correspondem à interface atual | não se aplica |
| [`rtl_sdr/`](rtl_sdr/) | Estação RS41 com RTL-SDR (`radiosonde_auto_rx` + SondeHub, indicativo `LCA-UNB`) | [`LEIA-ME.md`](rtl_sdr/LEIA-ME.md) |
| [`ensaio_missao/`](ensaio_missao/) | Ensaio acelerado de seis horas de missão para o sistema de logs | `python tools/ensaio_missao/soak_mission.py --packets 21600 --rate 100` |

Os scripts são executados a partir da raiz do repositório.
