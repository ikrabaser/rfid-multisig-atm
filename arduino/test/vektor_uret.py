#!/usr/bin/env python3
"""Ortak test vektorleri: Python (referans uygulama, bridge/cerceve.py) uretir, hem Python hem C++ testleri dogrular.
Boylece firmware'deki C++ CRC/cerceve kodu ile koprudeki Python kodu BIREBIR ayni sonucu verir.

Kullanim:  python arduino/test/vektor_uret.py          # arduino/test/vektorler.txt dosyasini yeniden yazar
           python arduino/test/vektor_uret.py --kontrol  # dosya guncel mi (testler bunu kullanir)
Format (sekme ayracli):  OK <sira_hex> <yuk> <cerceve>   |   BAD <cerceve>   |   CRC <hex_dize> <crc_hex>
"""
import random
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(KOK / "bridge"))
import cerceve  # noqa: E402

DOSYA = Path(__file__).with_name("vektorler.txt")

YUKLER = [
    (0x00, "HELLO|2"), (0x01, "PING"), (0x0A, "KART|a0a0a001"), (0x7F, "PIN|a0a0a001|1234"),
    (0xFF, "ONAY|12|b0b0b003|4321|E"), (0x10, "BEKLE|7|3"), (0x11, ""), (0x12, "A*B|*|**"),
    (0x13, "ISLEM|1:1vAbCd:" + "x" * 43 + "|C|100.50||1a2b3c4d"),
    (0x14, "x" * cerceve.YUK_EN_COK),
    (0x15, "HATA|Sunucu yanitsiz"), (0x16, "TAMAM|1234.50|55|200x5+100x2"),
]


def uret():
    satirlar = ["# otomatik uretildi: python arduino/test/vektor_uret.py"]
    for ham in ("", "a", "123456789", "KART|a0a0a001", "\x00\xff\x10"):
        veri = ham.encode("latin-1")
        satirlar.append(f"CRC\t{veri.hex().upper()}\t{cerceve.crc16(veri):04X}")
    for sira, yuk in YUKLER:
        satirlar.append(f"OK\t{sira:02X}\t{yuk}\t{cerceve.kodla(sira, yuk)}")
    iyi = cerceve.kodla(0x0A, "KART|a0a0a001")
    kotu = [
        iyi[:-1],                                   # CRC eksik hane
        iyi[:-5],                                   # CRC yok
        iyi.replace("KART", "KARD"),                # yuk degisti
        iyi[:3] + "X" + iyi[4:],                    # yuk bozuk
        "0A KART|a0a0a001*" + iyi[-4:],             # ':' yerine bosluk
        "ZZ:KART|a0a0a001*" + iyi[-4:],             # sira onaltilik degil
        "0A:KART|a0a0a001*GGGG",                    # CRC onaltilik degil
        "KART|a0a0a001",                            # cercevesiz
        "",                                         # bos
        "*0000",
    ]
    for k in kotu:
        satirlar.append(f"BAD\t{k}")
    # rastgele tek bit hatalari: hepsi BAD olmali (CRC16: 3 bite kadar hata kesin yakalanir)
    rng = random.Random(7)
    for _ in range(40):
        sira, yuk = YUKLER[rng.randrange(len(YUKLER))]
        f = bytearray(cerceve.kodla(sira, yuk).encode())
        f[rng.randrange(len(f))] ^= 1 << rng.randrange(7)     # 7 bit: yazdirilabilir kalsin
        if 0x20 <= min(f) and max(f) < 0x7F and bytes(f) != cerceve.kodla(sira, yuk).encode():
            satirlar.append(f"BAD\t{f.decode()}")
    return "\n".join(satirlar) + "\n"


if __name__ == "__main__":
    yeni = uret()
    if "--kontrol" in sys.argv:
        sys.exit(0 if DOSYA.exists() and DOSYA.read_text(encoding="utf-8") == yeni else 1)
    DOSYA.write_text(yeni, encoding="utf-8", newline="\n")
    print(f"{DOSYA} yazildi ({yeni.count(chr(10))} satir)")
