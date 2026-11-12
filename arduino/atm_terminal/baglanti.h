// Baglanti (link) katmani: tek seferde TEK bekleyen istek, CRC'li cerceve, zaman asimi + yeniden iletim.
// Hic bloklamaz: her sey tick() icinde ilerler. Terminal durum makinesi bu katmanin ustunde calisir.
//
//   gonder()  ──► tx tamponuna cerceve ──► seri hat (bos yer oldukca parca parca)
//     cevap yok / NAK / bozuk satir ──► ayni sira no ile yeniden (en fazla DENEME_SAYISI, deneme basina DENEME_MS)
//     hepsi tukenirse ──► ZAMAN_ASIMI ("HATA|Sunucu yanitsiz" sentetik cevabiyla)
//   yeniden() : uygulama seviyesi yeniden deneme, ayni yuk YENI sira no ile (gec gelen eski cevaplar karismasin)
//
// Kopru tarafi (bridge/cerceve.py) ayni sira no + ayni yuku tekrar gorurse istegi TEKRAR ISLEMEZ, onbellekten cevaplar.
#pragma once
#include "cerceve.h"
#include "donanim.h"
#include "metin.h"

#ifndef VX_DENEME_MS
#define VX_DENEME_MS 2000   // bir istek icin cevap bekleme suresi (ms); DENEME_SAYISI kez denenir
#endif

namespace vx {

class Baglanti {
 public:
  enum { YUK_KAP = YUK_EN_COK + 1, RX_KAP = CERCEVE_EN_COK + 4, FR_KAP = CERCEVE_EN_COK + 2 };
  enum { DENEME_SAYISI = 3 };
  enum Sonuc : uint8_t { BOS, BEKLIYOR, CEVAP, ZAMAN_ASIMI };

  struct Istatistik {
    uint16_t gonderilen;   // yeni istek
    uint16_t yeniden;      // cerceve tekrar yollama (zaman asimi / NAK / bozuk cevap)
    uint16_t nak;          // kopru "CRC tutmadi" dedi
    uint16_t bozuk;        // CRC'si tutmayan / tasan gelen satir
    uint16_t yoksayilan;   // isteksiz ya da eski sira numarali (gec gelen) cevap
    uint16_t zaman_asimi;  // tum denemeler tukendi
  };

  static const uint32_t DENEME_MS = VX_DENEME_MS;   // varsayilan 2000; Wokwi'de elle cevap icin buyutulur (wokwi_hazirla.py)

  explicit Baglanti(Donanim &d);

  void baslat();                      // rastgele baslangic sira numarasi
  char *yuk_tamponu();                // istek yuku (Yazici ile, en fazla YUK_KAP bayt) buraya yazilir, sonra gonder()
  void gonder();                      // yeni istek (yeni sira no)
  bool yeniden();                     // ayni yuk, YENI sira no (yuk_ bossa false)
  void iptal() { bitir(); }           // bekleyen istegi birak (cevabi gelirse yoksayilir)
  void bitir();                       // sonucu tuket: yuk/cevap tamponlarini SIL, BOS'a don
  void tick(uint32_t simdi);

  Sonuc sonuc() const { return sonuc_; }
  char *cevap() { return cevap_; }    // sonuc CEVAP ya da ZAMAN_ASIMI iken gecerli (NUL'lu yuk, yerinde bolunebilir)
  uint16_t gonderi_no() const { return gonderi_no_; }  // her gonder()/yeniden()'de artar
  const Istatistik &istatistik() const { return ist_; }

 private:
  void oku();
  void satir_isle();
  void hizli_yeniden();
  void tx_hazirla(uint32_t simdi);
  void tx_pompala();
  void zaman_asimina_dus();
  void yeni_istek(bool yeni_sira);

  Donanim &d_;
  Sonuc sonuc_;
  uint8_t sira_;
  uint8_t deneme_;
  bool gonder_bekliyor_;  // cerceve (yeniden) yollanacak, tx tamponunun bosalmasi bekleniyor
  uint32_t son_gonderim_;
  uint16_t gonderi_no_;

  // Tek tampon: "SS:" + yuk + "*CCCC\n". Yuk fr_+3'e yazilir; yeniden gonderimde yalniz sira no ve CRC yeniden yazilir.
  // (Ayri yuk + tx tamponlari ~130 bayt RAM daha harcardi; Uno'da toplam 2 KB var.)
  char fr_[FR_KAP];
  uint8_t yuk_n_;
  uint8_t tx_n_, tx_i_;
  char rx_[RX_KAP];
  uint8_t rx_n_;
  bool rx_tasti_;
  char *cevap_;
  Istatistik ist_;
};

}  // namespace vx
