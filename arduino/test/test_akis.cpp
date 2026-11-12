// Uctan uca akislar: sahte donanim + sahte kopru/sunucu uzerinde gercek terminal cekirdegi (gurultusuz hat).
#include "dunya.h"

using namespace vx;

static bool role_hic_acilmadi(const Dunya &d) {
  for (auto &g : d.hw.role_gecmisi) if (g.second) return false;
  return true;
}

TEST(acilis_hello_ve_bosta_ekrani) {
  Dunya d;
  KONTROL_ESIT(d.l0(), "Baslatiliyor...");
  d.kos_kadar([&] { return d.term.durum() == D_BOSTA; }, 3000);
  d.kos(400);
  KONTROL_ESIT(d.l0(), "Karti okutun");
  KONTROL_ESIT(d.l1(), "Mini ATM / Kasa");
  KONTROL(!d.kopruye_giden.empty() && d.kopruye_giden[0] == "HELLO|2");
  KONTROL(d.term.kopru_ok());
  KONTROL(!d.hw.role_durum && !d.hw.led);
  KONTROL(d.hw.bekci_besleme > 100);  // watchdog her turda beslenir
}

TEST(bekci_reseti_acilista_gosterilir) {
  SahteDonanim hw;
  hw.bekci_reset = true;
  Terminal t(hw);
  t.baslat();
  KONTROL_ESIT(hw.satir_metni(1), "Bekci reseti!");
}

TEST(musteri_giris_bakiye_cikis_ve_hassas_veri_silinir) {
  Dunya d;
  d.ac();
  KONTROL(d.musteri_girisi());
  KONTROL(d.term.oturum_acik());
  KONTROL(d.l0().find("A:Bakiye") == 0);
  d.tikla('A');
  KONTROL(d.durum_bekle(D_MESAJ, 3000) || d.l0() == "Bakiye:");
  d.kos(200);
  KONTROL_ESIT(d.l0(), "Bakiye:");
  KONTROL_ESIT(d.l1(), "5000.00 TL");
  KONTROL(d.durum_bekle(D_MENU, 6000));
  d.tikla('*');
  KONTROL(d.bosta_bekle(3000));
  KONTROL(d.term.hassas_bos());   // jeton + UID + PIN RAM'den silindi
  KONTROL(!d.term.oturum_acik());
  KONTROL_ESIT(d.l0(), "Karti okutun");
  KONTROL(role_hic_acilmadi(d));
  KONTROL_ESIT(d.l0(), "Karti okutun");
}

TEST(yanlis_pin_hata_gosterir_ve_bostaya_doner) {
  Dunya d;
  d.ac();
  d.kart_okut("a0a0a001");
  d.durum_bekle(D_PIN_AL, 3000);
  d.yaz("9999");
  KONTROL(d.ekran_bekle("PIN yanlis", 6000));
  KONTROL(d.bosta_bekle(5000));
  KONTROL(d.term.hassas_bos());
  KONTROL(d.hw.bip_hata >= 1);
}

TEST(pin_iptal_ve_zaman_asimi) {
  Dunya d;
  d.ac();
  d.kart_okut("a0a0a001");
  d.durum_bekle(D_PIN_AL, 3000);
  d.yaz("12");
  d.tikla('D');
  KONTROL_ESIT(d.l1().substr(0, 6), "PIN: *");
  d.tikla('*');
  KONTROL_ESIT(d.l0(), "Iptal edildi");
  KONTROL(d.bosta_bekle(3000));
  // zaman asimi: 20 sn tus yok
  d.kart_okut("a0a0a001");
  d.durum_bekle(D_PIN_AL, 3000);
  d.kos(19500);
  KONTROL(d.term.durum() == D_PIN_AL);
  d.kos(1500);
  KONTROL(d.term.durum() != D_PIN_AL);
  KONTROL(d.bosta_bekle(3000));
  KONTROL(d.term.hassas_bos());
}

