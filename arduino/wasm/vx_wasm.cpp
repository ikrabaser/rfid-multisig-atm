// VAULTIX firmware laboratuvari: GERCEK terminal cekirdegini (arduino/atm_terminal) sahte donanimla calistirir ve
// tarayiciya (WebAssembly) ya da Node'a sade bir C API olarak sunar. Kopru tarafi burada YOK: terminalin seri hatta yazdigi
// satirlar olay olarak cikar, kopru cevaplari vx_seri_ver ile geri verilir (tarayici bunu sunucudaki gercek koprü koduna yollar).
//
// Derleme: arduino/wasm/derle.sh (Emscripten).  Ana makinede de derlenir (-DVX_ANA_MAKINE) ve test edilir.
//
// Olaylar (vx_cikti ile satir satir okunur, okununca temizlenir):
//   <ms> TX <satir>      terminalin hatta yazdigi satir (gurultu uygulanmis hali)
//   <ms> RX <satir>      terminale ulasan satir (gurultu uygulanmis hali)
//   <ms> LCD <s0>|<s1>   <ms> ROLE 0/1   <ms> LED 0/1   <ms> BIP 0/1 (1: basarili, 0: hata)   <ms> DURUM <n>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <memory>
#include <random>
#include <string>

#include "../atm_terminal/terminal.h"
#include "../test/sahte_donanim.h"

#if defined(__EMSCRIPTEN__)
#include <emscripten.h>
#define VX_API extern "C" EMSCRIPTEN_KEEPALIVE
#else
#define VX_API extern "C"
#endif

namespace {

struct Durum {
  SahteDonanim hw;
  std::unique_ptr<vx::Terminal> term;
  std::mt19937 rng{1};
  double g_bit = 0, g_dusur = 0, g_ekle = 0;
  std::deque<char> asagi;        // kopru -> terminal akisi (1 bayt/ms)
  std::string tx_satir, rx_satir;
  std::string cikti, istatistik_metni;
  int son_lcd = -1, son_bip_t = 0, son_bip_h = 0, son_durum = -1;
  bool son_role = false, son_led = false;
};

Durum g;

bool olasilik(double p) { return p > 0 && std::uniform_real_distribution<double>(0, 1)(g.rng) < p; }

std::string hat(char c) {
  std::string o;
  if (olasilik(g.g_ekle)) o += (char)(g.rng() & 0x7F);
  if (olasilik(g.g_dusur)) return o;
  if (olasilik(g.g_bit)) c = (char)(c ^ (1 << (g.rng() % 7)));
  return o + c;
}

void yaz(const char *tur, const std::string &deger) {
  char bas[32];
  std::snprintf(bas, sizeof bas, "%u %s ", g.hw.t, tur);
  g.cikti += bas;
  g.cikti += deger;
  g.cikti += '\n';
}

void olaylar() {
  auto &hw = g.hw;
  if (hw.lcd_yazma != g.son_lcd) {
    g.son_lcd = hw.lcd_yazma;
    yaz("LCD", hw.satir_metni(0) + "|" + hw.satir_metni(1));
  }
  if (hw.role_durum != g.son_role) { g.son_role = hw.role_durum; yaz("ROLE", g.son_role ? "1" : "0"); }
  if (hw.led != g.son_led) { g.son_led = hw.led; yaz("LED", g.son_led ? "1" : "0"); }
  while (g.son_bip_t < hw.bip_tamam) { g.son_bip_t++; yaz("BIP", "1"); }
  while (g.son_bip_h < hw.bip_hata) { g.son_bip_h++; yaz("BIP", "0"); }
  if ((int)g.term->durum() != g.son_durum) { g.son_durum = (int)g.term->durum(); yaz("DURUM", std::to_string(g.son_durum)); }
}

void adim() {
  auto &hw = g.hw;
  hw.t++;
  if (!hw.tx_hw.empty()) {
    char c = hw.tx_hw.front();
    hw.tx_hw.pop_front();
    for (char x : hat(c)) {
      if (x == '\n') { yaz("TX", g.tx_satir); g.tx_satir.clear(); }
      else if (x != '\r') { g.tx_satir += x; if (g.tx_satir.size() > 1000) g.tx_satir.clear(); }
    }
  }
  if (!g.asagi.empty()) {
    char c = g.asagi.front();
    g.asagi.pop_front();
    for (char x : hat(c)) {
      if (hw.rx_hw.size() >= 63) hw.rx_tasma++;
      else hw.rx_hw.push_back(x);
      if (x == '\n') { yaz("RX", g.rx_satir); g.rx_satir.clear(); }
      else if (x != '\r') { g.rx_satir += x; if (g.rx_satir.size() > 1000) g.rx_satir.clear(); }
    }
  }
  g.term->tick();
  olaylar();
}

}  // namespace

