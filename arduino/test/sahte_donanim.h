// Sahte donanim: zaman, tus, kart, LCD, role, seri hat tamponlari. Hem testler (dunya.h) hem de firmware simulatoru
// (sim_main.cpp) kullanir.
#pragma once
#include <cstdint>
#include <cstring>
#include <deque>
#include <string>
#include <vector>

#include "../atm_terminal/donanim.h"
#include "../atm_terminal/metin.h"

struct TusOlayi {
  char c;
  uint32_t bas, bitis;
  bool teslim;
};

class SahteDonanim : public vx::Donanim {
 public:
  uint32_t t = 0;
  std::vector<TusOlayi> tuslar;
  std::string bekleyen_kart;      // okutulmayi bekleyen kart (RC522 alaninda)
  bool kart_teslim_edildi = false;
  char lcd[2][17];
  std::vector<std::string> lcd_gecmisi;  // her LCD degisiminde "satir0|satir1"
  int lcd_yazma = 0;
  int bip_tamam = 0, bip_hata = 0;
  bool role_durum = false, led = false;
  std::vector<std::pair<uint32_t, bool>> role_gecmisi;
  std::deque<char> rx_hw;   // kopru -> terminal (donanim tamponu, 63 bayt)
  std::deque<char> tx_hw;   // terminal -> kopru (donanim tamponu, 63 bayt)
  int rx_tasma = 0;
  bool bekci_reset = false;
  int bekci_besleme = 0;
  uint32_t rastgele_durum = 12345;
  uint16_t eeprom[16];   // kalici bellek yuvalari (yazilmamis: 0xFFFF)
  int eeprom_yazma = 0;

  SahteDonanim() {
    std::memset(lcd, 0, sizeof lcd);
    lcd[0][0] = lcd[1][0] = 0;
    for (auto &e : eeprom) e = 0xFFFF;
  }

  uint32_t simdi_ms() override { return t; }
  char tus() override {
    for (auto &o : tuslar) {
      if (!o.teslim && t >= o.bas && t < o.bitis) {
        o.teslim = true;
        return o.c;
      }
    }
    return 0;
  }
  bool kart_oku(char *uid_hex, size_t kap) override {
    if (bekleyen_kart.empty() || kart_teslim_edildi) return false;
    vx::kopyala(uid_hex, kap, bekleyen_kart.c_str());
    kart_teslim_edildi = true;
    return true;
  }
  void lcd_satir(uint8_t satir, const char *metin16) override {
    std::memcpy(lcd[satir], metin16, 17);
    lcd_yazma++;
    lcd_gecmisi.push_back(satir_metni(0) + "|" + satir_metni(1));
  }
  void bip(bool basarili) override { (basarili ? bip_tamam : bip_hata)++; }
  void role(bool ac) override {
    role_durum = ac;
    role_gecmisi.push_back({t, ac});
  }
  void para_led(bool yak) override { led = yak; }
  int seri_oku() override {
    if (rx_hw.empty()) return -1;
    char c = rx_hw.front();
    rx_hw.pop_front();
    return (unsigned char)c;
  }
  int seri_yazilabilir() override { return 63 - (int)tx_hw.size(); }
  void seri_yaz(const char *veri, size_t n) override {
    for (size_t i = 0; i < n; i++) tx_hw.push_back(veri[i]);
  }
  uint16_t rastgele() override {
    rastgele_durum = rastgele_durum * 1664525u + 1013904223u;
    return (uint16_t)(rastgele_durum >> 8);
  }
  void bekci_besle() override { bekci_besleme++; }
  bool bekci_resetiydi() override { return bekci_reset; }
  uint16_t kalici_oku(uint8_t yuva) override { return eeprom[yuva & 15]; }
  void kalici_yaz(uint8_t yuva, uint16_t deger) override {
    if (eeprom[yuva & 15] != deger) eeprom_yazma++;
    eeprom[yuva & 15] = deger;
  }

  std::string satir_metni(int i) const {
    std::string s(lcd[i]);
    while (!s.empty() && s.back() == ' ') s.pop_back();
    return s;
  }
};

