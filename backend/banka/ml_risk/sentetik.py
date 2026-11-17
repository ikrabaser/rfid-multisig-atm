"""Sentetik müşteri trafiği ve etiketli saldırı senaryoları (tohumlu, tekrarlanabilir).

Gerçek dolandırıcılık verisi yoktur; bu üretici bilinçli olarak basittir. Normal trafikte de GERÇEKÇİ HATALAR vardır (yılda birkaç büyük
alışveriş, yolculukta yeni terminal, yeni alıcıya havale, ardışık çekim, hatalı PIN), yoksa yanlış alarm oranı gerçekçi çıkmaz.
Etiketler yalnızca ÖLÇÜM içindir; model eğitimde etiket görmez.

Saldırı türleri (etiket):
    klon_hizli         klonlanmış kartla görülmemiş terminalde birkaç dakikada 4-8 çekim
    gece_buyuk_havale  gece saatinde, yeni alıcıya, olağanın 3-8 katı havale
    bosaltma           yeni alıcıya bakiyenin %70-95'i havale (saat sıradan)
    pin_deneme         3-5 hatalı PIN'den sonra görülmemiş terminalde olağanın 2-4 katı çekim
    yavas_kucuk        ZOR: görülmemiş terminalde olağana yakın tek çekim, olağan saatte (neredeyse sıradan)
"""
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from .ozellikler import Olay

SALDIRI_TURLERI = ("klon_hizli", "gece_buyuk_havale", "bosaltma", "pin_deneme", "yavas_kucuk")
TERMINALLER = [f"ATM-{i:02d}" for i in range(1, 9)]
BASLANGIC = datetime(2026, 1, 5, 0, 0)


@dataclass
class Musteri:
    no: str
    tipik_tutar: float
    tipik_saat: float
    gunluk_islem: float
    ana_terminal: str
    ikinci_terminal: str
    alicilar: list
    bakiye: float


@dataclass(frozen=True)
class EtiketliOlay:
    musteri: str
    olay: Olay
    etiket: str                 # "normal" ya da saldırı türü
    saldiri_no: int = 0         # aynı saldırıya ait olayları bağlar (0: normal)


def musteri_uret(rng, n):
    musteriler = []
    for i in range(n):
        terminaller = rng.sample(TERMINALLER, 2)
        musteriler.append(Musteri(
            no=f"M{i:04d}",
            tipik_tutar=math.exp(rng.gauss(math.log(500), 0.7)),          # çoğu 250-1000 TL, bir kısmı birkaç bin
            tipik_saat=min(max(rng.gauss(14.0, 3.0), 8.0), 21.0),
            gunluk_islem=rng.uniform(0.25, 1.2),
            ana_terminal=terminaller[0], ikinci_terminal=terminaller[1],
            alicilar=[f"H{i:04d}-{k}" for k in range(rng.randint(2, 4))],
            bakiye=math.exp(rng.gauss(math.log(40000), 0.8)),
        ))
    return musteriler


def _saat_zamani(gun, saat):
    return BASLANGIC + timedelta(days=gun, hours=saat)


def _normal_olay(rng, m, zaman):
    """Müşterinin sıradan bir işlemi; küçük bir olasılıkla meşru ama sıra dışı (gerçek dünya gürültüsü)."""
    tutar = m.tipik_tutar * math.exp(rng.gauss(0, 0.35))
    if rng.random() < 0.03:
        tutar *= rng.uniform(3.0, 6.0)                                    # büyük alışveriş
    terminal = m.ana_terminal if rng.random() < 0.8 else m.ikinci_terminal
    if rng.random() < 0.03:
        terminal = rng.choice(TERMINALLER)                                # yolculuk
    havale = rng.random() < 0.15
    hedef = None
    if havale:
        hedef = m.alicilar[rng.randrange(len(m.alicilar))]
        if rng.random() < 0.04:
            hedef = f"YENI-{rng.randrange(10**6)}"                        # meşru yeni alıcı
    pin_hata = 0 if rng.random() < 0.97 else rng.randint(1, 2)
    return Olay(zaman, round(tutar, 2), "HAVALE" if havale else "CEKME", terminal, hedef,
                round(m.bakiye * rng.uniform(0.7, 1.3), 2), pin_hata)


