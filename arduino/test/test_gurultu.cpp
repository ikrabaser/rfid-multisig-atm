// Gurultulu hat, kayip/bozuk cerceveler, gec gelen cevaplar, bellek guvenligi ve kasa kilidi degismezi.
#include <set>

#include "dunya.h"

using namespace vx;

// Para islemi: gurultulu hatta bile EN FAZLA bir kez uygulanir (ve hat makulse tamamlanir)
static void gurultulu_cekme(double bit, double dusur, double ekle, uint32_t tohum, bool *tamam) {
  Dunya d(1000, tohum);
  d.yukari = {bit, dusur, ekle};
  d.asagi = {bit, dusur, ekle};
  d.kos_kadar([&] { return d.term.durum() == D_BOSTA; }, 3000);
  d.kos(400);
  d.hw.bekleyen_kart = "a0a0a001";
  d.hw.kart_teslim_edildi = false;
  d.kos_kadar([&] { return d.hw.kart_teslim_edildi; }, 4000);
  d.hw.bekleyen_kart.clear();
  *tamam = false;
  if (!d.durum_bekle(D_PIN_AL, 20000)) return;
  d.yaz("1234");
  if (!d.durum_bekle(D_MENU, 40000)) return;
  d.tikla('B');
  d.yaz("250");
  d.tikla('#');
  if (!d.durum_bekle(D_PARA_BEKLE, 60000)) {
    KONTROL(d.sunucu.islem_uygulandi <= 1);   // 3 tur da tukendiyse bile en fazla bir kez cekilmis olmali
    return;
  }
  d.tikla('A');
  d.kos(8000);
  *tamam = true;
  KONTROL(d.sunucu.islem_uygulandi == 1);
  for (auto &p : d.sunucu.uygulama_basina) KONTROL(p.second <= 1);
  KONTROL(d.term.degismez_ihlali() == 0);
}

// ~%10 cerceve bozulmasi (bayt basina 0.1% bit hatasi): gercek USB seri hat bundan cok daha temiz; kotu kablo/parazit senaryosu.
TEST(gurultulu_hat_cerceveleri_bozulur_islem_yine_de_tamamlanir_ve_tek_kez_uygulanir) {
  int tamam_sayisi = 0;
  for (uint32_t tohum = 1; tohum <= 40; tohum++) {
    bool tamam;
    gurultulu_cekme(0.001, 0.0005, 0.0005, tohum, &tamam);
    tamam_sayisi += tamam;
  }
  std::printf("      (40 denemede %d tamamlandi)\n", tamam_sayisi);
  KONTROL(tamam_sayisi >= 36);
}

// ~%33-%75 cerceve bozulmasi: islem tamamlanmayabilir, ama para ASLA iki kez cekilmez.
TEST(gurultulu_hat_agir_bozulma_en_fazla_bir_kez_uygulanir) {
  for (uint32_t tohum = 100; tohum < 130; tohum++) {
    bool tamam;
    gurultulu_cekme(tohum % 2 ? 0.004 : 0.02, 0.002, 0.002, tohum, &tamam);
  }
}

TEST(cevap_kaybolunca_ayni_cerceve_yeniden_gider_sunucu_tek_kez_calisir) {
  Dunya d;
  d.ac();
  d.musteri_girisi();
  d.tikla('B');
  d.yaz("100");
  int cagri_once = d.sunucu_cagri_sayisi;
  d.tikla('#');
  d.kos_kadar([&] { return d.sunucu_cagri_sayisi > cagri_once; }, 3000);
  d.donus.clear();                       // cevap yolda kayboldu
  d.asagi_akis.clear();
  KONTROL(d.durum_bekle(D_PARA_BEKLE, 9000));
  KONTROL(d.sunucu.islem_uygulandi == 1);
  KONTROL(d.kopru_tekrar >= 1);          // ikinci cerceve onbellekten cevaplandi
  KONTROL(d.term.istatistik().yeniden >= 1);
}

TEST(gec_gelen_eski_cevap_siradaki_istegin_cevabi_sanilmaz) {
  Dunya d;
  d.ac();
  d.kopru_acik = false;
  d.kart_okut("c0c0c001");
  d.kos(3000);
  d.terminale_cerceve_yolla(ref_kodla(0x55, "TIP|MUSTERI"));   // yanlis sira numarali gec cevap
  d.kos(200);
  KONTROL(d.term.durum() == D_KART_BEKLE);
  KONTROL(d.term.istatistik().yoksayilan >= 1);
}

