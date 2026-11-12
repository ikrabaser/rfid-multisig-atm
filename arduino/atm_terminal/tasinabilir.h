// VAULTIX terminal cekirdegi - AVR (Arduino Uno) ile PC (testler) arasinda ortak tasinabilirlik katmani.
//
// Cekirdek (metin, cerceve, baglanti, terminal) Arduino.h'a BAGIMLI DEGILDIR; yalnizca bu basligi kullanir.
// Boylece ayni kod hem Uno'da hem de PC'de (g++ ile, arduino/test/) calisir ve test edilebilir.
#pragma once
#include <stddef.h>
#include <stdint.h>

#if defined(__AVR__)
#include <avr/pgmspace.h>
#define VX_FLASH_BAYT(p) pgm_read_byte(p)
#define VX_PSTR(x) PSTR(x)
#else
#define VX_FLASH_BAYT(p) (*reinterpret_cast<const unsigned char *>(p))
#define VX_PSTR(x) (x)
#endif

namespace vx {

// Flash'taki (AVR'de PROGMEM) bir metin. AYRI bir tur oldugu icin RAM'deki const char* ile karistirilamaz:
// "FM() yerine duz metin" ya da tersi PC'de calisip AVR'de cop okuyan hatalar derleme hatasi olur.
struct FS {
  const char *p;
};

// Not: FS{PSTR(x)} (suslu parantezle) yazmak avr-gcc 5.x'te PSTR'nin static dizisini uretmiyor ("undefined reference ...::__c").
// Duz fonksiyon cagrisi kalibi (Arduino'nun F() makrosuyla ayni) guvenilir.
inline FS fs(const char *p) {
  FS f;
  f.p = p;
  return f;
}

}  // namespace vx

// Kullanim: ekran(FM("Karti okutun")). Yalnizca fonksiyon icinde kullanilir (PSTR gerekliligi).
#define FM(x) (::vx::fs(VX_PSTR(x)))