def normal_akis(rng, m, gun_bas, gun_say):
    olaylar = []
    for gun in range(gun_bas, gun_bas + gun_say):
        n = 0
        p = m.gunluk_islem
        while rng.random() < p and n < 4:                                 # günlük işlem sayısı, geometrik
            saat = min(max(rng.gauss(m.tipik_saat, 2.0), 7.0), 22.5)
            olaylar.append(_normal_olay(rng, m, _saat_zamani(gun, saat)))
            n += 1
            p *= 0.5
        if rng.random() < 0.03:                                           # meşru ardışık çekim (limit nedeniyle bölünmüş)
            t0 = _saat_zamani(gun, min(max(rng.gauss(m.tipik_saat, 2.0), 7.0), 22.0))
            for k in range(rng.randint(2, 3)):
                o = _normal_olay(rng, m, t0 + timedelta(minutes=2 * k))
                olaylar.append(Olay(o.zaman, o.tutar, "CEKME", m.ana_terminal, None, o.bakiye, 0))
    olaylar.sort(key=lambda o: o.zaman)
    return olaylar


def gorulmemis_terminal(rng, m):
    return rng.choice([t for t in TERMINALLER if t not in (m.ana_terminal, m.ikinci_terminal)])


def saldiri_uret(rng, tur, m, gun):
    """Bir müşteriye karşı tek saldırı: Olay listesi (zamana göre)."""
    yeni_alici = f"DOLANDIRICI-{rng.randrange(10**6)}"
    bakiye = round(m.bakiye * rng.uniform(0.7, 1.3), 2)
    if tur == "klon_hizli":
        t0 = _saat_zamani(gun, rng.uniform(9.0, 22.0))
        terminal = gorulmemis_terminal(rng, m)
        return [Olay(t0 + timedelta(minutes=rng.uniform(0.5, 1.2) * k * 1.0), round(m.tipik_tutar * rng.uniform(0.6, 1.6), 2),
                     "CEKME", terminal, None, bakiye, 0) for k in range(rng.randint(4, 8))]
    if tur == "gece_buyuk_havale":
        t = _saat_zamani(gun, rng.uniform(1.0, 4.5))
        return [Olay(t, round(m.tipik_tutar * rng.uniform(3.0, 8.0), 2), "HAVALE", m.ana_terminal, yeni_alici, bakiye, 0)]
    if tur == "bosaltma":
        t = _saat_zamani(gun, rng.uniform(10.0, 20.0))
        return [Olay(t, round(bakiye * rng.uniform(0.70, 0.95), 2), "HAVALE", m.ana_terminal, yeni_alici, bakiye, 0)]
    if tur == "pin_deneme":
        t = _saat_zamani(gun, rng.uniform(9.0, 22.0))
        return [Olay(t, round(m.tipik_tutar * rng.uniform(2.0, 4.0), 2), "CEKME", gorulmemis_terminal(rng, m), None, bakiye,
                     rng.randint(3, 5))]
    if tur == "yavas_kucuk":
        t = _saat_zamani(gun, min(max(rng.gauss(m.tipik_saat, 1.0), 8.0), 21.0))
        return [Olay(t, round(m.tipik_tutar * rng.uniform(1.0, 1.6), 2), "CEKME", gorulmemis_terminal(rng, m), None, bakiye, 0)]
    raise ValueError(tur)


def veri_uret(tohum=1, musteri=200, egitim_gun=60, test_gun=15, saldiri=60):
    """(egitim, test): iki `EtiketliOlay` listesi (zamana göre sıralı, tüm müşteriler birlikte).
    Eğitim yalnızca normal trafiktir; test, normal trafik + `saldiri` adet saldırı (türlere eşit dağıtılmış) içerir."""
    rng = random.Random(tohum)
    musteriler = musteri_uret(rng, musteri)
    egitim, test = [], []
    for m in musteriler:
        egitim += [EtiketliOlay(m.no, o, "normal") for o in normal_akis(rng, m, 0, egitim_gun)]
        test += [EtiketliOlay(m.no, o, "normal") for o in normal_akis(rng, m, egitim_gun, test_gun)]
    for k in range(saldiri):
        tur = SALDIRI_TURLERI[k % len(SALDIRI_TURLERI)]
        m = musteriler[rng.randrange(len(musteriler))]
        gun = egitim_gun + rng.randrange(test_gun)
        for o in saldiri_uret(rng, tur, m, gun):
            test.append(EtiketliOlay(m.no, o, tur, k + 1))
    egitim.sort(key=lambda e: e.olay.zaman)
    test.sort(key=lambda e: e.olay.zaman)
    return egitim, test
