#include "baglanti.h"

namespace vx {

Baglanti::Baglanti(Donanim &d)
    : d_(d), sonuc_(BOS), sira_(0), deneme_(0), gonder_bekliyor_(false), son_gonderim_(0), gonderi_no_(0),
      yuk_n_(0), tx_n_(0), tx_i_(0), rx_n_(0), rx_tasti_(false), cevap_(nullptr) {
  fr_[0] = 0;
  rx_[0] = 0;
  ist_ = Istatistik();
}

void Baglanti::baslat() {
  sira_ = (uint8_t)(d_.rastgele() & 0xFF);  // acilista rastgele: kopru onbellegiyle yanlis eslesme olasiligi dusuk
  bitir();
}

char *Baglanti::yuk_tamponu() {
  tx_n_ = tx_i_ = 0;  // yari yollanmis eski cerceve varsa BIRAK (yeni yuk tamponun uzerine yazilacak); kopru NAK'ler, yeniden denenir
  return fr_ + 3;
}

void Baglanti::yeni_istek(bool yeni_sira) {
  if (sonuc_ == CEVAP || sonuc_ == ZAMAN_ASIMI) {  // eski cevap (jeton icerebilir) tamponda kalmasin
    sil(rx_, sizeof rx_);
    rx_n_ = 0;
    rx_tasti_ = false;
  }
  if (yeni_sira) sira_++;
  deneme_ = 1;
  sonuc_ = BEKLIYOR;
  cevap_ = nullptr;
  gonder_bekliyor_ = true;
  gonderi_no_++;
}

void Baglanti::gonder() {
  yuk_n_ = (uint8_t)uzunluk(fr_ + 3);
  ist_.gonderilen++;
  yeni_istek(true);
}

bool Baglanti::yeniden() {
  if (yuk_n_ == 0) return false;
  ist_.yeniden++;
  yeni_istek(true);
  return true;
}

void Baglanti::bitir() {
  sil(fr_, sizeof fr_);
  yuk_n_ = 0;
  tx_n_ = tx_i_ = 0;
  sil(rx_, sizeof rx_);
  rx_n_ = 0;
  rx_tasti_ = false;
  cevap_ = nullptr;
  gonder_bekliyor_ = false;
  sonuc_ = BOS;
}

void Baglanti::tx_hazirla(uint32_t simdi) {
  size_t n = cerceve_yerinde(fr_, sizeof fr_, yuk_n_, sira_);
  if (n == 0) {  // yuk gecersiz (cok uzun / yazdirilamaz): bos yere bekletme, hemen hata ver
    zaman_asimina_dus();
    return;
  }
  tx_n_ = (uint8_t)n;
  tx_i_ = 0;
  gonder_bekliyor_ = false;
  son_gonderim_ = simdi;
}

void Baglanti::tx_pompala() {
  if (tx_i_ >= tx_n_) return;
  int bos = d_.seri_yazilabilir();
  if (bos <= 0) return;
  size_t kalan = (size_t)(tx_n_ - tx_i_);
  size_t n = kalan < (size_t)bos ? kalan : (size_t)bos;
  d_.seri_yaz(fr_ + tx_i_, n);
  tx_i_ = (uint8_t)(tx_i_ + n);
}

void Baglanti::zaman_asimina_dus() {
  sonuc_ = ZAMAN_ASIMI;
  ist_.zaman_asimi++;
  gonder_bekliyor_ = false;
  rx_n_ = 0;
  rx_tasti_ = false;
  kopyala(rx_, sizeof rx_, FM("HATA|Sunucu yanitsiz"));  // uygulama katmani tek bicim gorur
  cevap_ = rx_;
}

void Baglanti::hizli_yeniden() {
  if (sonuc_ == BEKLIYOR && !gonder_bekliyor_ && deneme_ < DENEME_SAYISI) {
    deneme_++;
    ist_.yeniden++;
    gonder_bekliyor_ = true;
  }
}

void Baglanti::satir_isle() {
  rx_[rx_n_] = 0;
  uint8_t sira;
  bool nak;
  char *yuk;
  if (!cerceve_coz(rx_, rx_n_, sira, nak, yuk)) {
    ist_.bozuk++;
    hizli_yeniden();  // cevap bozuldu: kopru onbellekten tekrar verir, yan etki iki kez olmaz
    rx_n_ = 0;
    return;
  }
  if (sonuc_ != BEKLIYOR) {
    ist_.yoksayilan++;
    rx_n_ = 0;
    return;
  }
  if (nak) {
    ist_.nak++;
    hizli_yeniden();
    rx_n_ = 0;
    return;
  }
  if (sira != sira_) {  // onceki (zaman asimina ugramis) istegin gec gelen cevabi
    ist_.yoksayilan++;
    rx_n_ = 0;
    return;
  }
  cevap_ = yuk;
  sonuc_ = CEVAP;
}

void Baglanti::oku() {
  for (uint8_t sinir = 0; sinir < 64; sinir++) {  // bir turda en fazla 64 bayt: dongu suresi sinirli
    int c = d_.seri_oku();
    if (c < 0) return;
    if (c == '\r') continue;
    if (c == '\n') {
      if (rx_tasti_) {
        ist_.bozuk++;
        hizli_yeniden();
        rx_tasti_ = false;
        rx_n_ = 0;
      } else if (rx_n_ > 0) {
        satir_isle();
        if (sonuc_ == CEVAP) return;  // rx_ cevabi tutuyor: tuketilene kadar okuma
      }
      continue;
    }
    if (rx_tasti_) continue;
    if (rx_n_ + 1 < RX_KAP) {
      rx_[rx_n_++] = (char)c;
    } else {
      rx_tasti_ = true;  // satir cok uzun: sonuna kadar at
    }
  }
}

void Baglanti::tick(uint32_t simdi) {
  if (sonuc_ == BOS || sonuc_ == BEKLIYOR) oku();

  if (sonuc_ == BEKLIYOR) {
    if (gonder_bekliyor_) {
      if (tx_i_ >= tx_n_) tx_hazirla(simdi);  // onceki cerceve tamamen gittiyse yenisini hazirla (ayni yuk, bu sira no)
    } else if ((uint32_t)(simdi - son_gonderim_) >= (uint32_t)DENEME_MS) {
      if (deneme_ >= DENEME_SAYISI) {
        zaman_asimina_dus();
      } else {
        deneme_++;
        ist_.yeniden++;
        gonder_bekliyor_ = true;
      }
    }
  }
  tx_pompala();
}

}  // namespace vx