TEST(para_cekme_para_alindi) {
  Dunya d;
  d.ac();
  d.musteri_girisi();
  d.tikla('B');
  KONTROL_ESIT(d.l0(), "Tutar (C:. #:ok)");
  d.yaz("500");
  d.tikla('C');
  d.yaz("25");
  KONTROL_ESIT(d.l1(), "500.25");
  d.tikla('#');
  KONTROL(d.durum_bekle(D_PARA_BEKLE, 5000));
  KONTROL_ESIT(d.l0(), "Parayi alin");
  KONTROL(d.hw.led);
  d.tikla('A');
  KONTROL(d.ekran_bekle("Islem tamam", 5000));
  KONTROL(!d.hw.led);
  KONTROL(d.sunucu.teslim_alindi == 1 && d.sunucu.teslim_geri == 0);
  KONTROL(d.sunucu.islem_uygulandi == 1);
  KONTROL_ESIT(d.l1(), "Bak: 4499.75");
  // ISLEM yukunde 6 alan ve anahtar var
  bool bulundu = false;
  for (auto &y : d.kopruye_giden)
    if (y.rfind("ISLEM|", 0) == 0) {
      bulundu = true;
      KONTROL(y.find("|C|500.25||") != std::string::npos);
    }
  KONTROL(bulundu);
  KONTROL(d.durum_bekle(D_MENU, 5000));
  KONTROL(role_hic_acilmadi(d));
}

TEST(para_alinmazsa_8sn_sonra_ters_kayit) {
  Dunya d;
  d.ac();
  d.musteri_girisi();
  d.tikla('B');
  d.yaz("100");
  d.tikla('#');
  KONTROL(d.durum_bekle(D_PARA_BEKLE, 5000));
  uint32_t bas = d.hw.t;
  KONTROL(d.durum_bekle(D_TESLIM_BEKLE, 12000));
  uint32_t gecen = d.hw.t - bas;
  KONTROL(gecen >= 7900 && gecen <= 8300);   // 8 sn
  KONTROL(!d.hw.led);
  KONTROL(d.ekran_bekle("Para alinmadi", 5000));
  KONTROL(d.sunucu.teslim_geri == 1 && d.sunucu.teslim_alindi == 0);
}

TEST(yatirma_ve_havale_hesap_no_dogrulama) {
  Dunya d;
  d.ac();
  d.musteri_girisi();
  d.tikla('C');
  d.yaz("250");
  d.tikla('#');
  KONTROL(d.ekran_bekle("Islem tamam", 5000));
  KONTROL_ESIT(d.l1(), "Bak: 5250.00");
  d.durum_bekle(D_MENU, 5000);
  // havale: 6 hane sart; 5 haneyle # ile onaylanirsa iptal
  d.tikla('D');
  KONTROL_ESIT(d.l0(), "Hesap no(6hane)");
  d.yaz("12345");
  d.tikla('#');
  KONTROL_ESIT(d.l0(), "Iptal edildi");
  KONTROL(d.durum_bekle(D_MENU, 3000));
  d.tikla('D');
  d.yaz("123456");
  d.tikla('7');                      // 7. hane eklenmez
  KONTROL_ESIT(d.l1(), "123456");
  d.tikla('#');
  KONTROL_ESIT(d.l0(), "Tutar (C:. #:ok)");
  d.yaz("40");
  d.tikla('#');
  KONTROL(d.ekran_bekle("Islem tamam", 5000));
}

TEST(tutar_giris_kurallari) {
  Dunya d;
  d.ac();
  d.musteri_girisi();
  d.tikla('B');
  d.tikla('C');                      // bastaki nokta olmaz
  KONTROL_ESIT(d.l1(), "");
  d.yaz("12");
  d.tikla('C');
  d.tikla('C');                      // ikinci nokta olmaz
  d.yaz("345");                      // 3. kurus hanesi olmaz
  KONTROL_ESIT(d.l1(), "12.34");
  d.tikla('D');
  KONTROL_ESIT(d.l1(), "12.3");
  d.tikla('D');
  d.tikla('D');
  d.tikla('D');
  KONTROL_ESIT(d.l1(), "1");
  d.yaz("2345678901234");            // en fazla 11 karakter
  KONTROL(d.l1().size() == 11);
  d.tikla('*');
  KONTROL_ESIT(d.l0(), "Iptal edildi");
  KONTROL(d.durum_bekle(D_MENU, 3000));
  KONTROL(d.sunucu.islem_uygulandi == 0);
}