TEST(timeout_sonrasi_gelen_cevap_yeni_istege_karismaz) {
  Dunya d;
  d.ac();
  d.musteri_girisi();
  d.kopru_acik = false;
  d.tikla('A');
  KONTROL(d.ekran_bekle("Sunucu yanitsiz", 9000));
  d.durum_bekle(D_MENU, 4000);
  d.kopru_acik = true;
  d.tikla('A');
  KONTROL(d.ekran_bekle("Bakiye:", 5000));
  KONTROL_ESIT(d.l1(), "5000.00 TL");
}

TEST(sunucu_asiri_uzun_cevap_koprude_kesilir_terminal_calisir) {
  Dunya d;
  d.ac();
  d.sunucu.ozel = [](const std::string &) { return "HATA|" + std::string(300, 'x'); };
  d.kart_okut("a0a0a001");
  KONTROL(d.ekran_bekle("Yanit gecersiz", 4000));
  KONTROL(d.bosta_bekle(5000));
}

TEST(terminale_dogrudan_tasan_satir_atilir_ve_toparlanir) {
  Dunya d;
  d.ac();
  d.terminale_cerceve_yolla(std::string(400, 'Z'));
  d.kos(1200);
  KONTROL(d.term.durum() == D_BOSTA);
  d.kart_okut("a0a0a001");
  KONTROL(d.durum_bekle(D_PIN_AL, 5000));
}

TEST(jeton_tampona_sigmazsa_oturum_acilmaz) {
  Dunya d;
  d.sunucu.token = std::string(100, 'T');
  d.ac();
  d.kart_okut("a0a0a001");
  d.durum_bekle(D_PIN_AL, 4000);
  d.yaz("1234");
  KONTROL(d.ekran_bekle("Oturum acilamadi", 6000));
  KONTROL(d.bosta_bekle(4000));
  KONTROL(d.term.hassas_bos());
  KONTROL(!d.term.oturum_acik());
}

TEST(en_uzun_gecerli_istek_cerceveye_sigar) {
  Dunya d;
  d.sunucu.token = "123456789:1vXyZa:" + std::string(43, 'k');   // 60 karakter: gercekte olabilecek en uzun jeton
  d.ac();
  d.musteri_girisi();
  d.tikla('D');
  d.yaz("123456");
  d.tikla('#');
  d.yaz("1234567C99");                    // 10 karakter; 10000 ustu oldugu icin sunucu onay ister (BEKLE) - yine de yuk sigmali
  d.tikla('#');
  d.kos_kadar([&] { return d.sunucu.talep_var; }, 5000);
  KONTROL(d.sunucu.talep_var);
  size_t en_uzun = 0;
  for (auto &y : d.kopruye_giden) if (y.size() > en_uzun) en_uzun = y.size();
  KONTROL(en_uzun <= (size_t)YUK_EN_COK && en_uzun > 90);
}

// ---------------------------------------------------------------- degismezler ve fuzz

static bool role_acildi(const Dunya &d) {
  for (auto &g : d.hw.role_gecmisi) if (g.second) return true;
  return false;
}

// --- fuzz yardimcilari: duruma duyarli tus/kart secimi ve komuta uygun ama BOZUK icerikli sunucu cevaplari ---

static std::string rastgele_metin(std::mt19937 &rng, size_t en_cok) {
  static const char *parca[] = {"0", "1", "99", "x", "-", "..", "TL", "UYGULANDI", "KASA_ACMA", " ", "abc", "5000000000", ""};
  std::string s;
  size_t n = rng() % 5;
  for (size_t i = 0; i < n && s.size() < en_cok; i++) s += parca[rng() % (sizeof parca / sizeof parca[0])];
  return s.substr(0, en_cok);
}

