#include "cerceve.h"

namespace vx {

uint16_t crc16(const char *veri, size_t n) {
  uint16_t crc = 0xFFFF;
  while (n--) {
    crc ^= (uint16_t)((uint8_t)*veri++) << 8;
    for (uint8_t i = 0; i < 8; i++) crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
  }
  return crc;
}

static char hex_char(uint8_t d) { return (char)(d < 10 ? '0' + d : 'A' + (d - 10)); }

static int hex_deger(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  return -1;
}

static bool yazdirilabilir(char c) { return (uint8_t)c >= 0x20 && (uint8_t)c <= 0x7E; }

size_t cerceve_yerinde(char *tampon, size_t kap, size_t n, uint8_t sira) {
  if (n > YUK_EN_COK || kap < n + 9) return 0;  // "SS:" + yuk + "*CCCC" + "\n"
  for (size_t i = 0; i < n; i++) {
    if (!yazdirilabilir(tampon[3 + i])) return 0;
  }
  tampon[0] = hex_char((uint8_t)(sira >> 4));
  tampon[1] = hex_char((uint8_t)(sira & 0xF));
  tampon[2] = ':';
  uint16_t crc = crc16(tampon, 3 + n);
  char *p = tampon + 3 + n;
  *p++ = '*';
  *p++ = hex_char((uint8_t)(crc >> 12));
  *p++ = hex_char((uint8_t)((crc >> 8) & 0xF));
  *p++ = hex_char((uint8_t)((crc >> 4) & 0xF));
  *p++ = hex_char((uint8_t)(crc & 0xF));
  *p++ = '\n';
  return (size_t)(p - tampon);
}

size_t cerceve_kodla(char *cikis, size_t kap, uint8_t sira, bool nak, const char *yuk) {
  size_t n = 0;
  while (yuk[n]) {
    if (n >= YUK_EN_COK) return 0;
    n++;
  }
  if (kap < n + 9) return 0;
  for (size_t i = 0; i < n; i++) cikis[3 + i] = yuk[i];
  size_t toplam = cerceve_yerinde(cikis, kap, n, sira);
  if (toplam && nak) {  // NAK: "--" sira alani; CRC yeniden hesaplanir
    cikis[0] = '-';
    cikis[1] = '-';
    uint16_t crc = crc16(cikis, 3 + n);
    char *p = cikis + 3 + n + 1;
    *p++ = hex_char((uint8_t)(crc >> 12));
    *p++ = hex_char((uint8_t)((crc >> 8) & 0xF));
    *p++ = hex_char((uint8_t)((crc >> 4) & 0xF));
    *p++ = hex_char((uint8_t)(crc & 0xF));
  }
  return toplam;
}

bool cerceve_coz(char *satir, size_t n, uint8_t &sira, bool &nak, char *&yuk) {
  if (n < 8 || n > CERCEVE_EN_COK) return false;  // en az "SS:" + "*CCCC"
  for (size_t i = 0; i < n; i++) {
    if (!yazdirilabilir(satir[i])) return false;
  }
  if (satir[2] != ':' || satir[n - 5] != '*') return false;

  int c[4];
  for (uint8_t i = 0; i < 4; i++) {
    c[i] = hex_deger(satir[n - 4 + i]);
    if (c[i] < 0) return false;
  }
  uint16_t gelen = (uint16_t)((c[0] << 12) | (c[1] << 8) | (c[2] << 4) | c[3]);
  if (crc16(satir, n - 5) != gelen) return false;

  if (satir[0] == '-' && satir[1] == '-') {
    nak = true;
    sira = 0;
  } else {
    int h = hex_deger(satir[0]), l = hex_deger(satir[1]);
    if (h < 0 || l < 0) return false;
    nak = false;
    sira = (uint8_t)((h << 4) | l);
  }
  satir[n - 5] = 0;
  yuk = satir + 3;
  return true;
}

}  // namespace vx
