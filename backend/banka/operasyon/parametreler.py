"""Simülasyon parametreleri: doğrulama ve sınırlar (panelden gelen değerlere güvenilmez)."""
from dataclasses import asdict, dataclass, fields


class ParametreHatasi(ValueError):
    pass


# alan: (en_az, en_cok)
SINIRLAR = {
    "musteri_sayisi": (1, 500),
    "sure_sn": (1, 600),
    "zaman_olcegi": (0.0, 10.0),
    "isci_sayisi": (1, 32),
    "terminal_sayisi": (1, 10),
    "tohum": (0, 2 ** 31 - 1),
    "ortalama_islem": (0.5, 20.0),
    "hatali_pin_orani": (0.0, 0.5),
    "terminal_degistirme_orani": (0.0, 1.0),
    "onay_red_orani": (0.0, 1.0),
    "onay_gecikme_sn": (0.0, 120.0),
    "teslim_sorun_orani": (0.0, 1.0),
    "sikisma_orani": (0.0, 0.2),
}


@dataclass
class Parametreler:
    musteri_sayisi: int = 100          # sanal müşteri
    sure_sn: int = 30                  # SANAL süre: işlemler bu aralığa dağıtılır
    zaman_olcegi: float = 1.0          # gerçek sn = sanal sn x ölçek (1: gerçek zamanlı, 0: beklemeden en hızlı)
    isci_sayisi: int = 8               # eşzamanlı ATM oturumu (iş parçacığı)
    terminal_sayisi: int = 5
    tohum: int = 42                    # aynı tohum, aynı plan
    ortalama_islem: float = 4.0        # müşteri başına ortalama işlem
    # işlem karışımı (oran olarak; toplamı 1'e normalize edilir)
    cekme_agirligi: float = 0.50
    yatirma_agirligi: float = 0.25
    havale_agirligi: float = 0.15
    buyuk_cekme_agirligi: float = 0.10  # 10.000 TL üstü: onay gerektirir
    hatali_pin_orani: float = 0.02
    terminal_degistirme_orani: float = 0.03   # kart başka terminalde de kullanılır (risk faktörü)
    onay_red_orani: float = 0.10
    onay_gecikme_sn: float = 6.0       # yetkilinin ortalama tepki süresi (sanal sn, onay başına)
    teslim_sorun_orani: float = 0.03   # para alınmadı (ters kayıt)
    sikisma_orani: float = 0.0         # dağıtıcı sıkışması (terminali kapatır; varsayılan kapalı)
    gercek_hash: bool = False          # False: hızlı PIN hash'i (mantığı ölçer, hash maliyetini değil)

    @classmethod
    def al(cls, veri=None):
        """Sözlükten doğrulanmış Parametreler. Bilinmeyen alan ya da bozuk değer ParametreHatasi."""
        veri = dict(veri or {})
        adlar = {f.name: f for f in fields(cls)}
        bilinmeyen = set(veri) - set(adlar)
        if bilinmeyen:
            raise ParametreHatasi(f"Bilinmeyen parametre: {', '.join(sorted(bilinmeyen))}")
        sonuc = {}
        for ad, deger in veri.items():
            tip = type(getattr(cls, ad))
            try:
                if tip is bool:
                    deger = deger if isinstance(deger, bool) else str(deger).lower() in ("1", "true", "on", "evet")
                else:
                    deger = tip(deger)
            except (TypeError, ValueError):
                raise ParametreHatasi(f"Geçersiz değer: {ad}")
            if ad in SINIRLAR:
                en_az, en_cok = SINIRLAR[ad]
                if not (en_az <= deger <= en_cok):
                    raise ParametreHatasi(f"{ad}: {en_az} ile {en_cok} arasında olmalı")
            elif ad.endswith("_agirligi") and deger < 0:
                raise ParametreHatasi(f"{ad}: negatif olamaz")
            sonuc[ad] = deger
        p = cls(**sonuc)
        if p.agirlik_toplami() <= 0:
            raise ParametreHatasi("İşlem karışımının en az bir ağırlığı pozitif olmalı")
        return p

    def agirlik_toplami(self):
        return self.cekme_agirligi + self.yatirma_agirligi + self.havale_agirligi + self.buyuk_cekme_agirligi

    def sozluk(self):
        return asdict(self)
