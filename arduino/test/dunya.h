// Test "dunyasi": sahte donanim + sahte kopru (bridge/cerceve.py'nin C++ modeli) + sahte sunucu + gurultulu seri hat.
// 1 ms cozunurluk; her ms'de terminal.tick() cagrilir. Seri hat 9600 baud gibi ~1 bayt/ms akar, 64 baytlik donanim tamponlari var.
#pragma once
#include <cstdint>
#include <cstring>
#include <deque>
#include <functional>
#include <map>
#include <random>
#include <string>
#include <vector>

#include "../atm_terminal/cerceve.h"
#include "../atm_terminal/terminal.h"
#include "mini_test.h"

#include "sahte_donanim.h"

// ---------------------------------------------------------------------------- referans CRC / cerceve (testin kendi bagimsiz kodu)

inline uint16_t ref_crc(const std::string &s) {
  uint16_t crc = 0xFFFF;
  for (unsigned char b : s) {
    crc ^= (uint16_t)(b << 8);
    for (int i = 0; i < 8; i++) crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
  }
  return crc;
}
inline std::string ref_kodla(int sira, const std::string &yuk, bool nak = false) {
  char bas[8];
  if (nak) std::snprintf(bas, sizeof bas, "--:");
  else std::snprintf(bas, sizeof bas, "%02X:", sira & 0xFF);
  std::string govde = std::string(bas) + yuk;
  char son[16];
  std::snprintf(son, sizeof son, "*%04X", ref_crc(govde));
  return govde + son;
}
// "SS:yuk*CCCC" -> true ise sira/yuk dolar
inline bool ref_coz(const std::string &s, int &sira, std::string &yuk) {
  if (s.size() < 8 || s[2] != ':' || s[s.size() - 5] != '*') return false;
  for (unsigned char c : s) if (c < 0x20 || c > 0x7E) return false;
  std::string govde = s.substr(0, s.size() - 5);
  unsigned gelen = 0;
  if (std::sscanf(s.c_str() + s.size() - 4, "%4x", &gelen) != 1) return false;
  if (gelen != ref_crc(govde)) return false;
  if (s.compare(0, 2, "--") == 0) sira = -1;
  else if (std::sscanf(s.c_str(), "%2x", (unsigned *)&sira) != 1) return false;
  yuk = s.substr(3, s.size() - 8);
  return true;
}

// ---------------------------------------------------------------------------- sahte sunucu (Django'nun yerine, kopru protokolu seviyesinde)

struct SahteSunucu {
  std::string token = "7:1vXyZa:" + std::string(43, 'k');  // gercek TimestampSigner jetonu kadar uzun (~59)
  double bakiye = 5000.0;
  int cagri = 0;                         // toplam yuk isleme sayisi
  std::map<std::string, std::string> idem;  // anahtar -> cevap
  int islem_uygulandi = 0;               // para hareketi gercekten kac kez uygulandi
  std::map<std::string, int> uygulama_basina;  // anahtar -> kac kez uygulandi (hep <= 1 olmali)
  std::string pin = "1234", yetkili_pin = "4321";
  int onay_verilen = 0, onay_gereken = 2;
  std::string talep_tur = "CEKME";       // bekleyen talebin turu
  bool talep_var = false;
  bool durum_uygulandi = false;
  int kasa_acma_uygulandi_cevabi = 0;    // sunucunun "UYGULANDI|...|KASA_ACMA" cevabi sayisi (rolenin tek yetkisi)
  int teslim_alindi = 0, teslim_geri = 0;
  bool sunucu_arizali = false;           // true: "HATA|Sunucu kapali" doner
  std::function<std::string(const std::string &)> ozel;  // fuzz: tum cevaplari ezer

