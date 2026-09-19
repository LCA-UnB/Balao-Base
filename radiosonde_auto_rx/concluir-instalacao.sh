#!/usr/bin/env bash
set -euo pipefail
station_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

missing_commands=()
for command_name in python3 make cc lsusb rtl_test rtl_power rtl_fm; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        missing_commands+=("$command_name")
    fi
done

if (( ${#missing_commands[@]} > 0 )); then
    printf 'Dependencias do sistema ausentes: %s\n' "${missing_commands[*]}" >&2
    printf 'Arch Linux: sudo pacman -S --needed base-devel python rtl-sdr usbutils\n' >&2
    printf 'Debian/Ubuntu: sudo apt install build-essential python3 python3-venv rtl-sdr usbutils\n' >&2
    exit 1
fi

if [[ ! -x "$station_dir/venv/bin/python" ]]; then
    python3 -m venv "$station_dir/venv"
fi
"$station_dir/venv/bin/python" -m pip install --no-cache-dir -r "$station_dir/source/auto_rx/requirements.txt"
"$station_dir/venv/bin/python" -m pip check

cd "$station_dir/source/auto_rx"

if [[ ! -x rs41mod || ! -x fsk_demod || ! -x dft_detect ]]; then
    bash build.sh
fi

mkdir -p log
"$station_dir/venv/bin/python" -c 'from autorx.config import read_auto_rx_config; from autorx.utils import check_rs_utils; c = read_auto_rx_config("../../station.cfg", no_sdr_test=True); assert c is not None, "Configuracao invalida"; assert check_rs_utils(c), "Decodificadores ausentes"; print("Configuracao e decodificadores verificados.")'
printf 'Instalacao concluida. Conecte o RTL-SDR e execute: bash "%s/iniciar.sh"\n' "$station_dir"