TEST(menu_30sn_tus_yoksa_oturum_kapanir_ve_sayfalar_doner) {
  Dunya d;
  d.ac();
  d.musteri_girisi();
  KONTROL_ESIT(d.l0(), "A:Bakiye B:Cek");
  d.kos(3100);
  KONTROL_ESIT(d.l0(), "#:PIN degistir");
  d.kos(3000);
  KONTROL_ESIT(d.l0(), "A:Bakiye B:Cek");
  d.kos(24000);
  KONTROL(d.term.durum() == D_BOSTA || d.term.durum() == D_MESAJ);
  KONTROL(d.bosta_bekle(3000));
  KONTROL(d.term.hassas_bos());
}

TEST(pin_degistirme_basarili_uyusmayan_ve_iptal) {
  Dunya d;
  d.ac();
  d.musteri_girisi();
  d.tikla('#');
  KONTROL_ESIT(d.l0(), "Eski PIN:");
  d.yaz("1234");
  KONTROL_ESIT(d.l0(), "Yeni PIN:");
  d.yaz("5678");
  KONTROL_ESIT(d.l0(), "Yeni PIN tekrar:");
  d.yaz("5679");
  KONTROL_ESIT(d.l0(), "PIN uyusmadi");
  KONTROL(d.durum_bekle(D_MENU, 4000));
  KONTROL(d.term.hassas_bos() == false);  // oturum acik: jeton var
  KONTROL(d.sunucu.pin == "1234");
  // iptal
  d.tikla('#');
  d.yaz("1234");
  d.tikla('*');
  KONTROL_ESIT(d.l0(), "Iptal edildi");
  KONTROL(d.durum_bekle(D_MENU, 3000));
  // basarili
  d.tikla('#');
  d.yaz("1234");
  d.yaz("5678");
  d.yaz("5678");
  KONTROL(d.ekran_bekle("PIN degisti", 5000));
  KONTROL_ESIT(d.sunucu.pin, "5678");
  // eski PIN uyusmazligi sunucuda yakalanir
  KONTROL(d.durum_bekle(D_MENU, 4000));
  d.tikla('#');
  d.yaz("1234");
  d.yaz("1111");
  d.yaz("1111");
  KONTROL(d.ekran_bekle("Eski PIN yanlis", 5000));
  KONTROL_ESIT(d.sunucu.pin, "5678");
}

TEST(kart_turleri_bloke_imzasiz_bilinmeyen) {
  Dunya d;
  d.ac();
  d.kart_okut("c0c0c001");
  KONTROL(d.ekran_bekle("Kart bloke", 3000));
  KONTROL(d.bosta_bekle(5000));
  d.kart_okut("d0d0d001");
  KONTROL(d.ekran_bekle("Kart reddedildi", 3000));
  KONTROL_ESIT(d.l1(), "Klon suphesi");
  KONTROL(d.bosta_bekle(5000));
  d.kart_okut("99999999");
  KONTROL(d.ekran_bekle("Kart taninmadi", 3000));
}

// ---- dort goz onayi

