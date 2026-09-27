# Estação LCA-UNB — RTL-SDR / RS41

Estação automática para localizar, decodificar, registrar e publicar radiossondas no SondeHub. A solução usa o [`radiosonde_auto_rx`](https://github.com/projecthorus/radiosonde_auto_rx) 1.9.0, revisão `53d03c72ad18ce4357c0cedd1f4acf2bf1efb36e`, distribuído sob GPL-3.0.

O código de terceiros está preservado em `source/`. A adição local dentro dessa árvore é `source/auto_rx/autorx/static/localizar-antena.html`, uma página auxiliar que consulta a geolocalização do navegador sem transmitir o resultado. Ambientes virtuais, logs, caches e binários compilados são gerados localmente e não são versionados.

## O que foi configurado

- estação `LCA-UNB`;
- um RTL-SDR no índice `0`, ganho automático, correção inicial de `0 ppm` e bias tee desligado;
- varredura entre 400,05 e 406 MHz;
- decodificação de RS41 e dos demais modelos compatíveis com o Auto-RX;
- painel local em <http://localhost:5000>;
- um arquivo de log por sonda em `source/auto_rx/log/`;
- envio de telemetria ao SondeHub a cada 15 segundos;
- publicação do marcador da estação no SondeHub.

A configuração fixa da antena está em `station.cfg`. Como `upload_listener_position = True`, essa localização fica pública no mapa do SondeHub. A altitude da estação ainda está em `0.0 m` e deve ser corrigida quando for medida.

## Dependências do sistema

No Arch Linux:

```bash
sudo pacman -S --needed base-devel python rtl-sdr usbutils
```

No Debian/Ubuntu:

```bash
sudo apt install build-essential python3 python3-venv rtl-sdr usbutils
```

Feche o SDR++ ou qualquer outro programa que esteja usando o dongle antes de iniciar o Auto-RX.

## Primeira instalação

A partir da raiz do `Balao-Base`:

```bash
bash tools/rtl_sdr/concluir-instalacao.sh
```

O script cria `tools/rtl_sdr/venv`, instala as bibliotecas Python, compila os decodificadores nativos e valida a configuração.

## Iniciar

```bash
bash tools/rtl_sdr/iniciar.sh
```

Depois, abra <http://localhost:5000>. Use `Ctrl+C` no terminal para encerrar.

Argumentos adicionais do Auto-RX podem ser passados ao iniciador. Para fixar temporariamente uma RS41 em 403,001 MHz, por exemplo:

```bash
bash tools/rtl_sdr/iniciar.sh -f 403.001 -m RS41
```

## Validação realizada

Em 19/09/2026, a estação decodificou a RS41 `X3922156` em 403,001 MHz, gravou a telemetria local e teve pacotes aceitos pela API do SondeHub com o indicativo `LCA-UNB`.

O SoX não é necessário para a cadeia IQ padrão da RS41, mas algumas cadeias de demodulação de outros modelos podem exigir esse programa.
