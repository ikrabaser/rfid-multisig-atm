#include "terminal.h"

namespace vx {

namespace {
const uint32_t BASLAT_MS = 1000;
const uint32_t PING_ARALIK_MS = 10000;
const uint32_t KILIT_ACIK_MS = 5000;
const uint32_t PARA_ALMA_MS = 8000;   // para alinmazsa geri cekilmis sayilir
const uint32_t PIN_ZA_MS = 20000;
const uint32_t SAYI_ZA_MS = 30000;
const uint32_t MENU_ZA_MS = 30000;
const uint32_t MENU_SAYFA_MS = 3000;
const uint32_t KARAR_ZA_MS = 10000;
const uint32_t TALEP_YOK_ZA_MS = 8000;
const uint32_t ONAY_TANITIM_MS = 1500;
const uint32_t DURUM_ARALIK_MS = 1000;
const uint8_t UYGULAMA_YENIDEN = 2;   // zaman asimi/sunucu hatasinda ISLEM ve TESLIM bu kadar daha denenir
const uint8_t BIP_YOK = 0, BIP_TAMAM = 1, BIP_HATA = 2;
}  // namespace

Terminal::Terminal(Donanim &d)
    : d_(d), bag_(d), simdi_(0), durum_(D_BASLAT), giris_ms_(0), mesaj_sonra_(D_BOSTA), mesaj_ms_(0),
      role_acik_(false), ihlal_(0), kopru_ok_(true), hello_ok_(false), uyumsuz_(false), ping_bekliyor_(false),
      son_ping_(0), bosta_gosterilen_(255), oturum_acik_(false), pin_amac_(P_GIRIS), pin_n_(0),
      pin_iptal_durum_(D_BOSTA), pin_iptal_mesaji_(false), sayi_amac_(S_TUTAR), tur_('C'), acilis_no_(0), son_tus_ms_(0),
      uygulama_deneme_(0), onay_kasa_(false), onay_tanitim_(false), sorgu_gerek_(false), onay_donus_(D_BOSTA),
      donus_(D_BOSTA), son_sorgu_(0), tanitim_ms_(0), karar_('E'), teslim_isle_(false), alindi_(false),
      menu_sayfa2_(false), sayfa_ms_(0) {
  sil(&lcd_[0][0], sizeof lcd_);
  uid_[0] = token_[0] = 0;
  pin_[0] = pin_a_[0] = pin_b_[0] = 0;
  tutar_[0] = hedef_[0] = anahtar_[0] = 0;
  talep_[0] = islem_[0] = bakiye_[0] = 0;
}

void Terminal::baslat() {
  simdi_ = d_.simdi_ms();
  role_ayarla(false);  // guvenli durum
  d_.role(false);
  d_.para_led(false);
  acilis_no_ = acilis_sayaci_ilerlet(d_);
  bag_.baslat();
  son_ping_ = simdi_ - PING_ARALIK_MS;  // ilk HELLO hemen
  gir(D_BASLAT);
}

bool Terminal::hassas_bos() const {
  return token_[0] == 0 && uid_[0] == 0 && pin_[0] == 0 && pin_a_[0] == 0 && pin_b_[0] == 0;
}

// ------------------------------------------------------------------ ana dongu

void Terminal::tick() {
  simdi_ = d_.simdi_ms();
  d_.bekci_besle();

  // DEGISMEZ KURAL: role yalnizca D_KILIT'te acik olabilir. Her ne sebeple olursa olsun ihlal varsa kapat ve say.
  if (role_acik_ && durum_ != D_KILIT) {
    role_ayarla(false);
    ihlal_++;
  }

  bag_.tick(simdi_);
  const uint16_t gonderi = bag_.gonderi_no();

  switch (durum_) {
    case D_BASLAT:
      if (gecen(giris_ms_) >= BASLAT_MS) gir(D_BOSTA);
      break;
    case D_BOSTA: bosta(); break;
    case D_KART_BEKLE: kart_bekle(); break;
    case D_MESAJ:
      if (gecen(giris_ms_) >= mesaj_ms_) gir(mesaj_sonra_);
      break;
    case D_PIN_AL: pin_al(); break;
    case D_GIRIS_BEKLE: giris_bekle(); break;
    case D_MENU: menu(); break;
    case D_SAYI_AL: sayi_al(); break;
    case D_BAKIYE_BEKLE: bakiye_bekle(); break;
    case D_ISLEM_BEKLE: islem_bekle(); break;
    case D_ISLEM_TAMAM: break;  // gecici, gir() halleder
    case D_PARA_BEKLE: para_bekle(); break;
    case D_TESLIM_BEKLE: teslim_bekle(); break;
    case D_ONAY_IZLE: onay_izle(); break;
    case D_ONAY_DURUM_BEKLE: onay_durum_bekle(); break;
    case D_PERSONEL_KART_BEKLE: personel_kart_bekle(); break;
    case D_BEKLEYEN_BEKLE: bekleyen_bekle(); break;
    case D_TALEP_YOK: talep_yok(); break;
    case D_KARAR_AL: karar_al(); break;
    case D_ONAY_ISTEK_BEKLE: onay_istek_bekle(); break;
    case D_KASAAC_BEKLE: kasaac_bekle(); break;
    case D_PINDEGIS_BEKLE: pindegis_bekle(); break;
    case D_KILIT: kilit(); break;
  }

  // Isleyici sonucu okuduysa (ve yeni istek baslatmadiysa) tamponlari sil ve hatti serbest birak.
  if (hazir() && bag_.gonderi_no() == gonderi) bag_.bitir();
}

// ------------------------------------------------------------------ gecisler

void Terminal::gir(Durum d) {
  durum_ = d;
  giris_ms_ = simdi_;
  if (d != D_PIN_AL) pin_sil();  // PIN akisindan cikilinca (sonuc, iptal, hata) hicbir PIN RAM'de kalmaz

  switch (d) {
    case D_BASLAT:
      ekran(FM("Baslatiliyor..."), d_.bekci_resetiydi() ? FM("Bekci reseti!") : FM(""));
      break;
    case D_BOSTA:
      oturum_sil();
      if (ping_bekliyor_ || bag_.sonuc() != Baglanti::BOS) {
        bag_.iptal();
        ping_bekliyor_ = false;
      }
      bosta_gosterilen_ = 255;
      bosta_ekrani();
      break;
    case D_MENU:
      menu_sayfa2_ = false;
      sayfa_ms_ = simdi_;
      menu_ciz();
      break;
    case D_PIN_AL:
      pin_n_ = 0;
      pin_[0] = 0;
      pin_ekrani();
      break;
    case D_ISLEM_TAMAM: tamam_goster(); break;
    case D_PARA_BEKLE:
      alindi_ = false;
      d_.para_led(true);
      ekran(FM("Parayi alin"), FM("A: Aldim"));
      break;
    case D_KARAR_AL: ekran(FM("A:Onayla B:Red"), FM("*:Iptal")); break;
    case D_TALEP_YOK: ekran(FM("Talep yok"), FM("A:Kasa ac *:Cik")); break;
    case D_KILIT:
      ekran(FM("Kasa acildi"), FM("5 sn acik"));
      d_.bip(true);
      role_ayarla(true);
      break;
    default: break;
  }
}

void Terminal::mesaj(Durum sonra, uint16_t ms, uint8_t bip) {
  if (bip == BIP_TAMAM) d_.bip(true);
  if (bip == BIP_HATA) d_.bip(false);
  mesaj_sonra_ = sonra;
  mesaj_ms_ = ms;
  durum_ = D_MESAJ;
  giris_ms_ = simdi_;
  pin_sil();
}

void Terminal::hata_goster(const Alanlar &a, Durum donus) {
  const char *m = a.al(1);
  if (m[0] == 0) m = a.al(0);
  ekran_metin(m);
  mesaj(donus, 2500, BIP_HATA);
}

void Terminal::role_ayarla(bool ac) {
  if (role_acik_ == ac) return;
  role_acik_ = ac;
  d_.role(ac);
}

void Terminal::oturum_sil() {
  sil(token_, sizeof token_);
  sil(uid_, sizeof uid_);
  oturum_acik_ = false;
  pin_sil();
}

void Terminal::pin_sil() {
  sil(pin_, sizeof pin_);
  sil(pin_a_, sizeof pin_a_);
  sil(pin_b_, sizeof pin_b_);
  pin_n_ = 0;
}

// ------------------------------------------------------------------ ekran

void Terminal::satir(uint8_t i, const char *m) {
  char t[17];
  uint8_t n = 0;
  while (n < 16 && m[n]) {
    t[n] = m[n];
    n++;
  }
  while (n < 16) t[n++] = ' ';
  t[16] = 0;
  for (uint8_t k = 0; k < 17; k++) {
    if (t[k] != lcd_[i][k]) {
      for (uint8_t j = 0; j < 17; j++) lcd_[i][j] = t[j];
      d_.lcd_satir(i, t);
      return;
    }
  }
}

void Terminal::satir(uint8_t i, FS m) {
  char t[17];
  kopyala(t, sizeof t, m);
  satir(i, t);
}

void Terminal::ekran(FS a) {
  satir(0, a);
  satir(1, "");
}
void Terminal::ekran(FS a, FS b) {
  satir(0, a);
  satir(1, b);
}
void Terminal::ekran(FS a, const char *b) {
  satir(0, a);
  satir(1, b);
}
void Terminal::ekran(const char *a, const char *b) {
  satir(0, a);
  satir(1, b);
}
void Terminal::ekran_metin(const char *m) {
  satir(0, m);
  satir(1, uzunluk(m) > 16 ? m + 16 : "");
}

// ------------------------------------------------------------------ baglanti

bool Terminal::hazir() const {
  Baglanti::Sonuc s = bag_.sonuc();
  return s == Baglanti::CEVAP || s == Baglanti::ZAMAN_ASIMI;
}

// Hazirlanan yuku yollar ve `bekle` durumuna gecer. Yuk sigmadiysa istek HIC yollanmaz.
bool Terminal::gonder(const Yazici &y, Durum bekle, Durum hata_donus) {
  uygulama_deneme_ = 0;
  if (y.tasti) {
    bag_.bitir();
    ekran(FM("Istek cok uzun"));
    mesaj(hata_donus, 2000, BIP_HATA);
    return false;
  }
  bag_.gonder();
  gir(bekle);
  return true;
}

bool Terminal::sunucu_hatasi(const Alanlar &a) { return esit(a.al(0), FM("HATA")) && baslar(a.al(1), FM("Sunucu")); }

// ISLEM/TESLIM: zaman asimi ya da "HATA|Sunucu..." olursa AYNI yukle (ISLEM'de ayni idempotency anahtariyla) yeniden dene.
bool Terminal::tekrar_dene(const Alanlar &a, bool goster) {
  if (uygulama_deneme_ >= UYGULAMA_YENIDEN || !sunucu_hatasi(a)) return false;
  uygulama_deneme_++;
  if (goster) ekran(FM("Tekrar deneniyor"));
  return bag_.yeniden();
}

// ------------------------------------------------------------------ bosta / kart

void Terminal::bosta_ekrani() {
  uint8_t g = uyumsuz_ ? 2 : (kopru_ok_ ? 0 : 1);
  if (g == bosta_gosterilen_) return;
  bosta_gosterilen_ = g;
  ekran(FM("Karti okutun"), g == 2 ? FM("Surum uyumsuz") : (g == 1 ? FM("Kopru yok!") : FM("Mini ATM / Kasa")));
}

void Terminal::ping_sonucu() {
  Alanlar a;
  a.bol(bag_.cevap());
  if (bag_.sonuc() == Baglanti::CEVAP) {
    kopru_ok_ = true;
    if (esit(a.al(0), FM("HELLO"))) {
      if (esit(a.al(1), FM("OK"))) {
        hello_ok_ = true;
        uyumsuz_ = false;
      } else {
        uyumsuz_ = true;
      }
    }
  } else {
    kopru_ok_ = false;
  }
  ping_bekliyor_ = false;
}

void Terminal::bosta() {
  if (ping_bekliyor_ && hazir()) ping_sonucu();

  if (d_.kart_oku(uid_, sizeof uid_)) {
    if (ping_bekliyor_) {  // kontrol sorgusunu birak, kullaniciyi bekletme
      bag_.iptal();
      ping_bekliyor_ = false;
    }
    kart_istegi(uid_, D_KART_BEKLE);
    return;
  }

  if (!ping_bekliyor_ && bag_.sonuc() == Baglanti::BOS && gecen(son_ping_) >= PING_ARALIK_MS) {
    son_ping_ = simdi_;
    Yazici y(bag_.yuk_tamponu(), Baglanti::YUK_KAP);
    if (hello_ok_) {
      y.ekle(FM("PING"));
    } else {
      y.ekle(FM("HELLO|")).ekle_sayi(SURUM);  // kopru onbellegini temizler, surumu dogrular
    }
    bag_.gonder();
    ping_bekliyor_ = true;
  }
  bosta_ekrani();
}

void Terminal::kart_istegi(char *uid, Durum bekle) {
  ekran(FM("Kart okundu"), FM("Bekleyin..."));
  Yazici y(bag_.yuk_tamponu(), Baglanti::YUK_KAP);
  y.ekle(FM("KART|")).ekle(uid);
  gonder(y, bekle, D_BOSTA);
}

void Terminal::kart_bekle() {
  if (!hazir()) return;
  Alanlar a;
  a.bol(bag_.cevap());
  kopru_ok_ = (bag_.sonuc() == Baglanti::CEVAP);

  if (!esit(a.al(0), FM("TIP"))) {
    hata_goster(a, D_BOSTA);
    return;
  }
  const char *tip = a.al(1);
  if (esit(tip, FM("MUSTERI"))) {
    pin_baslat(P_GIRIS, D_BOSTA, true);
  } else if (esit(tip, FM("PERSONEL"))) {
    ekran(FM("Kontrol ediliyor"));
    Yazici y(bag_.yuk_tamponu(), Baglanti::YUK_KAP);
    y.ekle(FM("BEKLEYEN"));
    gonder(y, D_BEKLEYEN_BEKLE, D_BOSTA);
  } else if (esit(tip, FM("BLOKE"))) {
    ekran(FM("Kart bloke"), FM("Subeye basvurun"));
    mesaj(D_BOSTA, 2500, BIP_HATA);
  } else if (esit(tip, FM("IMZASIZ"))) {
    ekran(FM("Kart reddedildi"), FM("Klon suphesi"));
    mesaj(D_BOSTA, 2500, BIP_HATA);
  } else {
    ekran(FM("Kart taninmadi"));
    mesaj(D_BOSTA, 1800, BIP_HATA);
  }
}

// ------------------------------------------------------------------ PIN

void Terminal::pin_baslat(PinAmac amac, Durum iptal_durum, bool iptal_mesaji) {
  pin_amac_ = amac;
  pin_iptal_durum_ = iptal_durum;
  pin_iptal_mesaji_ = iptal_mesaji;
  gir(D_PIN_AL);
}

void Terminal::pin_ekrani() {
  switch (pin_amac_) {
    case P_GIRIS: satir(0, FM("PIN girin:")); break;
    case P_ESKI: satir(0, FM("Eski PIN:")); break;
    case P_YENI: satir(0, FM("Yeni PIN:")); break;
    case P_TEKRAR: satir(0, FM("Yeni PIN tekrar:")); break;
    default: satir(0, FM("Yetkili PIN:")); break;
  }
  char t[17];
  Yazici y(t, sizeof t);
  y.ekle(FM("PIN: "));
  for (uint8_t i = 0; i < pin_n_; i++) y.ekle('*');
  while (y.uzunluk() < 11) y.ekle(' ');
  y.ekle(FM("D:sil"));
  satir(1, t);
}

void Terminal::pin_al() {
  if (gecen(giris_ms_) > PIN_ZA_MS) {
    pin_n_ = 0;
  } else {
    char t = d_.tus();
    if (!t) return;
    if (t >= '0' && t <= '9') {
      pin_[pin_n_++] = t;
      pin_[pin_n_] = 0;
      pin_ekrani();
      if (pin_n_ == 4) pin_tamam();
      return;
    }
    if (t == 'D') {
      if (pin_n_ > 0) pin_[--pin_n_] = 0;
      pin_ekrani();
      return;
    }
    if (t != '*') return;
  }
  // iptal ya da zaman asimi
  if (pin_iptal_mesaji_) {
    ekran(FM("Iptal edildi"));
    mesaj(pin_iptal_durum_, 1000, BIP_YOK);
  } else {
    gir(pin_iptal_durum_);
  }
}

void Terminal::pin_tamam() {
  Yazici y(bag_.yuk_tamponu(), Baglanti::YUK_KAP);
  switch (pin_amac_) {
    case P_GIRIS:
      y.ekle(FM("PIN|")).ekle(uid_).ekle('|').ekle(pin_);
      ekran(FM("Kontrol ediliyor"));
      gonder(y, D_GIRIS_BEKLE, D_BOSTA);
      return;
    case P_ESKI:
      kopyala(pin_a_, sizeof pin_a_, pin_);
      pin_baslat(P_YENI, pin_iptal_durum_, pin_iptal_mesaji_);
      return;
    case P_YENI:
      kopyala(pin_b_, sizeof pin_b_, pin_);
      pin_baslat(P_TEKRAR, pin_iptal_durum_, pin_iptal_mesaji_);
      return;
    case P_TEKRAR:
      if (!esit(pin_, pin_b_)) {
        ekran(FM("PIN uyusmadi"), FM("Degismedi"));
        mesaj(D_MENU, 2000, BIP_HATA);
        return;
      }
      y.ekle(FM("PINDEGIS|")).ekle(token_).ekle('|').ekle(pin_a_).ekle('|').ekle(pin_b_);
      ekran(FM("Kontrol ediliyor"));
      gonder(y, D_PINDEGIS_BEKLE, D_MENU);
      return;
    case P_KARAR:
      y.ekle(FM("ONAY|")).ekle(talep_).ekle('|').ekle(uid_).ekle('|').ekle(pin_).ekle('|').ekle(karar_ == 'E' ? 'E' : 'H');
      ekran(FM("Kontrol ediliyor"));
      gonder(y, D_ONAY_ISTEK_BEKLE, donus_);
      return;
    case P_KASAAC:
      y.ekle(FM("KASAAC|")).ekle(uid_).ekle('|').ekle(pin_);
      ekran(FM("Kontrol ediliyor"));
      gonder(y, D_KASAAC_BEKLE, D_BOSTA);
      return;
  }
}

void Terminal::giris_bekle() {
  if (!hazir()) return;
  Alanlar a;
  a.bol(bag_.cevap());
  kopru_ok_ = (bag_.sonuc() == Baglanti::CEVAP);

  if (!esit(a.al(0), FM("GIRIS"))) {
    hata_goster(a, D_BOSTA);
    return;
  }
  if (!kopyala(token_, sizeof token_, a.al(1)) || token_[0] == 0) {  // jeton tampona sigmadi: oturum acma
    sil(token_, sizeof token_);
    ekran(FM("Oturum acilamadi"));
    mesaj(D_BOSTA, 2000, BIP_HATA);
    return;
  }
  oturum_acik_ = true;
  ekran(FM("Merhaba"), a.al(2));
  mesaj(D_MENU, 1200, BIP_TAMAM);
}

// ------------------------------------------------------------------ menu (musteri)

void Terminal::menu_ciz() {
  if (!menu_sayfa2_) {
    ekran(FM("A:Bakiye B:Cek"), FM("C:Yatir D:Havale"));
  } else {
    ekran(FM("#:PIN degistir"), FM("*:Cikis"));
  }
}

void Terminal::cikis() {
  ekran(FM("Cikis yapildi"));
  oturum_sil();
  mesaj(D_BOSTA, 1000, BIP_YOK);
}

void Terminal::menu() {
  if (gecen(giris_ms_) >= MENU_ZA_MS) {  // 30 sn tus yok: oturumu kapat
    cikis();
    return;
  }
  char t = d_.tus();
  if (t == 'A') {
    ekran(FM("Bekleyin..."));
    Yazici y(bag_.yuk_tamponu(), Baglanti::YUK_KAP);
    y.ekle(FM("BAKIYE|")).ekle(token_);
    gonder(y, D_BAKIYE_BEKLE, D_MENU);
  } else if (t == 'B' || t == 'C' || t == 'D') {
    tur_ = t == 'B' ? 'C' : (t == 'C' ? 'Y' : 'H');
    hedef_[0] = 0;
    tutar_[0] = 0;
    sayi_baslat(tur_ == 'H' ? S_HEDEF : S_TUTAR);
  } else if (t == '#') {
    pin_baslat(P_ESKI, D_MENU, true);
  } else if (t == '*') {
    cikis();
  } else if (gecen(sayfa_ms_) >= MENU_SAYFA_MS) {  // 16x2'ye sigmadigi icin menu iki sayfa
    menu_sayfa2_ = !menu_sayfa2_;
    sayfa_ms_ = simdi_;
    menu_ciz();
  }
}

void Terminal::bakiye_bekle() {
  if (!hazir()) return;
  Alanlar a;
  a.bol(bag_.cevap());
  if (!esit(a.al(0), FM("BAKIYE"))) {
    hata_goster(a, D_MENU);
    return;
  }
  char t[17];
  Yazici y(t, sizeof t);
  y.ekle(a.al(1)).ekle(FM(" TL"));
  ekran(FM("Bakiye:"), t);
  mesaj(D_MENU, 3500, BIP_TAMAM);
}

void Terminal::pindegis_bekle() {
  if (!hazir()) return;
  Alanlar a;
  a.bol(bag_.cevap());
  if (esit(a.al(0), FM("PINDEGIS"))) {
    ekran(FM("PIN degisti"), FM("Yeni PIN gecerli"));
    mesaj(D_MENU, 2500, BIP_TAMAM);
  } else {
    hata_goster(a, D_MENU);
  }
}

// ------------------------------------------------------------------ rakam girisi (hesap no, tutar)

void Terminal::sayi_baslat(SayiAmac amac) {
  sayi_amac_ = amac;
  son_tus_ms_ = simdi_;
  durum_ = D_SAYI_AL;
  giris_ms_ = simdi_;
  pin_sil();
  if (amac == S_HEDEF) {
    hedef_[0] = 0;
    ekran(FM("Hesap no(6hane)"), "");
  } else {
    tutar_[0] = 0;
    ekran(FM("Tutar (C:. #:ok)"), "");
  }
}

void Terminal::sayi_al() {
  char *s = sayi_amac_ == S_HEDEF ? hedef_ : tutar_;
  const uint8_t maks = sayi_amac_ == S_HEDEF ? 6 : 11;
  const bool ondalik = sayi_amac_ == S_TUTAR;
  uint8_t n = (uint8_t)uzunluk(s);

  if (gecen(son_tus_ms_) > SAYI_ZA_MS) {
    ekran(FM("Iptal edildi"));
    mesaj(D_MENU, 1000, BIP_YOK);
    return;
  }
  char t = d_.tus();
  if (!t) return;
  son_tus_ms_ = simdi_;

  if (t >= '0' && t <= '9') {
    const char *nokta = nullptr;
    for (uint8_t i = 0; i < n; i++) {
      if (s[i] == '.') nokta = s + i;
    }
    bool kurus_dolu = nokta && (uint8_t)(s + n - nokta) > 2;  // noktadan sonra 2 hane var
    if (!kurus_dolu && n < maks) {
      s[n] = t;
      s[n + 1] = 0;
    }
  } else if (t == 'C' && ondalik) {
    bool var = false;
    for (uint8_t i = 0; i < n; i++) var = var || s[i] == '.';
    if (n > 0 && !var && n < maks) {
      s[n] = '.';
      s[n + 1] = 0;
    }
  } else if (t == '#') {
    if (n > 0) {
      if (s[n - 1] == '.') s[n - 1] = 0;
      sayi_tamam();
      return;
    }
  } else if (t == '*') {
    ekran(FM("Iptal edildi"));
    mesaj(D_MENU, 1000, BIP_YOK);
    return;
  } else if (t == 'D') {
    if (n > 0) s[n - 1] = 0;
  }
  satir(1, s);
}

void Terminal::sayi_tamam() {
  if (sayi_amac_ == S_HEDEF) {
    if (uzunluk(hedef_) != 6) {
      ekran(FM("Iptal edildi"));
      mesaj(D_MENU, 1000, BIP_YOK);
      return;
    }
    sayi_baslat(S_TUTAR);
    return;
  }
  islem_gonder();
}

void Terminal::islem_gonder() {
  ekran(FM("Isleniyor..."));
  // Idempotency anahtari: ayni istek tekrarlanirsa AYNI anahtar gider, sunucu para islemini iki kez uygulamaz.
  // acilis sayaci (kalici) + millis + rastgele: yeniden baslatma sonrasi bile eski bir anahtarla cakismaz (bkz. acilis.h).
  Yazici k(anahtar_, sizeof anahtar_);
  k.ekle_hex(acilis_no_, 4).ekle_hex(simdi_, 8).ekle_hex(d_.rastgele(), 4);

  Yazici y(bag_.yuk_tamponu(), Baglanti::YUK_KAP);
  y.ekle(FM("ISLEM|")).ekle(token_).ekle('|').ekle(tur_).ekle('|').ekle(tutar_).ekle('|').ekle(hedef_).ekle('|').ekle(anahtar_);
  gonder(y, D_ISLEM_BEKLE, D_MENU);
}

void Terminal::islem_bekle() {
  if (!hazir()) return;
  Alanlar a;
  a.bol(bag_.cevap());
  if (tekrar_dene(a, true)) return;
  kopru_ok_ = (bag_.sonuc() == Baglanti::CEVAP);

  if (esit(a.al(0), FM("TAMAM"))) {  // TAMAM|bakiye|islemId|plan
    kopyala(bakiye_, sizeof bakiye_, a.al(1));
    kopyala(islem_, sizeof islem_, a.al(2));
    if (tur_ == 'C') {
      para_baslat(true);
    } else {
      gir(D_ISLEM_TAMAM);
    }
  } else if (esit(a.al(0), FM("BEKLE"))) {  // BEKLE|talepId|gereken: dort goz onayi
    kopyala(talep_, sizeof talep_, a.al(1));
    onay_kasa_ = false;
    onay_donus_ = D_MENU;
    onay_baslat();
  } else {
    hata_goster(a, D_MENU);
  }
}

void Terminal::tamam_goster() {
  char t[17];
  Yazici y(t, sizeof t);
  y.ekle(FM("Bak: ")).ekle(bakiye_);
  ekran(FM("Islem tamam"), t);
  mesaj(D_MENU, 3000, BIP_TAMAM);
}

// ------------------------------------------------------------------ para teslimi

// islem_yolu: true = dogrudan cekme (bittikten sonra "Islem tamam"), false = onayli islem (onay_donus_'e doner)
void Terminal::para_baslat(bool islem_yolu) {
  teslim_isle_ = islem_yolu;
  gir(D_PARA_BEKLE);
}

void Terminal::para_bekle() {
  char t = d_.tus();  // A tusu "para alindi" sensorunu simule eder
  if (t == 'A') {
    alindi_ = true;
    para_bitir();
  } else if (gecen(giris_ms_) >= PARA_ALMA_MS) {
    para_bitir();
  }
}

void Terminal::para_bitir() {
  d_.para_led(false);
  Durum sonra = teslim_isle_ ? D_ISLEM_TAMAM : onay_donus_;
  if (islem_[0] == 0) {  // islem numarasi yok: teslim bildirilemez
    if (teslim_isle_) {
      gir(D_ISLEM_TAMAM);
    } else {
      gir(sonra);
    }
    return;
  }
  ekran(FM("Kaydediliyor..."));
  Yazici y(bag_.yuk_tamponu(), Baglanti::YUK_KAP);
  y.ekle(FM("TESLIM|")).ekle(islem_).ekle('|').ekle(alindi_ ? 'A' : 'H');
  gonder(y, D_TESLIM_BEKLE, sonra);
}

void Terminal::teslim_bekle() {
  if (!hazir()) return;
  Alanlar a;
  a.bol(bag_.cevap());
  if (tekrar_dene(a, false)) return;  // bildirim idempotent: ayni TESLIM tekrar yollanabilir
  kopru_ok_ = (bag_.sonuc() == Baglanti::CEVAP);

  Durum donus = teslim_isle_ ? D_MENU : onay_donus_;
  if (!esit(a.al(0), FM("TESLIM"))) {
    // Para zaten verildi/islem kaydedildi: hatayi goster, sonra yine de sonuc ekranina don
    const char *m = a.al(1)[0] ? a.al(1) : a.al(0);
    ekran_metin(m);
    mesaj(teslim_isle_ ? D_ISLEM_TAMAM : onay_donus_, 2500, BIP_HATA);
    return;
  }
  if (esit(a.al(1), FM("GERI"))) {
    ekran(FM("Para alinmadi"), FM("Islem iptal"));
    mesaj(donus, 2500, BIP_HATA);
  } else if (teslim_isle_) {
    gir(D_ISLEM_TAMAM);
  } else {
    ekran(FM("Islem onaylandi"), FM("Tamamlandi"));
    mesaj(onay_donus_, 1500, BIP_TAMAM);
  }
}

// ------------------------------------------------------------------ dort goz onayi (izleme)

void Terminal::onay_baslat() {
  ekran(FM("Onay gerekli"), "");
  if (onay_kasa_) {
    satir(1, FM("Kasa acma"));
  } else {
    char t[17];
    Yazici y(t, sizeof t);
    y.ekle(tutar_).ekle(FM(" TL"));
    satir(1, t);
  }
  onay_tanitim_ = true;
  tanitim_ms_ = simdi_;
  sorgu_gerek_ = true;
  durum_ = D_ONAY_IZLE;
  giris_ms_ = simdi_;
  pin_sil();
}

void Terminal::onay_izle() {
  if (onay_tanitim_) {
    if (gecen(tanitim_ms_) < ONAY_TANITIM_MS) return;
    onay_tanitim_ = false;
  }
  if (d_.kart_oku(uid_, sizeof uid_)) {  // yetkili karti okutuldu
    kart_istegi(uid_, D_PERSONEL_KART_BEKLE);
    return;
  }
  if (sorgu_gerek_ || gecen(son_sorgu_) >= DURUM_ARALIK_MS) {
    sorgu_gerek_ = false;
    son_sorgu_ = simdi_;
    Yazici y(bag_.yuk_tamponu(), Baglanti::YUK_KAP);
    y.ekle(FM("DURUM|")).ekle(talep_);
    gonder(y, D_ONAY_DURUM_BEKLE, onay_donus_);
  }
}

void Terminal::onay_durum_bekle() {
  if (!hazir()) {
    if (d_.kart_oku(uid_, sizeof uid_)) {  // sorgu surerken kart: sorguyu birak, yetkili akisina gec
      bag_.iptal();
      kart_istegi(uid_, D_PERSONEL_KART_BEKLE);
    }
    return;
  }
  Alanlar a;
  a.bol(bag_.cevap());
  kopru_ok_ = (bag_.sonuc() == Baglanti::CEVAP);

  if (!esit(a.al(0), FM("DURUM"))) {
    hata_goster(a, onay_donus_);
    return;
  }
  // DURUM|verilen|gereken|durum|kalanSn|ustYetkili|tur|islemId|plan
  const char *durum = a.al(3);
  if (esit(durum, FM("UYGULANDI"))) {
    if (esit(a.al(6), FM("KASA_ACMA"))) {
      gir(D_KILIT);  // roleyi acabilen TEK yol
    } else if (esit(a.al(6), FM("CEKME"))) {
      kopyala(islem_, sizeof islem_, a.al(7));
      d_.bip(true);
      para_baslat(false);
    } else {
      ekran(FM("Islem onaylandi"), FM("Tamamlandi"));
      mesaj(onay_donus_, 1500, BIP_TAMAM);
    }
  } else if (esit(durum, FM("REDDEDILDI"))) {
    ekran(FM("Islem reddedildi"));
    mesaj(onay_donus_, 2500, BIP_HATA);
  } else if (esit(durum, FM("SURESI_DOLDU"))) {
    ekran(FM("Sure doldu"), FM("Islem iptal"));
    mesaj(onay_donus_, 2500, BIP_HATA);
  } else if (esit(durum, FM("BASARISIZ"))) {
    ekran(FM("Islem basarisiz"), FM("Subeye basvurun"));
    mesaj(onay_donus_, 2500, BIP_HATA);
  } else {
    char t[17];
    Yazici y(t, sizeof t);
    y.ekle(FM("Onay ")).ekle(a.al(1)).ekle('/').ekle(a.al(2)).ekle(' ').ekle(a.al(4)).ekle(FM("sn"));
    satir(0, t);
    satir(1, esit(a.al(5), FM("1")) ? FM("Ust rol gerekli") : FM("Yetkili kart oku"));
    durum_ = D_ONAY_IZLE;
    giris_ms_ = simdi_;
  }
}

void Terminal::personel_kart_bekle() {
  if (!hazir()) return;
  Alanlar a;
  a.bol(bag_.cevap());
  kopru_ok_ = (bag_.sonuc() == Baglanti::CEVAP);

  if (esit(a.al(0), FM("TIP")) && esit(a.al(1), FM("PERSONEL"))) {
    donus_ = D_ONAY_IZLE;
    gir(D_KARAR_AL);
  } else {
    ekran(FM("Yetkili karti"), FM("degil"));
    mesaj(D_ONAY_IZLE, 1200, BIP_HATA);
  }
}

// ------------------------------------------------------------------ yetkili (personel) akislari

// Bos moddayken yetkili karti: bekleyen talebi goster, yoksa kasa acma talebi baslat
void Terminal::bekleyen_bekle() {
  if (!hazir()) return;
  Alanlar a;
  a.bol(bag_.cevap());
  kopru_ok_ = (bag_.sonuc() == Baglanti::CEVAP);

  if (!esit(a.al(0), FM("BEKLEYEN"))) {
    hata_goster(a, D_BOSTA);
    return;
  }
  if (esit(a.al(1), FM("YOK"))) {
    gir(D_TALEP_YOK);
    return;
  }
  kopyala(talep_, sizeof talep_, a.al(1));
  char t1[17], t2[17];
  Yazici y1(t1, sizeof t1), y2(t2, sizeof t2);
  y1.ekle(FM("Talep #")).ekle(a.al(1));
  y2.ekle(a.al(2)).ekle(' ').ekle(a.al(3));
  ekran(t1, t2);
  donus_ = D_BOSTA;
  mesaj(D_KARAR_AL, 2000, BIP_YOK);
}

void Terminal::talep_yok() {
  if (gecen(giris_ms_) >= TALEP_YOK_ZA_MS) {
    gir(D_BOSTA);
    return;
  }
  char t = d_.tus();
  if (t == 'A') {
    pin_baslat(P_KASAAC, D_BOSTA, false);
  } else if (t == '*') {
    gir(D_BOSTA);
  }
}

void Terminal::karar_al() {
  if (gecen(giris_ms_) >= KARAR_ZA_MS) {
    gir(donus_);
    return;
  }
  char t = d_.tus();
  if (t == 'A' || t == 'B') {
    karar_ = t == 'A' ? 'E' : 'H';
    pin_baslat(P_KARAR, donus_, false);
  } else if (t == '*') {
    gir(donus_);
  }
}

void Terminal::onay_istek_bekle() {
  if (!hazir()) return;
  Alanlar a;
  a.bol(bag_.cevap());
  kopru_ok_ = (bag_.sonuc() == Baglanti::CEVAP);

  if (esit(a.al(0), FM("ONAY"))) {  // ONAY|verilen|gereken|durum
    char t[17];
    Yazici y(t, sizeof t);
    y.ekle(a.al(1)).ekle('/').ekle(a.al(2));
    ekran(FM("Oyunuz alindi"), t);
    mesaj(donus_, 1200, BIP_TAMAM);
  } else {
    hata_goster(a, donus_);
  }
}

void Terminal::kasaac_bekle() {
  if (!hazir()) return;
  Alanlar a;
  a.bol(bag_.cevap());
  kopru_ok_ = (bag_.sonuc() == Baglanti::CEVAP);

  if (esit(a.al(0), FM("BEKLE"))) {
    kopyala(talep_, sizeof talep_, a.al(1));
    onay_kasa_ = true;
    onay_donus_ = D_BOSTA;
    onay_baslat();
  } else {
    hata_goster(a, D_BOSTA);
  }
}

// ------------------------------------------------------------------ kasa kilidi

void Terminal::kilit() {
  if (gecen(giris_ms_) >= KILIT_ACIK_MS) {
    role_ayarla(false);
    ekran(FM("Kasa kapandi"));
    mesaj(onay_donus_, 1000, BIP_YOK);
  }
}

}  // namespace vx