TEST(buyuk_cekme_iki_yetkili_onayi_sonra_para_teslimi) {
  Dunya d;
  d.ac();
  d.musteri_girisi();
  d.tikla('B');
  d.yaz("15000");
  d.tikla('#');
  KONTROL(d.durum_bekle(D_ONAY_IZLE, 8000));
  KONTROL_ESIT(d.l0(), "Onay gerekli");
  KONTROL_ESIT(d.l1(), "15000 TL");
  d.kos(2500);
  KONTROL(d.l0().find("Onay 0/2") == 0);
  KONTROL_ESIT(d.l1(), "Yetkili kart oku");

  for (int i = 0; i < 2; i++) {
    KONTROL(d.kart_okut(i == 0 ? "b0b0b001" : "b0b0b002", 8000));
    KONTROL(d.durum_bekle(D_KARAR_AL, 6000));
    KONTROL_ESIT(d.l0(), "A:Onayla B:Red");
    d.tikla('A');
    KONTROL(d.durum_bekle(D_PIN_AL, 2000));
    KONTROL_ESIT(d.l0(), "Yetkili PIN:");
    d.yaz("4321");
    KONTROL(d.ekran_bekle("Oyunuz alindi", 6000));
    KONTROL_ESIT(d.l1(), std::to_string(i + 1) + "/2");
    d.kos(1400);
  }
  KONTROL(d.durum_bekle(D_PARA_BEKLE, 8000));
  KONTROL(d.hw.led);
  d.tikla('A');
  KONTROL(d.ekran_bekle("Islem onaylandi", 6000));
  KONTROL(d.sunucu.teslim_alindi == 1);
  KONTROL(d.durum_bekle(D_MENU, 5000));    // musteri oturumuna geri
  KONTROL(d.term.oturum_acik());
  KONTROL(role_hic_acilmadi(d));
}

TEST(yetkili_red_verirse_islem_reddedildi) {
  Dunya d;
  d.ac();
  d.musteri_girisi();
  d.tikla('B');
  d.yaz("20000");
  d.tikla('#');
  d.durum_bekle(D_ONAY_IZLE, 8000);
  d.kos(1800);
  d.kart_okut("b0b0b001", 8000);
  d.durum_bekle(D_KARAR_AL, 6000);
  d.tikla('B');
  d.durum_bekle(D_PIN_AL, 2000);
  d.yaz("4321");
  KONTROL(d.ekran_bekle("Oyunuz alindi", 6000));
  d.sunucu.onay_verilen = 0;
  // sunucu tarafinda red: sahte durum
  d.sunucu.ozel = [&](const std::string &y) -> std::string {
    if (y.rfind("DURUM|", 0) == 0) return "DURUM|0|2|REDDEDILDI|0|0|CEKME|0|";
    return "HATA|x";
  };
  KONTROL(d.ekran_bekle("Islem reddedildi", 6000));
  KONTROL(d.durum_bekle(D_MENU, 5000));
}

TEST(musteri_karti_yetkili_degil_denince_onay_izlemeye_doner) {
  Dunya d;
  d.ac();
  d.musteri_girisi();
  d.tikla('B');
  d.yaz("15000");
  d.tikla('#');
  d.durum_bekle(D_ONAY_IZLE, 8000);
  d.kos(1800);
  d.kart_okut("a0a0a001", 8000);       // musteri karti yetkili sayilmaz
  KONTROL(d.ekran_bekle("Yetkili karti", 6000));
  KONTROL(d.durum_bekle(D_ONAY_IZLE, 4000));
  d.kos(1600);
  KONTROL(d.l0().find("Onay 0/2") == 0);
}

TEST(bos_modda_yetkili_bekleyen_talebi_onaylar) {
  Dunya d;
  d.ac();
  d.sunucu.talep_var = true;
  d.sunucu.talep_tur = "CEKME";
  d.kart_okut("b0b0b001");
  KONTROL(d.ekran_bekle("Talep #77", 6000));
  KONTROL_ESIT(d.l1(), "Cek 15000");
  KONTROL(d.durum_bekle(D_KARAR_AL, 4000));
  d.tikla('A');
  d.yaz("4321");
  KONTROL(d.ekran_bekle("Oyunuz alindi", 6000));
  KONTROL_ESIT(d.l1(), "1/2");
  KONTROL(d.bosta_bekle(4000));
  KONTROL(d.sunucu.onay_verilen == 1);
}

TEST(yetkili_yanlis_pin_hata_gosterir) {
  Dunya d;
  d.ac();
  d.sunucu.talep_var = true;
  d.kart_okut("b0b0b001");
  d.durum_bekle(D_KARAR_AL, 8000);
  d.tikla('A');
  d.yaz("0000");
  KONTROL(d.durum_bekle(D_MESAJ, 6000));
  KONTROL_ESIT(d.ekran(), "Yetkili PIN yanl / is");   // 18 karakter: iki satira bolunur
  KONTROL(d.bosta_bekle(5000));
  KONTROL(d.sunucu.onay_verilen == 0);
  KONTROL(d.term.hassas_bos());
}