  std::string isle(const std::string &yuk) {
    cagri++;
    if (ozel) return ozel(yuk);
    if (sunucu_arizali) return "HATA|Sunucu kapali";
    std::vector<std::string> a;
    size_t bas = 0;
    for (;;) {
      size_t k = yuk.find('|', bas);
      if (k == std::string::npos) { a.push_back(yuk.substr(bas)); break; }
      a.push_back(yuk.substr(bas, k - bas));
      bas = k + 1;
    }
    auto al = [&](size_t i) { return i < a.size() ? a[i] : std::string(); };
    const std::string &k = a[0];
    if (k == "KART") {
      std::string u = al(1);
      if (u == "a0a0a001") return "TIP|MUSTERI";
      if (u == "b0b0b001" || u == "b0b0b002") return "TIP|PERSONEL";
      if (u == "c0c0c001") return "TIP|BLOKE";
      if (u == "d0d0d001") return "TIP|IMZASIZ";
      return "TIP|BILINMIYOR";
    }
    if (k == "PIN") {
      if (al(1) == "a0a0a001" && al(2) == pin) return "GIRIS|" + token + "|Ayse";
      return "HATA|PIN yanlis";
    }
    if (k == "PINDEGIS") {
      if (al(1) != token) return "HATA|Oturum gecersiz";
      if (al(2) != pin) return "HATA|Eski PIN yanlis";
      pin = al(3);
      return "PINDEGIS|TAMAM";
    }
    if (k == "BAKIYE") {
      if (al(1) != token) return "HATA|Oturum gecersiz";
      char b[32];
      std::snprintf(b, sizeof b, "BAKIYE|%.2f", bakiye);
      return b;
    }
    if (k == "ISLEM") {  // ISLEM|token|tur|tutar|hedef|anahtar
      if (al(1) != token) return "HATA|Oturum gecersiz";
      std::string anahtar = al(5);
      if (!anahtar.empty() && idem.count(anahtar)) return idem[anahtar];
      double tutar = std::atof(al(3).c_str());
      std::string c;
      if (tutar > 10000) {
        talep_var = true;
        talep_tur = al(2) == "C" ? "CEKME" : "HAVALE";
        onay_verilen = 0;
        durum_uygulandi = false;
        c = "BEKLE|77|" + std::to_string(onay_gereken);
      } else if (al(2) == "C" && tutar > bakiye) {
        c = "HATA|Yetersiz bakiye";
      } else {
        if (al(2) == "C") bakiye -= tutar; else if (al(2) == "Y") bakiye += tutar;
        islem_uygulandi++;
        uygulama_basina[anahtar]++;
        char b[64];
        std::snprintf(b, sizeof b, "TAMAM|%.2f|%d|%s", bakiye, 100 + islem_uygulandi, al(2) == "C" ? "200x2" : "");
        c = b;
      }
      if (!anahtar.empty()) idem[anahtar] = c;
      return c;
    }
    if (k == "KASAAC") {
      if ((al(1) == "b0b0b001" || al(1) == "b0b0b002") && al(2) == yetkili_pin) {
        talep_var = true;
        talep_tur = "KASA_ACMA";
        onay_verilen = 0;
        durum_uygulandi = false;
        return "BEKLE|88|" + std::to_string(onay_gereken);
      }
      return "HATA|Yetkili PIN yanlis";
    }
    if (k == "BEKLEYEN") {
      if (!talep_var || durum_uygulandi) return "BEKLEYEN|YOK";
      return std::string("BEKLEYEN|") + (talep_tur == "KASA_ACMA" ? "88" : "77") + "|" +
             (talep_tur == "KASA_ACMA" ? "Kasa" : "Cek") + "|" + (talep_tur == "KASA_ACMA" ? "ac" : "15000") + "|" +
             std::to_string(onay_verilen) + "|" + std::to_string(onay_gereken);
    }
    if (k == "ONAY") {  // ONAY|talep|uid|pin|E/H
      if (!(al(2) == "b0b0b001" || al(2) == "b0b0b002") || al(3) != yetkili_pin) return "HATA|Yetkili PIN yanlis";
      if (al(4) == "E") onay_verilen++;
      if (onay_verilen >= onay_gereken) durum_uygulandi = true;
      return "ONAY|" + std::to_string(onay_verilen) + "|" + std::to_string(onay_gereken) + "|" +
             (durum_uygulandi ? "UYGULANDI" : "BEKLIYOR");
    }
    if (k == "DURUM") {
      std::string tur = talep_tur;
      if (durum_uygulandi) {
        if (tur == "KASA_ACMA") kasa_acma_uygulandi_cevabi++;
        return "DURUM|" + std::to_string(onay_verilen) + "|" + std::to_string(onay_gereken) + "|UYGULANDI|0|0|" + tur +
               "|" + (tur == "CEKME" ? "501" : "0") + "|" + (tur == "CEKME" ? "200x75" : "");
      }
      return "DURUM|" + std::to_string(onay_verilen) + "|" + std::to_string(onay_gereken) + "|BEKLIYOR|250|0|" + tur + "|0|";
    }
    if (k == "TESLIM") {
      if (al(2) == "A") { teslim_alindi++; return "TESLIM|ALINDI|"; }
      teslim_geri++;
      return "TESLIM|GERI|2000";
    }
    return "HATA|Bilinmeyen komut";
  }
};

