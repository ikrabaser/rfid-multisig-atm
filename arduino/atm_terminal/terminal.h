// VAULTIX ATM terminali: bloklamayan durum makinesi (FSM). Hicbir yerde delay() ya da readStringUntil() yok;
// loop() her turda tick() cagirir, tum beklemeler "su zamana kadar" seklinde durum olarak tutulur.
//
// Bu kod HICBIR karar vermez (PIN, bakiye, politika sunucudadir). Yaptigi: kart/tus/ekran/role donanimini
// surmek ve sunucuyla CRC'li seri protokolle konusmak. Guvenlik ozellikleri:
//   * Role YALNIZCA D_KILIT durumunda acik olabilir; bu degismez kural her tick'te denetlenir (ihlal sayaci).
//     D_KILIT'e yalniz gecerli CRC'li, sira numarasi eslesen "DURUM|...|UYGULANDI|...|KASA_ACMA" cevabiyla girilir.
//   * PIN ve oturum jetonu kullanimdan hemen sonra RAM'den silinir (bekleme/hata/oturum bitisi dahil).
//   * Sabit tamponlar: String/heap yok; tasan istek yollanmaz, kullaniciya hata gosterilir.
#pragma once
#include "acilis.h"
#include "baglanti.h"
#include "donanim.h"
#include "metin.h"

namespace vx {

enum Durum : uint8_t {
  D_BASLAT,
  D_BOSTA,
  D_KART_BEKLE,
  D_MESAJ,
  D_PIN_AL,
  D_GIRIS_BEKLE,
  D_MENU,
  D_SAYI_AL,
  D_BAKIYE_BEKLE,
  D_ISLEM_BEKLE,
  D_ISLEM_TAMAM,  // gecici: girilince sonuc ekrani cizilir
  D_PARA_BEKLE,
  D_TESLIM_BEKLE,
  D_ONAY_IZLE,
  D_ONAY_DURUM_BEKLE,
  D_PERSONEL_KART_BEKLE,
  D_BEKLEYEN_BEKLE,
  D_TALEP_YOK,
  D_KARAR_AL,
  D_ONAY_ISTEK_BEKLE,
  D_KASAAC_BEKLE,
  D_PINDEGIS_BEKLE,
  D_KILIT
};

class Terminal {
 public:
  explicit Terminal(Donanim &d);

  void baslat();  // setup()'tan, donanim hazir olunca
  void tick();    // loop()'tan her turda

  // Test ve hata ayiklama icin gozlem arayuzu
  Durum durum() const { return durum_; }
  bool role_acik() const { return role_acik_; }
  uint16_t degismez_ihlali() const { return ihlal_; }
  bool oturum_acik() const { return oturum_acik_; }
  const char *lcd(uint8_t satir) const { return lcd_[satir & 1]; }
  const Baglanti::Istatistik &istatistik() const { return bag_.istatistik(); }
  bool kopru_ok() const { return kopru_ok_; }
  // PIN, jeton ve kart UID'si RAM'de yoksa true (oturum bitince / bosta iken beklenir)
  bool hassas_bos() const;
  uint16_t acilis_no() const { return acilis_no_; }

 private:
  enum PinAmac : uint8_t { P_GIRIS, P_ESKI, P_YENI, P_TEKRAR, P_KARAR, P_KASAAC };
  enum SayiAmac : uint8_t { S_HEDEF, S_TUTAR };

  // durum gecisi / ortak yardimcilar
  void gir(Durum d);
  void mesaj(Durum sonra, uint16_t ms, uint8_t bip);  // ekrandaki yaziyi ms boyunca tut, sonra `sonra`ya gec
  void hata_goster(const Alanlar &a, Durum donus);
  void role_ayarla(bool ac);
  void oturum_sil();
  void pin_sil();

  // ekran (degisen satirlar LCD'ye yazilir; ayni metin tekrar yazilmaz -> I2C trafigi ve titreme azalir)
  void satir(uint8_t i, const char *m);
  void satir(uint8_t i, FS m);
  void ekran(FS a);
  void ekran(FS a, FS b);
  void ekran(FS a, const char *b);
  void ekran(const char *a, const char *b);
  void ekran_metin(const char *m);  // 16 karakteri asan sunucu mesaji iki satira bolunur

  // baglanti
  bool hazir() const;
  bool gonder(const Yazici &y, Durum bekle, Durum hata_donus);
  bool tekrar_dene(const Alanlar &a, bool goster);
  static bool sunucu_hatasi(const Alanlar &a);

  // durum isleyicileri
  void bosta();
  void ping_sonucu();
  void bosta_ekrani();
  void kart_istegi(char *uid, Durum bekle);
  void kart_bekle();
  void pin_baslat(PinAmac amac, Durum iptal_durum, bool iptal_mesaji);
  void pin_ekrani();
  void pin_al();
  void pin_tamam();
  void giris_bekle();
  void menu();
  void menu_ciz();
  void cikis();
  void sayi_baslat(SayiAmac amac);
  void sayi_al();
  void sayi_tamam();
  void islem_gonder();
  void bakiye_bekle();
  void islem_bekle();
  void tamam_goster();
  void para_baslat(bool islem_yolu);
  void para_bekle();
  void para_bitir();
  void teslim_bekle();
  void onay_baslat();
  void onay_izle();
  void onay_durum_bekle();
  void personel_kart_bekle();
  void bekleyen_bekle();
  void talep_yok();
  void karar_al();
  void onay_istek_bekle();
  void kasaac_bekle();
  void pindegis_bekle();
  void kilit();

  uint32_t gecen(uint32_t t) const { return (uint32_t)(simdi_ - t); }

  Donanim &d_;
  Baglanti bag_;
  uint32_t simdi_;
  Durum durum_;
  uint32_t giris_ms_;

  Durum mesaj_sonra_;
  uint16_t mesaj_ms_;

  bool role_acik_;
  uint16_t ihlal_;

  char lcd_[2][17];

  bool kopru_ok_, hello_ok_, uyumsuz_, ping_bekliyor_;
  uint32_t son_ping_;
  uint8_t bosta_gosterilen_;

  bool oturum_acik_;
  char uid_[21], token_[64];  // uid_: sirasiyla musteri ya da yetkili karti (oturum acildiktan sonra musteri UID'sine gerek yok)

  PinAmac pin_amac_;
  uint8_t pin_n_;
  char pin_[5], pin_a_[5], pin_b_[5];
  Durum pin_iptal_durum_;
  bool pin_iptal_mesaji_;

  SayiAmac sayi_amac_;
  char tur_;
  char tutar_[13], hedef_[8], anahtar_[17];
  uint16_t acilis_no_;
  uint32_t son_tus_ms_;

  char talep_[12], islem_[12], bakiye_[14];
  uint8_t uygulama_deneme_;

  bool onay_kasa_, onay_tanitim_, sorgu_gerek_;
  Durum onay_donus_, donus_;
  uint32_t son_sorgu_, tanitim_ms_;
  char karar_;

  bool teslim_isle_, alindi_;

  bool menu_sayfa2_;
  uint32_t sayfa_ms_;
};

}  // namespace vx
