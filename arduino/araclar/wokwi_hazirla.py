#!/usr/bin/env python3
"""Wokwi icin TEK DOSYALIK firmware uretir: arduino/wokwi/proje/sketch.ino

Cekirdek dosyalari (.h/.cpp) ve atm_terminal.ino bagimlilik sirasiyla tek sketch.ino'da birlestirilir;
Wokwi'ye yalnizca sketch.ino'nun icerigini yapistirmak yeter. diagram.json ve libraries.txt de yanina kopyalanir.
Kaynak tek yerde kalir (arduino/atm_terminal); uretilen klasor commitlenmez.

Kullanim:  python arduino/araclar/wokwi_hazirla.py [--gercek-zaman]

Varsayilan "insan hizi": cevap bekleme suresi 20 sn (kopru cevabini elle yapistirabilmek icin).
--gercek-zaman: firmware varsayilani (2 sn).
"""
import pathlib
import re
import shutil
import sys

KOK = pathlib.Path(__file__).resolve().parent.parent
KAYNAK = KOK / "atm_terminal"
WOKWI = KOK / "wokwi"
CIKTI = WOKWI / "proje"

# bagimlilik sirasi (once tipler/yardimcilar, sonra katmanlar, en sonda terminal ve .ino)
SIRA = ["tasinabilir.h", "metin.h", "donanim.h", "cerceve.h", "baglanti.h", "acilis.h", "terminal.h",
        "metin.cpp", "cerceve.cpp", "baglanti.cpp", "acilis.cpp", "terminal.cpp", "atm_terminal.ino"]
YEREL = re.compile(r'^\s*#\s*include\s*"[^"]+"\s*$')
SISTEM = re.compile(r'^\s*#\s*include\s*<[^>]+>\s*$')

mevcut = {f.name for f in KAYNAK.iterdir() if f.suffix in (".h", ".cpp", ".ino")}
eksik = mevcut - set(SIRA)
if eksik:
    raise SystemExit(f"SIRA listesinde olmayan dosya(lar): {sorted(eksik)} -- wokwi_hazirla.py'yi guncelle")

sistem, govde = [], []
for ad in SIRA:
    govde.append(f"\n// ===== {ad} =====")
    for satir in (KAYNAK / ad).read_text(encoding="utf-8").replace("\r\n", "\n").split("\n"):
        if satir.strip() == "#pragma once" or YEREL.match(satir):
            continue
        if SISTEM.match(satir):
            if satir.strip() not in sistem:
                sistem.append(satir.strip())
            continue
        govde.append(satir)

shutil.rmtree(CIKTI, ignore_errors=True)
CIKTI.mkdir(parents=True)
ayar = "" if "--gercek-zaman" in sys.argv else "#define VX_DENEME_MS 20000   // Wokwi: elle cevap icin 20 sn (3 deneme)\n"
(CIKTI / "sketch.ino").write_text(
    "// VAULTIX terminali - Wokwi icin birlestirilmis tek dosya (wokwi_hazirla.py uretir, elle duzenleme)\n"
    + ayar + "\n".join(sistem) + "\n" + "\n".join(govde) + "\n", encoding="utf-8", newline="\n")
for ad in ("diagram.json", "libraries.txt"):
    shutil.copy(WOKWI / ad, CIKTI / ad)
boyut = (CIKTI / "sketch.ino").stat().st_size
print(f"Hazir: {CIKTI / 'sketch.ino'}  ({boyut} bayt)")
print("Wokwi'de sketch.ino sekmesinin icerigini bununla DEGISTIR (tamamini sec, yapistir).")