// ---- kasa dairesi kilidi

TEST(kasa_acma_iki_onay_sonra_role_tam_5sn_acik) {
  Dunya d;
  d.ac();
  KONTROL(d.kart_okut("b0b0b001"));
  KONTROL(d.durum_bekle(D_TALEP_YOK, 6000));
  KONTROL_ESIT(d.l0(), "Talep yok");
  d.tikla('A');
  KONTROL_ESIT(d.l0(), "Yetkili PIN:");
  d.yaz("4321");
  KONTROL(d.durum_bekle(D_ONAY_IZLE, 8000));
  KONTROL_ESIT(d.l1(), "Kasa acma");
  d.kos(1800);
  KONTROL(role_hic_acilmadi(d));        // onay yokken role acilmaz

  for (int i = 0; i < 2; i++) {
    KONTROL(d.kart_okut(i == 0 ? "b0b0b001" : "b0b0b002", 8000));
    KONTROL(d.durum_bekle(D_KARAR_AL, 6000));
    d.tikla('A');
    d.yaz("4321");
    KONTROL(d.ekran_bekle("Oyunuz alindi", 6000));
    d.kos(1400);
    if (i == 0) KONTROL(role_hic_acilmadi(d));   // tek onayla role acilmaz
  }
  KONTROL(d.durum_bekle(D_KILIT, 8000));
  KONTROL(d.hw.role_durum);
  KONTROL_ESIT(d.l0(), "Kasa acildi");
  uint32_t acildi = d.hw.role_gecmisi.back().first;
  KONTROL(d.kos_kadar([&] { return !d.hw.role_durum; }, 7000));
  uint32_t kapandi = d.hw.role_gecmisi.back().first;
  KONTROL(kapandi - acildi >= 5000 && kapandi - acildi <= 5010);
  KONTROL_ESIT(d.l0(), "Kasa kapandi");
  KONTROL(d.bosta_bekle(4000));
  int acma = 0;
  for (auto &g : d.hw.role_gecmisi) if (g.second) acma++;
  KONTROL(acma == 1);
  KONTROL(d.term.degismez_ihlali() == 0);
  KONTROL(d.sunucu.kasa_acma_uygulandi_cevabi >= 1);
}

TEST(talep_yok_ekrani_8sn_sonra_bostaya_doner) {
  Dunya d;
  d.ac();
  d.kart_okut("b0b0b001");
  d.durum_bekle(D_TALEP_YOK, 6000);
  d.kos(7500);
  KONTROL(d.term.durum() == D_TALEP_YOK);
  d.kos(1000);
  KONTROL(d.term.durum() == D_BOSTA);
  KONTROL(role_hic_acilmadi(d));
}

// ---- baglanti kopmalari

TEST(kopru_yokken_zaman_asimi_sunucu_yanitsiz_ve_kopru_yok_gostergesi) {
  Dunya d;
  d.ac();
  d.kopru_acik = false;
  d.kart_okut("a0a0a001");
  uint32_t bas = d.hw.t;
  KONTROL(d.ekran_bekle("Sunucu yanitsiz", 9000));
  uint32_t gecen = d.hw.t - bas;
  KONTROL(gecen >= 5800 && gecen <= 6600);    // 3 deneme x 2 sn
  KONTROL(d.term.istatistik().yeniden == 2);
  KONTROL(d.term.istatistik().zaman_asimi == 1);
  KONTROL(d.bosta_bekle(5000));
  KONTROL(!d.term.kopru_ok());
  KONTROL_ESIT(d.l1(), "Kopru yok!");
  // kopru geri gelince heartbeat toparlar
  d.kopru_acik = true;
  KONTROL(d.kos_kadar([&] { return d.term.kopru_ok(); }, 20000));
  d.kos(100);
  KONTROL_ESIT(d.l1(), "Mini ATM / Kasa");
}

