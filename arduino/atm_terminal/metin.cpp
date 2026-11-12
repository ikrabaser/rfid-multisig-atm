#include "metin.h"

namespace vx {

size_t uzunluk(const char *s) {
  size_t n = 0;
  while (s[n]) n++;
  return n;
}

size_t flash_uzunluk(FS f) {
  size_t n = 0;
  while (VX_FLASH_BAYT(f.p + n)) n++;
  return n;
}

bool kopyala(char *hedef, size_t kap, const char *kaynak) {
  if (kap == 0) return false;
  size_t i = 0;
  for (; kaynak[i] && i + 1 < kap; i++) hedef[i] = kaynak[i];
  hedef[i] = 0;
  return kaynak[i] == 0;
}

bool kopyala(char *hedef, size_t kap, FS kaynak) {
  if (kap == 0) return false;
  size_t i = 0;
  char c;
  while ((c = (char)VX_FLASH_BAYT(kaynak.p + i)) != 0 && i + 1 < kap) {
    hedef[i++] = c;
  }
  hedef[i] = 0;
  return (char)VX_FLASH_BAYT(kaynak.p + i) == 0;
}

bool esit(const char *a, const char *b) {
  size_t i = 0;
  while (a[i] == b[i]) {
    if (!a[i]) return true;
    i++;
  }
  return false;
}

bool esit(const char *a, FS b) {
  for (size_t i = 0;; i++) {
    char c = (char)VX_FLASH_BAYT(b.p + i);
    if (a[i] != c) return false;
    if (!c) return true;
  }
}

bool baslar(const char *s, FS onek) {
  for (size_t i = 0;; i++) {
    char c = (char)VX_FLASH_BAYT(onek.p + i);
    if (!c) return true;
    if (s[i] != c) return false;
  }
}

void sil(char *t, size_t n) {
  volatile char *p = t;
  while (n--) *p++ = 0;
}

Yazici::Yazici(char *tampon, size_t kapasite) : tasti(false), t_(tampon), kap_(kapasite), n_(0) {
  if (kap_) t_[0] = 0;
}

Yazici &Yazici::ekle(char c) {
  if (n_ + 1 < kap_) {
    t_[n_++] = c;
    t_[n_] = 0;
  } else {
    tasti = true;
  }
  return *this;
}

Yazici &Yazici::ekle(const char *s) {
  while (*s) ekle(*s++);
  return *this;
}

Yazici &Yazici::ekle(FS s) {
  char c;
  for (size_t i = 0; (c = (char)VX_FLASH_BAYT(s.p + i)) != 0; i++) ekle(c);
  return *this;
}

Yazici &Yazici::ekle_sayi(uint32_t v) {
  char b[11];
  uint8_t n = 0;
  do {
    b[n++] = (char)('0' + v % 10);
    v /= 10;
  } while (v);
  while (n) ekle(b[--n]);
  return *this;
}

Yazici &Yazici::ekle_hex(uint32_t v, uint8_t hane) {
  while (hane) {
    uint8_t d = (uint8_t)((v >> (4 * (hane - 1))) & 0xF);
    ekle((char)(d < 10 ? '0' + d : 'a' + (d - 10)));
    hane--;
  }
  return *this;
}

void Alanlar::bol(char *satir) {
  n_ = 0;
  a_[n_++] = satir;
  for (char *p = satir; *p; p++) {
    if (*p == '|' && n_ < MAKS) {
      *p = 0;
      a_[n_++] = p + 1;
    }
  }
}

}  // namespace vx
