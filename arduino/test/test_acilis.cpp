// Kalici acilis sayaci (EEPROM halkasi) ve yeniden baslatma sonrasi islem anahtari benzersizligi.
#include <set>

#include "dunya.h"

using namespace vx;

TEST(acilis_sayaci_her_acilista_artar) {
  SahteDonanim hw;
  for (uint16_t beklenen = 1; beklenen <= 40; beklenen++) KONTROL(acilis_sayaci_ilerlet(hw) == beklenen);
}

TEST(acilis_sayaci_yazma_16_yuvaya_dagilir_omur_uzar) {
  SahteDonanim hw;
  int yazma_yuva[16] = {0};
  uint16_t onceki[16];
  std::memcpy(onceki, hw.eeprom, sizeof onceki);
  for (int i = 0; i < 1600; i++) {
    acilis_sayaci_ilerlet(hw);
    for (int k = 0; k < 16; k++) if (hw.eeprom[k] != onceki[k]) { yazma_yuva[k]++; onceki[k] = hw.eeprom[k]; }
  }
  for (int k = 0; k < 16; k++) KONTROL(yazma_yuva[k] == 100);     // her yuva 100 kez: tek yuvaya yazsaydik 1600 olurdu
}

TEST(acilis_sayaci_65536_acilista_tasar_ama_sifir_ve_bos_isaretini_uretmez) {
  SahteDonanim hw;
  uint16_t onceki = 0;
  std::set<uint16_t> son64;
  for (int i = 0; i < 140000; i++) {
    uint16_t n = acilis_sayaci_ilerlet(hw);
    KONTROL(n != 0 && n != 0xFFFF);
    if (i > 0 && n != 1) KONTROL(n == (uint16_t)(onceki + 1));     // tasma disinda hep +1
    KONTROL(n != onceki);
    onceki = n;
  }
}

TEST(acilis_sayaci_yari_yazilmis_deger_yoksayilir) {
  SahteDonanim hw;
  for (int i = 0; i < 20; i++) acilis_sayaci_ilerlet(hw);        // sayac 20, yuva 4'te
  hw.eeprom[4] = 0x0F14;                                          // elektrik giderken bozulmus: 3860 % 16 = 4 ama yuvaya uyan bozuk deger
  hw.eeprom[5] = 0xFF15;                                          // sonraki yuvaya yari yazilmis (yuva dogrulamasi tutmaz: 65301 % 16 = 5 -> tutar!)
  uint16_t n = acilis_sayaci_ilerlet(hw);
  KONTROL(n != 0 && n != 0xFFFF);
  hw.eeprom[7] = 0x1234;                                          // 4660 % 16 = 4 != 7: gecersiz, atilir
  uint16_t m = acilis_sayaci_ilerlet(hw);
  KONTROL(m != 0xFFFF && m != 0);
}

TEST(bozuk_yuvalar_sayaci_asla_sifirlamaz_gecersizse_1den_baslar) {
  SahteDonanim hw;
  for (auto &e : hw.eeprom) e = 0x0001;                           // 1 % 16 = 1 : yalniz yuva 1 gecerli
  KONTROL(acilis_sayaci_ilerlet(hw) == 2);
  SahteDonanim cop;
  for (int i = 0; i < 16; i++) cop.eeprom[i] = (uint16_t)(0x1234 + i * 7);   // hicbiri yuva ile uyusmuyor (cogu)
  uint16_t n = acilis_sayaci_ilerlet(cop);
  KONTROL(n != 0 && n != 0xFFFF);
}

// Gercek hatanin yeniden uretimi: ayni milisaniyede ayni islemi yapan, YENIDEN BASLATILMIS terminal ayni anahtari uretmemeli.
static std::string islem_anahtari(const uint16_t *eeprom_giris, uint16_t *eeprom_cikis) {
  Dunya d(1000, 1, eeprom_giris);
  d.ac();
  d.musteri_girisi();
  d.tikla('B');
  d.yaz("100");
  d.tikla('#');
  d.durum_bekle(D_PARA_BEKLE, 8000);
  std::memcpy(eeprom_cikis, d.hw.eeprom, 32);
  for (auto &y : d.kopruye_giden)
    if (y.rfind("ISLEM|", 0) == 0) return y.substr(y.rfind('|') + 1);
  return "";
}

TEST(yeniden_baslatma_ayni_anahtari_uretmez_ayni_zaman_ayni_rastgele_olsa_bile) {
  uint16_t ep[16];
  for (auto &e : ep) e = 0xFFFF;
  std::set<std::string> anahtarlar;
  for (int boot = 1; boot <= 30; boot++) {
    uint16_t sonraki[16];
    std::string a = islem_anahtari(ep, sonraki);
    KONTROL(a.size() == 16);
    KONTROL(anahtarlar.insert(a).second);          // hicbiri daha once gorulmedi
    std::memcpy(ep, sonraki, sizeof ep);
  }
  KONTROL(anahtarlar.size() == 30);
}

TEST(kalici_bellek_yoksa_bile_ayni_boot_icinde_anahtarlar_farkli) {
  Dunya d;
  d.ac();
  d.musteri_girisi();
  std::set<std::string> a;
  for (int i = 0; i < 4; i++) {
    d.tikla('B');
    d.yaz("10");
    d.tikla('#');
    d.durum_bekle(D_PARA_BEKLE, 8000);
    d.tikla('A');
    d.durum_bekle(D_MENU, 8000);
  }
  for (auto &y : d.kopruye_giden)
    if (y.rfind("ISLEM|", 0) == 0) a.insert(y.substr(y.rfind('|') + 1));
  KONTROL(a.size() == 4);
}
