# Balao-I

Projeto de balão estratosférico com telemetria LoRa. Contém 3 códigos Arduino (Plataforma Heltec ESP32 LoRa V3):

## `src/LoraBordo.ino` — Transmissor (a bordo do balão)

Código embarcado no balão. Realiza leituras periódicas de **GPS u-blox SAM-M10Q**, **BME280** (temperatura, pressão, umidade) e **BNO086** (acelerômetro, giroscópio, magnetômetro) e transmite tudo via rádio **LoRa a 915 MHz** em formato de string texto.

## `src/LoraSolo.ino` — Receptor (estação terrestre)

Código da estação de solo. Escuta o canal LoRa continuamente, recebe os pacotes transmitidos pelo balão, faz o parsing da string e exibe os dados no Serial Monitor.

## `src/LiberacaoCarga.ino` — Balança de liberação

Controla uma célula de carga com **ADC HX711** para medir peso. Quando o peso lido é **≤ 300g**, aciona um relé (pino 25) — utilizado para liberar uma carga útil (paraquedas) do balão.
