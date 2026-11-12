// Cerceve/CRC: ortak vektorler (vektorler.txt, Python tarafi da ayni dosyayi dogrular) + bicim/bozulma testleri.
#include <fstream>
#include <sstream>

#include "dunya.h"

using namespace vx;

TEST(crc_standart_kontrol_degeri) {
  KONTROL(crc16("123456789", 9) == 0x29B1);
  KONTROL(crc16("", 0) == 0xFFFF);
}

static std::string hex_coz(const std::string &h) {
  std::string s;
  for (size_t i = 0; i + 1 < h.size(); i += 2) s += (char)std::stoi(h.substr(i, 2), nullptr, 16);
  return s;
}

TEST(ortak_vektorler) {
  std::ifstream f("vektorler.txt");
  KONTROL(f.good());
  std::string satir;
  int crc_n = 0, ok_n = 0, bad_n = 0;
  while (std::getline(f, satir)) {
    if (satir.empty() || satir[0] == '#') continue;
    std::vector<std::string> a;
    std::stringstream ss(satir);
    std::string p;
    while (std::getline(ss, p, '\t')) a.push_back(p);
    if (a[0] == "CRC") {
      std::string v = hex_coz(a[1]);
      KONTROL(crc16(v.data(), v.size()) == std::stoul(a[2], nullptr, 16));
      crc_n++;
    } else if (a[0] == "OK") {
      uint8_t sira = (uint8_t)std::stoi(a[1], nullptr, 16);
      std::string yuk = a.size() > 2 ? a[2] : "";
      std::string beklenen = a.size() > 3 ? a[3] : "";
      char cikis[160];
      size_t n = cerceve_kodla(cikis, sizeof cikis, sira, false, yuk.c_str());
      KONTROL(n > 0 && std::string(cikis, n - 1) == beklenen);
      char tampon[160];
      std::strcpy(tampon, beklenen.c_str());
      uint8_t s2 = 0;
      bool nak = false;
      char *y2 = nullptr;
      KONTROL(cerceve_coz(tampon, beklenen.size(), s2, nak, y2));
      KONTROL(s2 == sira && !nak && yuk == y2);
      ok_n++;
    } else if (a[0] == "BAD") {
      std::string s = a.size() > 1 ? a[1] : "";
      char tampon[200];
      std::strncpy(tampon, s.c_str(), sizeof tampon - 1);
      tampon[sizeof tampon - 1] = 0;
      uint8_t s2;
      bool nak;
      char *y2;
      KONTROL(!cerceve_coz(tampon, s.size(), s2, nak, y2));
      bad_n++;
    }
  }
  KONTROL(crc_n >= 3 && ok_n >= 5 && bad_n >= 40);
}

TEST(kodla_coz_gidis_donus_tum_sira_numaralari) {
  for (int sira = 0; sira < 256; sira++) {
    char b[160];
    size_t n = cerceve_kodla(b, sizeof b, (uint8_t)sira, false, "KART|a0a0a001");
    KONTROL(n > 0);
    std::string ref = ref_kodla(sira, "KART|a0a0a001");
    KONTROL_ESIT(std::string(b, n - 1), ref);
    uint8_t s2;
    bool nak;
    char *y;
    KONTROL(cerceve_coz(b, n - 1, s2, nak, y) && s2 == sira);
  }
}

TEST(nak_cercevesi) {
  char b[160];
  size_t n = cerceve_kodla(b, sizeof b, 0, true, "NAK|CRC");
  KONTROL_ESIT(std::string(b, n - 1), ref_kodla(0, "NAK|CRC", true));
  uint8_t s;
  bool nak = false;
  char *y;
  KONTROL(cerceve_coz(b, n - 1, s, nak, y) && nak);
}

TEST(cok_uzun_ve_yazdirilamaz_yuk_kodlanmaz) {
  char b[200];
  KONTROL(cerceve_kodla(b, sizeof b, 1, false, std::string(YUK_EN_COK, 'x').c_str()) == YUK_EN_COK + 9);
  KONTROL(cerceve_kodla(b, sizeof b, 1, false, std::string(YUK_EN_COK + 1, 'x').c_str()) == 0);
  KONTROL(cerceve_kodla(b, sizeof b, 1, false, "a\nb") == 0);
  KONTROL(cerceve_kodla(b, sizeof b, 1, false, "caf\xc3\xa9") == 0);
  char kucuk[10];
  KONTROL(cerceve_kodla(kucuk, sizeof kucuk, 1, false, "PING") == 0);  // tampon kucuk
}

TEST(en_fazla_uc_bit_hatasi_asla_kabul_edilmez) {
  std::mt19937 rng(7);
  int denenen = 0;
  for (int i = 0; i < 4000; i++) {
    std::string yuk;
    int n = rng() % 80;
    for (int k = 0; k < n; k++) yuk += "ABCabc012|*. "[rng() % 13];
    int sira = rng() % 256;
    std::string f = ref_kodla(sira, yuk);
    std::string bozuk = f;
    int bit = 1 + rng() % 3;
    for (int k = 0; k < bit; k++) bozuk[rng() % bozuk.size()] ^= (char)(1 << (rng() % 7));
    if (bozuk == f) continue;
    char tampon[200];
    std::strcpy(tampon, bozuk.c_str());
    uint8_t s2;
    bool nak;
    char *y;
    denenen++;
    if (cerceve_coz(tampon, bozuk.size(), s2, nak, y)) {
      // yalnizca CRC'deki buyuk/kucuk harf farki (anlam ayni) kabul edilebilir
      KONTROL(s2 == sira && yuk == y);
    }
  }
  KONTROL(denenen > 3000);
}
