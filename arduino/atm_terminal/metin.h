// Sabit tamponlu, tasmaya dayanikli metin yardimcilari. Arduino String SINIFI KULLANILMAZ: 2 KB RAM'de heap
// parcalanmasi (sahada aylar sonra cikan "gizemli" cokmeler) ve gizli bellek ayirmalari olmasin diye.
#pragma once
#include "tasinabilir.h"

namespace vx {

size_t uzunluk(const char *s);
size_t flash_uzunluk(FS f);

// Hedefe en fazla kap-1 karakter kopyalar, daima NUL ile bitirir. Hepsi sigdiysa true (false: kirpildi).
bool kopyala(char *hedef, size_t kap, const char *kaynak);
bool kopyala(char *hedef, size_t kap, FS kaynak);

bool esit(const char *a, const char *b);
bool esit(const char *a, FS b);
bool baslar(const char *s, FS onek);

// Hassas veriyi (PIN, oturum jetonu) bellekten siler; derleyici "kullanilmiyor" diye atlayamaz.
void sil(char *t, size_t n);

// Tasmaya dayanikli ekleyici: sigmayan kisim atilir ve `tasti` isaretlenir (sessizce bozuk istek yollanmaz).
class Yazici {
 public:
  Yazici(char *tampon, size_t kapasite);
  Yazici &ekle(char c);
  Yazici &ekle(const char *s);
  Yazici &ekle(FS s);
  Yazici &ekle_sayi(uint32_t v);
  Yazici &ekle_hex(uint32_t v, uint8_t hane);  // kucuk harf, sifir dolgulu
  size_t uzunluk() const { return n_; }
  bool tasti;

 private:
  char *t_;
  size_t kap_;
  size_t n_;
};

// "A|B|C" satirini YERINDE boler ('|' yerine NUL koyar). Olmayan alan "" doner. En fazla MAKS alan; fazlasi son alana kalir.
class Alanlar {
 public:
  enum { MAKS = 10 };
  void bol(char *satir);
  const char *al(uint8_t i) const { return i < n_ ? a_[i] : ""; }
  uint8_t sayi() const { return n_; }

 private:
  const char *a_[MAKS];
  uint8_t n_ = 0;
};

}  // namespace vx