// ---------------------------------------------------------------------------- gurultulu hat + sahte kopru

struct Gurultu {
  double bit;     // bayt basina bit cevirme olasiligi
  double dusur;   // bayt dusurme
  double ekle;    // rastgele bayt ekleme
  Gurultu(double b = 0, double d = 0, double e = 0) : bit(b), dusur(d), ekle(e) {}
};

class Dunya {
 public:
  SahteDonanim hw;
  vx::Terminal term;
  SahteSunucu sunucu;
  Gurultu yukari, asagi;           // terminal->kopru, kopru->terminal
  uint32_t gecikme_ms = 15;        // kopru + sunucu cevap suresi
  bool kopru_acik = true;          // false: kopru hic cevap vermez (kablo cikik)
  bool surum_uyumsuz = false;      // kopru farkli protokol surumu bildirir
  std::mt19937 rng;

  // kopru modeli (bridge/cerceve.py ile ayni kurallar)
  struct Giris { int sira; std::string yuk, cevap; };
  std::deque<Giris> onbellek;
  std::string satir_tamponu;
  int sunucu_cagri_sayisi = 0, kopru_nak = 0, kopru_tekrar = 0, kopru_bozuk = 0;
  std::vector<std::string> kopruye_giden;  // terminalin yolladigi gecerli yukler (sirayla)
  std::deque<std::pair<uint32_t, std::string>> donus;  // (hazir olma zamani, cerceve)
  std::deque<char> asagi_akis;

  // eeprom: onceki acilistan kalan kalici bellek (yeniden baslatma benzetimi); yoksa bos (ilk acilis)
  explicit Dunya(uint32_t baslangic_ms = 1000, uint32_t tohum = 1, const uint16_t *eeprom = nullptr) : term(hw), rng(tohum) {
    hw.t = baslangic_ms;
    if (eeprom) std::memcpy(hw.eeprom, eeprom, sizeof hw.eeprom);
    term.baslat();
  }

  bool olasilik(double p) { return p > 0 && std::uniform_real_distribution<double>(0, 1)(rng) < p; }

  // gurultulu hattan gecen bir bayt: 0..2 bayt cikar
  std::string hattan_gecir(char c, const Gurultu &g) {
    std::string cikti;
    if (olasilik(g.ekle)) cikti += (char)(rng() & 0x7F);
    if (olasilik(g.dusur)) return cikti;
    if (olasilik(g.bit)) c = (char)(c ^ (1 << (rng() % 7)));
    cikti += c;
    return cikti;
  }

  void kopru_satir(const std::string &satir) {
    int sira;
    std::string yuk;
    if (!ref_coz(satir, sira, yuk)) {
      kopru_bozuk++;
      kopru_nak++;
      donus.push_back({hw.t + 1, ref_kodla(0, "NAK|CRC", true)});
      return;
    }
    if (sira < 0) return;
    kopruye_giden.push_back(yuk);
    std::string ad = yuk.substr(0, yuk.find('|'));
    std::string cevap;
    if (ad == "HELLO") {
      onbellek.clear();
      cevap = (yuk == "HELLO|2" && !surum_uyumsuz) ? "HELLO|OK|2" : "HELLO|UYUMSUZ|2";
    } else if (ad == "PING") {
      cevap = "PONG";
    } else {
      bool bulundu = false;
      for (auto &g : onbellek) {
        if (g.sira == sira && g.yuk == yuk) { cevap = g.cevap; bulundu = true; kopru_tekrar++; }
      }
      if (!bulundu) {
        sunucu_cagri_sayisi++;
        cevap = sunucu.isle(yuk);
        if (cevap.size() > (size_t)vx::YUK_EN_COK) cevap = "HATA|Yanit gecersiz";
        onbellek.push_back({sira, yuk, cevap});
        if (onbellek.size() > 8) onbellek.pop_front();
      }
    }
    donus.push_back({hw.t + gecikme_ms, ref_kodla(sira, cevap)});
  }

