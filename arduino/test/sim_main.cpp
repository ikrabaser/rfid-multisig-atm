// Firmware simulatoru: GERCEK terminal cekirdegini sahte donanimla calistirir; disaridan (Python) stdin/stdout ile surulur.
// Kopru tarafi burada YOK: terminalin seri hatta yazdigi satirlar "TX ..." olarak cikar, "S ..." ile terminale seri veri girilir.
// Boylece gercek Python kopru (bridge/serial_bridge.py) ve gercek Django sunucusuyla uctan uca test edilir.
//
//   Girdi (satir satir):  T <ms>   zamani ilerlet (her ms'de tick)      K <tus>   tusa 80 ms bas
//                         C <uid>  karti okut (terminal okuyana kadar)  S <satir> terminale seri satir (kopruden)
//                         Q        cik
//   Cikti:                <ms> TX <satir> | LCD <s0>|<s1> | ROLE 0/1 | LED 0/1 | BIP 0/1 | DURUM <n> | ... ; T'den sonra "OK <ms>"
// Secenekler:  --gurultu <bit> [<dusur> <ekle>]  hat gurultusu (bayt basina olasilik, iki yonde)   --tohum <n>
//              --acilis <n>  kalici acilis sayaci n olsun (gercek cihazda EEPROM'da kalir)   --baslangic <ms>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <iostream>
#include <random>
#include <sstream>
#include <string>

#include "../atm_terminal/terminal.h"
#include "sahte_donanim.h"

using namespace vx;

int main(int argc, char **argv) {
  double bit = 0, dusur = 0, ekle = 0;
  uint32_t tohum = 1, baslangic = 1000, acilis = 1;
  for (int i = 1; i < argc; i++) {
    if (!std::strcmp(argv[i], "--gurultu") && i + 1 < argc) {
      bit = std::atof(argv[++i]);
      if (i + 2 < argc && argv[i + 1][0] != '-') {
        dusur = std::atof(argv[++i]);
        ekle = std::atof(argv[++i]);
      }
    } else if (!std::strcmp(argv[i], "--tohum") && i + 1 < argc) {
      tohum = (uint32_t)std::atol(argv[++i]);
    } else if (!std::strcmp(argv[i], "--acilis") && i + 1 < argc) {
      acilis = (uint32_t)std::atol(argv[++i]);   // onceki acilis sayisi gibi davranir (kalici bellegi onceden doldurur)
    } else if (!std::strcmp(argv[i], "--baslangic") && i + 1 < argc) {
      baslangic = (uint32_t)std::strtoul(argv[++i], nullptr, 10);
    }
  }
  std::mt19937 rng(tohum);
  auto olasilik = [&](double p) { return p > 0 && std::uniform_real_distribution<double>(0, 1)(rng) < p; };
  auto hat = [&](char c) {
    std::string o;
    if (olasilik(ekle)) o += (char)(rng() & 0x7F);
    if (olasilik(dusur)) return o;
    if (olasilik(bit)) c = (char)(c ^ (1 << (rng() % 7)));
    return o + c;
  };

  SahteDonanim hw;
  hw.t = baslangic;
  if (acilis > 1) hw.eeprom[(acilis - 1) % 16] = (uint16_t)(acilis - 1);   // sayac acilista `acilis` olur
  Terminal term(hw);
  term.baslat();

  std::deque<char> asagi;      // kopru -> terminal akisi (1 bayt/ms)
  std::string satir;           // terminal -> kopru satir birlestirme
  int son_lcd = -1, son_bip_t = 0, son_bip_h = 0;
  bool son_role = false, son_led = false;
  int son_durum = -1;
  std::setvbuf(stdout, nullptr, _IOLBF, 0);

  auto olaylar = [&]() {
    if (hw.lcd_yazma != son_lcd) {
      son_lcd = hw.lcd_yazma;
      std::printf("%u LCD %s|%s\n", hw.t, hw.satir_metni(0).c_str(), hw.satir_metni(1).c_str());
    }
    if (hw.role_durum != son_role) {
      son_role = hw.role_durum;
      std::printf("%u ROLE %d\n", hw.t, son_role ? 1 : 0);
    }
    if (hw.led != son_led) {
      son_led = hw.led;
      std::printf("%u LED %d\n", hw.t, son_led ? 1 : 0);
    }
    while (son_bip_t < hw.bip_tamam) { son_bip_t++; std::printf("%u BIP 1\n", hw.t); }
    while (son_bip_h < hw.bip_hata) { son_bip_h++; std::printf("%u BIP 0\n", hw.t); }
    if ((int)term.durum() != son_durum) {
      son_durum = (int)term.durum();
      std::printf("%u DURUM %d\n", hw.t, son_durum);
    }
  };

  auto adim = [&]() {
    hw.t++;
    if (!hw.tx_hw.empty()) {
      char c = hw.tx_hw.front();
      hw.tx_hw.pop_front();
      for (char g : hat(c)) {
        if (g == '\n') {
          std::printf("%u TX %s\n", hw.t, satir.c_str());
          satir.clear();
        } else if (g != '\r') {
          satir += g;
          if (satir.size() > 1000) satir.clear();
        }
      }
    }
    if (!asagi.empty()) {
      char c = asagi.front();
      asagi.pop_front();
      for (char g : hat(c)) {
        if (hw.rx_hw.size() >= 63) hw.rx_tasma++;
        else hw.rx_hw.push_back(g);
      }
    }
    term.tick();
    olaylar();
  };

  olaylar();
  std::string girdi;
  while (std::getline(std::cin, girdi)) {
    if (!girdi.empty() && girdi.back() == '\r') girdi.pop_back();
    if (girdi.empty()) continue;
    char k = girdi[0];
    std::string arg = girdi.size() > 2 ? girdi.substr(2) : "";
    if (k == 'T') {
      long n = std::atol(arg.c_str());
      for (long i = 0; i < n; i++) adim();
      std::printf("%u OK\n", hw.t);
    } else if (k == 'K') {
      if (!arg.empty()) hw.tuslar.push_back({arg[0], hw.t, hw.t + 80, false});
      std::printf("%u OK\n", hw.t);
    } else if (k == 'C') {
      hw.bekleyen_kart = arg;
      hw.kart_teslim_edildi = false;
      std::printf("%u OK\n", hw.t);
    } else if (k == 'S') {
      for (char c : arg) asagi.push_back(c);
      asagi.push_back('\n');
      std::printf("%u OK\n", hw.t);
    } else if (k == 'I') {   // istatistik
      const auto &s = term.istatistik();
      std::printf("%u ISTATISTIK gonderilen=%u yeniden=%u nak=%u bozuk=%u yoksayilan=%u zaman_asimi=%u ihlal=%u rx_tasma=%d\n", hw.t, s.gonderilen,
                  s.yeniden, s.nak, s.bozuk, s.yoksayilan, s.zaman_asimi, term.degismez_ihlali(), hw.rx_tasma);
      std::printf("%u OK\n", hw.t);
    } else if (k == 'Q') {
      break;
    }
    if (hw.bekleyen_kart.size() && hw.kart_teslim_edildi) hw.bekleyen_kart.clear();
  }
  return 0;
}
