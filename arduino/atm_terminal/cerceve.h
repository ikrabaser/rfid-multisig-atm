// Seri hat cerceveleme (protokol v2) - bridge/cerceve.py ile BIREBIR ayni; ortak test vektorleri:
// arduino/test/vektorler.txt (iki taraf da ayni dosyayi dogrular).
//
//   SS:yuk*CCCC\n    SS: 2 haneli buyuk harf onaltilik sira no ("--": NAK)   CCCC: "SS:yuk" CRC-16/CCITT-FALSE
#pragma once
#include "tasinabilir.h"

namespace vx {

enum { SURUM = 2, YUK_EN_COK = 118, CERCEVE_EN_COK = 126 };  // 3 + 118 + 5 = 126 (satir sonu haric)

uint16_t crc16(const char *veri, size_t n);  // crc16("123456789", 9) == 0x29B1

// "SS:yuk*CCCC\n" yazar; yazilan bayt sayisini (\n dahil) dondurur, sigmazsa/gecersizse 0.
size_t cerceve_kodla(char *cikis, size_t kap, uint8_t sira, bool nak, const char *yuk);

// Yuku zaten tampon+3'te duran (n bayt) bir cerceveyi YERINDE tamamlar: basa "SS:", sona "*CCCC\n" yazar.
// tampon en az n + 9 bayt olmali; yazilan bayt sayisini dondurur (yuk yazdirilamaz bayt iceriyorsa ya da cok uzunsa 0).
size_t cerceve_yerinde(char *tampon, size_t kap, size_t n, uint8_t sira);

// `satir` (n bayt, satir sonu OLMADAN) cerceve ise sira/yuk'u cikarir. Yuk, satir icinde YERINDE NUL ile bitirilir.
// CRC tutmayan, bicimi bozuk ya da yazdirilamaz bayt iceren satirlari reddeder.
bool cerceve_coz(char *satir, size_t n, uint8_t &sira, bool &nak, char *&yuk);

}  // namespace vx