TEST(heartbeat_kart_okununca_iptal_edilir_kullanici_bekletilmez) {
  Dunya d;
  d.ac();
  d.kopru_acik = false;
  d.kos(10100);                        // PING gonderildi, cevap yok
  KONTROL(d.term.durum() == D_BOSTA);
  d.kopru_acik = true;
  uint32_t bas = d.hw.t;
  KONTROL(d.kart_okut("a0a0a001", 3000));
  KONTROL(d.durum_bekle(D_PIN_AL, 3000));
  KONTROL(d.hw.t - bas < 1500);
}

TEST(sunucu_kapali_hatasi_islemde_ayni_anahtarla_tekrar_denenir) {
  Dunya d;
  d.ac();
  d.musteri_girisi();
  d.tikla('B');
  d.yaz("100");
  d.sunucu.sunucu_arizali = true;
  d.tikla('#');
  d.kos_kadar([&] { return d.l0() == "Tekrar deneniyor"; }, 5000);
  KONTROL_ESIT(d.l0(), "Tekrar deneniyor");
  d.sunucu.sunucu_arizali = false;      // sunucu geri geldi
  KONTROL(d.durum_bekle(D_PARA_BEKLE, 8000));
  KONTROL(d.sunucu.islem_uygulandi == 1);   // para YALNIZ bir kez cekildi
  for (auto &p : d.sunucu.uygulama_basina) KONTROL(p.second <= 1);
  // tekrar denenen ISLEM'ler ayni idempotency anahtarini tasir
  std::string anahtar;
  int sayi = 0;
  for (auto &y : d.kopruye_giden)
    if (y.rfind("ISLEM|", 0) == 0) {
      std::string a = y.substr(y.rfind('|') + 1);
      if (anahtar.empty()) anahtar = a;
      KONTROL_ESIT(a, anahtar);
      sayi++;
    }
  KONTROL(sayi >= 2);
  KONTROL(anahtar.size() == 16);   // acilis sayaci(4) + millis(8) + rastgele(4)
}

TEST(sunucu_surekli_kapali_3_turdan_sonra_hata) {
  Dunya d;
  d.ac();
  d.musteri_girisi();
  d.tikla('B');
  d.yaz("100");
  d.sunucu.sunucu_arizali = true;
  d.tikla('#');
  KONTROL(d.durum_bekle(D_MESAJ, 8000));
  KONTROL_ESIT(d.l0(), "Sunucu kapali");
  int sayi = 0;
  for (auto &y : d.kopruye_giden) if (y.rfind("ISLEM|", 0) == 0) sayi++;
  KONTROL(sayi == 3);                    // 1 + 2 tekrar
  KONTROL(d.durum_bekle(D_MENU, 4000));
}

TEST(kopru_surum_uyumsuzsa_ekranda_belirtilir) {
  Dunya d;
  d.surum_uyumsuz = true;
  d.kos_kadar([&] { return d.term.durum() == D_BOSTA; }, 3000);
  d.kos(1500);
  KONTROL_ESIT(d.l1(), "Surum uyumsuz");
}

TEST(millis_tasmasi_akisi_bozmaz) {
  Dunya d(0xFFFFFFFFu - 2500u);        // ~2.5 sn sonra millis() 0'a sarar
  d.ac();
  KONTROL(d.musteri_girisi());
  d.tikla('A');
  KONTROL(d.ekran_bekle("Bakiye:", 4000));
  d.durum_bekle(D_MENU, 6000);
  d.tikla('B');
  d.yaz("10");
  d.tikla('#');
  KONTROL(d.durum_bekle(D_PARA_BEKLE, 8000));
  d.tikla('A');
  KONTROL(d.ekran_bekle("Islem tamam", 6000));
  KONTROL(d.hw.t < 100000);            // gercekten sarildi
}

TEST(lcd_ayni_metin_tekrar_yazilmaz) {
  Dunya d;
  d.ac();
  int once = d.hw.lcd_yazma;
  d.kos(3000);
  KONTROL(d.hw.lcd_yazma == once);     // bosta ekran durgun: I2C trafigi yok
}