// Kalici bellegi (EEPROM benzetimi) tarayici/Node kaydetsin diye: vx_baslat'tan ONCE vx_eeprom_ver ile yuklenir.
VX_API unsigned vx_eeprom_al(int yuva) { return g.hw.eeprom[yuva & 15]; }
VX_API void vx_eeprom_ver(int yuva, unsigned deger) { g.hw.eeprom[yuva & 15] = (uint16_t)deger; }

// Terminali (yeniden) baslatir: kalici bellek korunur, geri kalan her sey sifirlanir (guc kesilip verilmis gibi).
VX_API void vx_baslat(unsigned tohum, unsigned baslangic_ms) {
  uint16_t ep[16];
  std::memcpy(ep, g.hw.eeprom, sizeof ep);
  g.term.reset();
  g.hw = SahteDonanim();
  std::memcpy(g.hw.eeprom, ep, sizeof ep);
  g.hw.t = baslangic_ms;
  g.rng.seed(tohum);
  g.asagi.clear();
  g.tx_satir.clear();
  g.rx_satir.clear();
  g.cikti.clear();
  g.son_lcd = g.son_bip_t = g.son_bip_h = g.son_durum = -1;
  g.son_bip_t = g.son_bip_h = 0;
  g.son_role = g.son_led = false;
  g.term.reset(new vx::Terminal(g.hw));
  g.term->baslat();
  olaylar();
}

VX_API void vx_gurultu(double bit, double dusur, double ekle) { g.g_bit = bit; g.g_dusur = dusur; g.g_ekle = ekle; }
VX_API void vx_adim(int ms) { for (int i = 0; i < ms; i++) adim(); }
VX_API unsigned vx_zaman() { return g.hw.t; }

// Tusa 80 ms basar (okunup terminale teslim edilene kadar ms ilerletmek cagiranin isidir).
VX_API void vx_tus(int c) { g.hw.tuslar.push_back({(char)c, g.hw.t, g.hw.t + 80, false}); }

// Karti okuyucunun onune koyar; terminal okuyunca alan bosalir. Bos metin: karti geri cek.
VX_API void vx_kart(const char *uid_hex) {
  g.hw.bekleyen_kart = uid_hex ? uid_hex : "";
  g.hw.kart_teslim_edildi = false;
}

// Koprunun terminale yazdigi bir satir (cerceveli); sonuna satir sonu eklenir, 1 bayt/ms akar.
VX_API void vx_seri_ver(const char *satir) {
  for (const char *p = satir; *p; p++) g.asagi.push_back(*p);
  g.asagi.push_back('\n');
}

// Birikmis olaylar (okununca temizlenir). Isaretci bir sonraki cagriya kadar gecerlidir.
VX_API const char *vx_cikti() {
  static std::string cikis;
  cikis.swap(g.cikti);
  g.cikti.clear();
  if (g.hw.kart_teslim_edildi) g.hw.bekleyen_kart.clear();
  return cikis.c_str();
}

VX_API const char *vx_istatistik() {
  const auto &s = g.term->istatistik();
  char b[320];
  std::snprintf(b, sizeof b,
                "gonderilen=%u yeniden=%u nak=%u bozuk=%u yoksayilan=%u zaman_asimi=%u ihlal=%u rx_tasma=%d acilis=%u eeprom_yazma=%d bekci=%d",
                s.gonderilen, s.yeniden, s.nak, s.bozuk, s.yoksayilan, s.zaman_asimi, g.term->degismez_ihlali(), g.hw.rx_tasma,
                (unsigned)g.term->acilis_no(), g.hw.eeprom_yazma, g.hw.bekci_besleme);
  g.istatistik_metni = b;
  return g.istatistik_metni.c_str();
}

VX_API int vx_role() { return g.hw.role_durum ? 1 : 0; }