  // sunucu yerine terminale dogrudan cerceve basmak icin (sahte/eski/ters cevap testleri)
  void terminale_cerceve_yolla(const std::string &cerceve) {
    for (char c : cerceve) asagi_akis.push_back(c);
    asagi_akis.push_back('\n');
  }

  void adim() {
    hw.t++;
    // terminal -> kopru
    if (!hw.tx_hw.empty()) {
      char c = hw.tx_hw.front();
      hw.tx_hw.pop_front();
      for (char g : hattan_gecir(c, yukari)) {
        if (g == '\n') {
          if (kopru_acik) kopru_satir(satir_tamponu);
          satir_tamponu.clear();
        } else if (g != '\r') {
          satir_tamponu += g;
          if (satir_tamponu.size() > 1000) satir_tamponu.clear();
        }
      }
    }
    // kopru -> hat
    while (!donus.empty() && donus.front().first <= hw.t) {
      for (char c : donus.front().second) asagi_akis.push_back(c);
      asagi_akis.push_back('\n');
      donus.pop_front();
    }
    // hat -> terminal (1 bayt/ms, 63 baytlik donanim tamponu)
    if (!asagi_akis.empty()) {
      char c = asagi_akis.front();
      asagi_akis.pop_front();
      for (char g : hattan_gecir(c, asagi)) {
        if (hw.rx_hw.size() >= 63) hw.rx_tasma++;
        else hw.rx_hw.push_back(g);
      }
    }
    term.tick();
  }

  void kos(uint32_t ms) {
    for (uint32_t i = 0; i < ms; i++) adim();
  }
  bool kos_kadar(const std::function<bool()> &kosul, uint32_t en_cok_ms = 20000) {
    for (uint32_t i = 0; i < en_cok_ms; i++) {
      if (kosul()) return true;
      adim();
    }
    return kosul();
  }

  // --- kullanici eylemleri
  bool tikla(char c, uint32_t en_cok_ms = 3000) {  // tus 80 ms basili kalir; terminal okuyana kadar bekler
    hw.tuslar.push_back({c, hw.t, hw.t + 80, false});
    size_t idx = hw.tuslar.size() - 1;
    bool ok = kos_kadar([&] { return hw.tuslar[idx].teslim; }, en_cok_ms);
    if (!ok) hw.tuslar[idx].bitis = 0;
    kos(30);
    return ok;
  }
  bool yaz(const std::string &s) {
    for (char c : s) if (!tikla(c)) { basarisiz(__FILE__, __LINE__, std::string("tus teslim edilmedi: ") + c); return false; }
    return true;
  }
  bool kart_okut(const std::string &uid, uint32_t en_cok_ms = 4000) {
    hw.bekleyen_kart = uid;
    hw.kart_teslim_edildi = false;
    bool ok = kos_kadar([&] { return hw.kart_teslim_edildi; }, en_cok_ms);
    hw.bekleyen_kart.clear();
    return ok;
  }
  bool durum_bekle(vx::Durum d, uint32_t en_cok_ms = 20000) {
    return kos_kadar([&] { return term.durum() == d; }, en_cok_ms);
  }
  bool ekran_bekle(const std::string &satir0, uint32_t en_cok_ms = 20000) {
    return kos_kadar([&] { return term.lcd(0) == pad(satir0) || hw.satir_metni(0) == satir0; }, en_cok_ms);
  }
  static std::string pad(const std::string &s) { return s; }

  std::string l0() const { return hw.satir_metni(0); }
  std::string l1() const { return hw.satir_metni(1); }
  std::string ekran() const { return l0() + " / " + l1(); }

  // acilis: HELLO tamamlanip bosta ekranina gelene kadar
  void ac() {
    kos_kadar([&] { return term.durum() == vx::D_BOSTA; }, 3000);
    kos(300);
  }
  // musteri girisi: kart + PIN -> menu
  bool musteri_girisi(const std::string &pin = "1234") {
    if (!kart_okut("a0a0a001")) return false;
    if (!durum_bekle(vx::D_PIN_AL, 3000)) return false;
    if (!yaz(pin)) return false;
    return durum_bekle(vx::D_MENU, 8000);
  }
  bool bosta_bekle(uint32_t ms = 30000) { return durum_bekle(vx::D_BOSTA, ms); }
};