// KASA_ACMA + UYGULANDI HICBIR cevapta yok: bu kombinasyon roleyi acabilen tek sey, fuzz bunu uretmez.
static std::string fuzz_cevap(std::mt19937 &rng, const std::string &yuk) {
  std::string ad = yuk.substr(0, yuk.find('|'));
  std::string r1 = rastgele_metin(rng, 20), r2 = rastgele_metin(rng, 20);
  if (rng() % 6 == 0) return std::string("HATA|") + std::string(rng() % 50, (char)('a' + rng() % 26));
  if (rng() % 12 == 0) return rastgele_metin(rng, 60);
  if (ad == "KART") {
    const char *t[] = {"MUSTERI", "PERSONEL", "BLOKE", "IMZASIZ", "BILINMIYOR", ""};
    return std::string("TIP|") + t[rng() % 6];
  }
  if (ad == "PIN") return rng() % 2 ? std::string("GIRIS|") + std::string(rng() % 90, 'T') + "|Ad" : "GIRIS|tok|" + r1;
  if (ad == "BAKIYE") return "BAKIYE|" + r1;
  if (ad == "PINDEGIS") return rng() % 2 ? "PINDEGIS|TAMAM" : "PINDEGIS";
  if (ad == "ISLEM") {
    const char *t[] = {"TAMAM|12.50|501|200x1", "TAMAM|1|", "TAMAM", "BEKLE|9|2", "BEKLE||"};
    return t[rng() % 5];
  }
  if (ad == "TESLIM") return rng() % 2 ? "TESLIM|ALINDI|" : "TESLIM|GERI|" + r1;
  if (ad == "BEKLEYEN") return rng() % 2 ? "BEKLEYEN|YOK" : "BEKLEYEN|5|" + r1 + "|" + r2 + "|0|2";
  if (ad == "KASAAC") return rng() % 2 ? "BEKLE|88|2" : "BEKLE|" + r1;
  if (ad == "ONAY") return "ONAY|" + r1 + "|" + r2 + "|BEKLIYOR";
  if (ad == "DURUM") {
    const char *d[] = {"BEKLIYOR", "BEKLIYOR", "BEKLIYOR", "REDDEDILDI", "SURESI_DOLDU", "BASARISIZ", "UYGULANDI", "?"};
    const char *tur[] = {"CEKME", "HAVALE", "YATIRMA", "", "KASA"};   // KASA_ACMA yok
    return std::string("DURUM|") + r1 + "|" + r2 + "|" + d[rng() % 8] + "|" + std::to_string(rng() % 400) + "|" + (rng() % 2 ? "1" : "0") +
           "|" + tur[rng() % 5] + "|" + (rng() % 2 ? "501" : "") + "|" + r1;
  }
  return "HATA|?";
}

static char fuzz_tusu(std::mt19937 &rng, Durum d) {
  const char *sayi = "0123456789", *genel = "0123456789ABCD*#";
  switch (d) {
    case D_PIN_AL: return rng() % 10 ? sayi[rng() % 10] : "D*"[rng() % 2];
    case D_SAYI_AL: return rng() % 8 ? sayi[rng() % 10] : "CD#*"[rng() % 4];
    case D_MENU: return "ABCD#*ABCD"[rng() % 10];
    case D_KARAR_AL: return "AB*"[rng() % 3];
    case D_TALEP_YOK: return "A*"[rng() % 2];
    case D_PARA_BEKLE: return rng() % 3 ? 0 : 'A';
    default: return rng() % 40 ? 0 : genel[rng() % 16];
  }
}

