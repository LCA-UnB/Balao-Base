#!/usr/bin/env bash
set -euo pipefail
station_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

for command_name in lsusb rtl_test rtl_power rtl_fm; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        printf 'Dependencia ausente: %s. Consulte %s/LEIA-ME.md.\n' "$command_name" "$station_dir" >&2
        exit 1
    fi
done

cd "$station_dir/source/auto_rx"
if ! "$station_dir/venv/bin/python" -c 'import flask, flask_socketio, numpy, requests, semver, simple_websocket, dateutil' 2>/dev/null; then
    printf 'Faltam bibliotecas Python. Execute: bash "%s/concluir-instalacao.sh"\n' "$station_dir" >&2
    exit 1
fi

for decoder in rs41mod fsk_demod dft_detect; do
    if [[ ! -x "$station_dir/source/auto_rx/$decoder" ]]; then
        printf 'Decodificadores nao compilados. Execute: bash "%s/concluir-instalacao.sh"\n' "$station_dir" >&2
        exit 1
    fi
done

mkdir -p "$station_dir/source/auto_rx/log"
exec "$station_dir/venv/bin/python" auto_rx.py -c "$station_dir/station.cfg" "$@"
