#!/usr/bin/env bash
# Firmware Laboratuvari icin firmware cekirdegini WebAssembly'ye derler (tek dosya: wasm JS'in icine gomulu).
# Gerekenler: Emscripten (emcc).  Ubuntu: sudo apt install emscripten   |   ya da https://emscripten.org (emsdk)
# Cikti: backend/banka/static/banka/firmware-lab/vx_firmware.js  (repoda hazir durur; Windows'ta emcc gerekmez)
# Kullanim:  ./arduino/wasm/derle.sh [cikti.js]
set -euo pipefail
BU="$(cd "$(dirname "$0")" && pwd)"
CIKTI="${1:-$BU/../../backend/banka/static/banka/firmware-lab/vx_firmware.js}"
mkdir -p "$(dirname "$CIKTI")"
emcc -O2 -std=c++11 -Wall -Wextra -Wno-unused-parameter \
  "$BU/vx_wasm.cpp" "$BU"/../atm_terminal/{metin,cerceve,baglanti,acilis,terminal}.cpp \
  -o "$CIKTI" \
  -sMODULARIZE=1 -sEXPORT_NAME=VxModul -sSINGLE_FILE=1 -sALLOW_MEMORY_GROWTH=1 \
  -sENVIRONMENT=web,node -sEXPORTED_RUNTIME_METHODS=ccall,cwrap,UTF8ToString
echo "Hazir: $CIKTI ($(wc -c < "$CIKTI") bayt)"