// Sunucu "UYGULANDI|..|KASA_ACMA" demedikce role ASLA acilmamali; cokme / tasma / bozuk ekran olmamali.
TEST(fuzz_durum_duyarli_girdi_bozuk_cevaplar_role_asla_acilmaz) {
  std::set<int> gorulen;
  for (uint32_t tohum = 1; tohum <= 80; tohum++) {
    Dunya d(1000 + tohum * 977, tohum);
    d.yukari = Gurultu(0.002, 0.0005, 0.0005);
    d.asagi = Gurultu(0.002, 0.0005, 0.0005);
    std::mt19937 rng(tohum * 31 + 7);
    d.sunucu.ozel = [&](const std::string &y) { return fuzz_cevap(rng, y); };
    const char *musteri[] = {"a0a0a001"};
    const char *personel[] = {"b0b0b001", "b0b0b002"};
    const char *diger[] = {"c0c0c001", "ffffffffffffffffffff", "00"};
    uint32_t sonraki_kart = 0;
    for (int i = 0; i < 90000; i++) {
      Durum cur = d.term.durum();
      char k = fuzz_tusu(rng, cur);
      if (k && rng() % 25 == 0) d.hw.tuslar.push_back({k, d.hw.t, d.hw.t + 60, false});
      if (d.hw.t >= sonraki_kart && (cur == D_BOSTA || cur == D_ONAY_IZLE || cur == D_ONAY_DURUM_BEKLE) && rng() % 500 == 0) {
        d.hw.bekleyen_kart = cur == D_BOSTA ? (rng() % 3 ? musteri[0] : (rng() % 2 ? personel[rng() % 2] : diger[rng() % 3]))
                                            : (rng() % 3 ? personel[rng() % 2] : musteri[0]);
        d.hw.kart_teslim_edildi = false;
        sonraki_kart = d.hw.t + 300;
      }
      if (rng() % 9000 == 0) d.kopru_acik = !d.kopru_acik;
      d.adim();
      gorulen.insert(d.term.durum());
      for (int s = 0; s < 2; s++) {          // LCD her zaman yazdirilabilir ASCII, 16 karakter
        KONTROL(std::strlen(d.hw.lcd[s]) == 16 || d.hw.lcd[s][0] == 0);
        for (const char *p = d.hw.lcd[s]; *p; p++)
          if ((unsigned char)*p < 0x20 || (unsigned char)*p > 0x7E) { KONTROL(false); break; }
      }
    }
    KONTROL(!role_acildi(d));
    KONTROL(d.term.degismez_ihlali() == 0);
  }
  std::printf("      (fuzz %d/%d durum gezdi; gezilmeyen:", (int)gorulen.size(), (int)D_KILIT + 1);
  for (int k = 0; k <= (int)D_KILIT; k++) if (!gorulen.count(k)) std::printf(" %d", k);
  std::printf(")\n");
  KONTROL(gorulen.size() >= 20);
}

// Sunucu GERCEKTEN onay verebiliyorken (ve hat gurultuluyken) role ancak sunucunun KASA_ACMA UYGULANDI cevabindan SONRA
// acilir ve her acilista tam 5 sn acik kalir.
TEST(role_yalniz_sunucu_kasa_acma_uygulandi_dediginde_acilir) {
  for (uint32_t tohum = 1; tohum <= 12; tohum++) {
    Dunya d(1000, tohum);
    d.yukari = {0.002, 0.001, 0.001};
    d.asagi = {0.002, 0.001, 0.001};
    d.ac();
    d.sunucu.talep_var = true;
    d.sunucu.talep_tur = "KASA_ACMA";
    std::mt19937 rng(tohum);
    const char *kartlar[] = {"b0b0b001", "b0b0b002", "a0a0a001"};
    for (int i = 0; i < 50000; i++) {
      if (rng() % 80 == 0) d.hw.tuslar.push_back({"0123456789ABCD*#"[rng() % 16], d.hw.t, d.hw.t + 60, false});
      if (rng() % 400 == 0) {
        d.hw.bekleyen_kart = kartlar[rng() % 3];
        d.hw.kart_teslim_edildi = false;
      }
      d.adim();
      if (d.hw.role_durum) KONTROL(d.sunucu.kasa_acma_uygulandi_cevabi >= 1);
    }
    for (size_t k = 0; k + 1 < d.hw.role_gecmisi.size(); k += 2) {
      if (d.hw.role_gecmisi[k].second && !d.hw.role_gecmisi[k + 1].second) {
        uint32_t sure = d.hw.role_gecmisi[k + 1].first - d.hw.role_gecmisi[k].first;
        KONTROL(sure >= 5000 && sure <= 5010);
      }
    }
    KONTROL(d.term.degismez_ihlali() == 0);
  }
}

TEST(bostayken_hassas_veri_bellekte_kalmaz) {
  for (uint32_t tohum = 1; tohum <= 15; tohum++) {
    Dunya d(1000, tohum);
    d.yukari = {0.001, 0, 0};
    d.asagi = {0.001, 0, 0};
    std::mt19937 rng(tohum + 99);
    for (int i = 0; i < 40000; i++) {
      if (rng() % 70 == 0) d.hw.tuslar.push_back({"0123456789ABCD*#"[rng() % 16], d.hw.t, d.hw.t + 60, false});
      if (rng() % 500 == 0) {
        const char *k[] = {"a0a0a001", "b0b0b001"};
        d.hw.bekleyen_kart = k[rng() % 2];
        d.hw.kart_teslim_edildi = false;
      }
      d.adim();
      if (d.term.durum() == D_BOSTA && d.l0() == "Karti okutun" && d.hw.bekleyen_kart.empty() && !d.term.oturum_acik())
        KONTROL(d.term.hassas_bos());
    }
  }
}
