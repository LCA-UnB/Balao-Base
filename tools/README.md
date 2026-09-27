# tools/

Ferramentas auxiliares do Balao-Base. Cada subpasta é independente e tem seu próprio guia.

| Pasta | O que é | Como começar |
|---|---|---|
| [`calibrar_bateria/`](calibrar_bateria/) | Sketch Arduino (`calibrar_bateria.ino`) para levantar a curva do ADC da bateria com fonte de bancada, mais o guia e o modelo de planilha | [`CALIBRACAO_BATERIA.md`](calibrar_bateria/CALIBRACAO_BATERIA.md) |
| [`rastreador_sonda/`](rastreador_sonda/) | **Legada.** Interface Tkinter anterior do rastreador (`tracker.py`), sem missões, reprodução nem telecomando. A atual é `src/tracker.py` | `python tools/rastreador_sonda/tracker.py` |
| [`rtl_sdr/`](rtl_sdr/) | Estação RS41 com RTL-SDR (`radiosonde_auto_rx` + SondeHub, indicativo `LCA-UNB`) | [`LEIA-ME.md`](rtl_sdr/LEIA-ME.md) |
| [`ensaio_missao/`](ensaio_missao/) | Ensaio acelerado de seis horas de missão para o sistema de logs | `python tools/ensaio_missao/soak_mission.py --packets 21600 --rate 100` |

Os scripts são executados a partir da raiz do repositório.
