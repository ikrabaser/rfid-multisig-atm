"""Bir işlemi, aynı hesabın GEÇMİŞİNE göre sayısal bir özellik vektörüne çevirir.

Saf fonksiyon: veritabanı, saat dilimi ya da global durum yoktur. Zamanlar zaten yerel saate çevrilmiş `datetime`'dır
(Olay.zaman); entegrasyon adımı bunu `timezone.localtime` ile yapar. Geçmiş, bu işlemden ÖNCE uygulanmış işlemlerdir.
"""
import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

GECMIS_EN_AZ = 3                       # risk.py TUTAR_EN_AZ_GECMIS ile aynı: bundan az geçmişte "olağan" tanımlanamaz
GECE_BASLANGIC, GECE_BITIS = 0, 5      # risk.py "gece" kuralı: 00:00-05:00
YOK_SURE_SN = 86400                    # geçmiş yoksa "son işlemden bu yana" bilinmez: tarafsız (tipik) bir süre, aykırı sayılmasın


@dataclass(frozen=True)
class Olay:
    """Bir para çekme ya da havale. `bakiye`: işlemden ÖNCEKİ hesap bakiyesi. `pin_hata`: son 30 dk'daki hatalı PIN sayısı."""
    zaman: datetime
    tutar: float
    tur: str                           # "CEKME" | "HAVALE"
    terminal: str
    hedef: Optional[str] = None        # havalede hedef hesap no
    bakiye: float = 0.0
    pin_hata: int = 0


# (kod, kullanıcıya gösterilen ad). Sıra, vektördeki sıradır.
OZELLIKLER = (
    ("tutar_orani", "tutarın olağan tutara oranı (log)"),
    ("bakiye_orani", "tutarın bakiyeye oranı"),
    ("saat_sapmasi", "olağan saatten sapma"),
    ("gece", "gece saati (00-05)"),
    ("islem_10dk", "son 10 dk işlem sayısı"),
    ("islem_1sa", "son 1 sa işlem sayısı"),
    ("son_islemden_sn", "son işlemden bu yana geçen süre (log)"),
    ("yeni_terminal", "ilk kez görülen terminal"),
    ("yeni_hedef", "ilk kez görülen havale hedefi"),
    ("pin_hata", "son hatalı PIN sayısı"),
    ("havale", "havale işlemi"),
)
OZELLIK_KODLARI = tuple(k for k, _ in OZELLIKLER)
OZELLIK_ADLARI = dict(OZELLIKLER)


def _saat(z):
    return z.hour + z.minute / 60.0


def _dairesel_fark(a, b):
    """24 saatlik düzlemde iki saat arasındaki en kısa uzaklık, saat cinsinden (0..12)."""
    d = abs(a - b) % 24.0
    return min(d, 24.0 - d)


def _dairesel_ortalama(saatler):
    """Saatlerin dairesel ortalaması (23:30 ile 00:30'un ortalaması 00:00'dır, 12:00 değil)."""
    sin = sum(math.sin(2 * math.pi * s / 24.0) for s in saatler)
    cos = sum(math.cos(2 * math.pi * s / 24.0) for s in saatler)
    return (math.atan2(sin, cos) * 24.0 / (2 * math.pi)) % 24.0


def ozellik_vektoru(olay: Olay, gecmis: Sequence[Olay]):
    """`gecmis`: aynı hesabın bu olaydan önceki olayları (zamana göre artan). Dönüş: OZELLIK_KODLARI sırasında float listesi."""
    n = len(gecmis)
    yeterli = n >= GECMIS_EN_AZ

    if yeterli:
        medyan = statistics.median(g.tutar for g in gecmis)
        tutar_orani = math.log1p(olay.tutar) - math.log1p(medyan)
        tipik_saat = _dairesel_ortalama([_saat(g.zaman) for g in gecmis])
        saat_sapmasi = _dairesel_fark(_saat(olay.zaman), tipik_saat) / 12.0
        yeni_terminal = 0.0 if any(g.terminal == olay.terminal for g in gecmis) else 1.0
    else:                                   # geçmişi az hesapta "olağan" bilinmez: nötr değerler (risk.py'deki yeni_hesap_buyuk ayrı)
        tutar_orani, saat_sapmasi, yeni_terminal = 0.0, 0.0, 0.0

    yeni_hedef = 0.0
    if olay.tur == "HAVALE" and olay.hedef is not None and yeterli:
        yeni_hedef = 0.0 if any(g.hedef == olay.hedef for g in gecmis) else 1.0

    t = olay.zaman
    on_dk = sum(1 for g in gecmis if 0 <= (t - g.zaman).total_seconds() <= 600)
    bir_sa = sum(1 for g in gecmis if 0 <= (t - g.zaman).total_seconds() <= 3600)
    son_sn = (t - gecmis[-1].zaman).total_seconds() if n else YOK_SURE_SN

    return [
        tutar_orani,
        min(olay.tutar / max(olay.bakiye, 1.0), 1.0),
        saat_sapmasi,
        1.0 if GECE_BASLANGIC <= t.hour < GECE_BITIS else 0.0,
        float(min(on_dk, 10)),
        float(min(bir_sa, 20)),
        math.log1p(max(son_sn, 0.0)),
        yeni_terminal,
        yeni_hedef,
        float(min(olay.pin_hata, 5)),
        1.0 if olay.tur == "HAVALE" else 0.0,
    ]
