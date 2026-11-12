#!/usr/bin/env bash
# Arduino Uno (ATmega328P) icin GERCEK derleme: Arduino AVR cekirdegi + MFRC522 / Keypad / LiquidCrystal_I2C ile.
# Donanim olmadan "bu taslak gercekten derleniyor mu, RAM/flash ne kadar" sorusunu yanitlar.
#
# Gerekenler (Linux/WSL):  avr-gcc, avr-libc, binutils-avr  (Ubuntu: sudo apt install gcc-avr avr-libc binutils-avr)
# Kutuphane kaynaklari (GitHub'dan indirilmis klasorler) AVR_KAYNAK dizinindedir:
#   core/ (arduino/ArduinoCore-avr), mfrc522/ (miguelbalboa/rfid), keypad/ (Chris--A/Keypad), lcdi2c/ (fdebrabander/...)
#
# Kullanim:  AVR_KAYNAK=~/tools/src ./avr_derle.sh [taslak_klasoru] [cikti_klasoru]
#   LTO=0  baglama zamani optimizasyonunu kapatir (eski avr-gcc 5.x'te LTO bazi PSTR kullanimlarinda cokebilir;
#          Arduino IDE'nin avr-gcc 7.3'unde sorun yok). LTO'suz boyut, LTO'lu boyuttan biraz buyuktur (ust sinir).
set -euo pipefail

AVR_KAYNAK="${AVR_KAYNAK:?AVR_KAYNAK ayarla (kutuphane kaynaklarinin klasoru)}"
TASLAK="${1:-$(dirname "$0")/../atm_terminal}"
CIKTI="${2:-/tmp/avr_cikti}"
KOK="$AVR_KAYNAK/core"

mkdir -p "$CIKTI/obj"
rm -f "$CIKTI"/obj/*.o

LTO="${LTO:-1}"
LTO_BAYRAK=(); LTO_BAGLA=(); [ "$LTO" = "1" ] && { LTO_BAYRAK=(-flto -fno-fat-lto-objects); LTO_BAGLA=(-flto); }
BAYRAK=(-mmcu=atmega328p -DF_CPU=16000000UL -DARDUINO=10819 -DARDUINO_AVR_UNO -DARDUINO_ARCH_AVR
        -Os -ffunction-sections -fdata-sections -fno-exceptions "${LTO_BAYRAK[@]}")
UYARI=(-w)   # kutuphane/cekirdek uyarilari gurultu: yalniz taslak dosyalarinda -Wall -Wextra acilir
INC=(-I"$KOK/cores/arduino" -I"$KOK/variants/standard" -I"$KOK/libraries/Wire/src" -I"$KOK/libraries/SPI/src" -I"$KOK/libraries/EEPROM/src"
     -I"$AVR_KAYNAK/mfrc522/src" -I"$AVR_KAYNAK/keypad/src" -I"$AVR_KAYNAK/lcdi2c" -I"$TASLAK")

derle() {   # derle <kaynak>
  local k="$1" ad; ad="$(echo "$k" | md5sum | cut -c1-8)_$(basename "$k")"
  case "$k" in
    *.c)   avr-gcc "${BAYRAK[@]}" "${UYARI[@]}" -std=gnu11 "${INC[@]}" -c "$k" -o "$CIKTI/obj/$ad.o" ;;
    *.S)   avr-gcc "${BAYRAK[@]}" "${UYARI[@]}" -x assembler-with-cpp "${INC[@]}" -c "$k" -o "$CIKTI/obj/$ad.o" ;;
    *)     avr-g++ "${BAYRAK[@]}" "${UYARI[@]}" -std=gnu++11 -fno-threadsafe-statics -fpermissive "${INC[@]}" -c "$k" -o "$CIKTI/obj/$ad.o" ;;
  esac
}

# 1) Arduino cekirdegi + kutuphaneler
for k in "$KOK"/cores/arduino/*.c "$KOK"/cores/arduino/*.cpp "$KOK"/cores/arduino/*.S \
         "$KOK"/libraries/Wire/src/*.cpp "$KOK"/libraries/Wire/src/utility/*.c \
         "$KOK"/libraries/SPI/src/*.cpp \
         "$AVR_KAYNAK"/mfrc522/src/MFRC522.cpp "$AVR_KAYNAK"/keypad/src/*.cpp "$AVR_KAYNAK"/lcdi2c/*.cpp; do
  [ -f "$k" ] && derle "$k"
done

# 2) Taslak: .ino -> .cpp (Arduino IDE gibi basa Arduino.h eklenir), digerleri oldugu gibi
GECICI="$CIKTI/taslak"; rm -rf "$GECICI"; mkdir -p "$GECICI"
for f in "$TASLAK"/*.cpp "$TASLAK"/*.h; do [ -f "$f" ] && cp "$f" "$GECICI/"; done
for f in "$TASLAK"/*.ino; do { echo '#include <Arduino.h>'; echo "#line 1 \"$(basename "$f")\""; cat "$f"; } > "$GECICI/$(basename "$f" .ino).ino.cpp"; done
INC+=(-I"$GECICI")
UYARI=(-Wall -Wextra -Wno-unused-parameter)
for k in "$GECICI"/*.cpp; do derle "$k"; done

# 3) Baglama + rapor
avr-gcc -mmcu=atmega328p -Os "${LTO_BAGLA[@]}" -Wl,--gc-sections -o "$CIKTI/firmware.elf" "$CIKTI"/obj/*.o -lm
avr-objcopy -O ihex -R .eeprom "$CIKTI/firmware.elf" "$CIKTI/firmware.hex"
avr-size -C --mcu=atmega328p "$CIKTI/firmware.elf"
