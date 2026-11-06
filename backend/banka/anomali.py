"""Denetim logu üzerinde basit kural tabanlı anomali tespiti.

Kuralların hepsi hash zincirli logdaki olaylara bakar. Gerçek sistemlerde bunun yerine
(ya da yanında) istatistiksel/ML skorlama olur; burada amaç, loğun neye yaradığını
göstermek ve kuralların ne kadar basit başlayabileceğini görmek.
"""
import datetime
import json
from collections import defaultdict

from django.utils import timezone

from .models import LogKaydi

PENCERE_DK = 10
CIDDIYET_SIRASI = {"yuksek": 0, "orta": 1, "dusuk": 2}


def _simdi():
    from .services import simdi  # testlerde simdi() oynatilabilsin
    return simdi()


def _en_cok_olay(zamanlar, dakika=PENCERE_DK):
    """Sıralı zaman listesinde `dakika` penceresine sığan en çok olay sayısı."""
    en_cok, j = 0, 0
    pencere = datetime.timedelta(minutes=dakika)
    for i, t in enumerate(zamanlar):
        while t - zamanlar[j] > pencere:
            j += 1
        en_cok = max(en_cok, i - j + 1)
    return en_cok


def _bulgu(kural, ciddiyet, aciklama, **ek):
    return {"kural": kural, "ciddiyet": ciddiyet, "aciklama": aciklama, **ek}


def tara(saat=24, an=None):
    """Son `saat` saatin loglarını tarar, bulguların listesini döndürür (ciddiyete göre sıralı)."""
    an = an or _simdi()
    baslangic = an - datetime.timedelta(hours=saat)
    kayitlar = []
    for k in LogKaydi.objects.filter(zaman__gte=baslangic, zaman__lte=an).order_by("zaman", "sira"):
        try:
            veri = json.loads(k.veri)
        except ValueError:
            veri = {}
        kayitlar.append((k, veri))

    bulgular = []

    # 1) Aynı kimlikte kısa sürede çok hatalı PIN (kaba kuvvet denemesi)
    pin_kimlik = defaultdict(list)
    tum_pin = []
    for k, v in kayitlar:
        if k.olay_turu == "pin_hatali":
            pin_kimlik[v.get("kimlik", "?")].append(k.zaman)
            tum_pin.append((k.zaman, v.get("kimlik", "?")))
    for kimlik, zamanlar in pin_kimlik.items():
        n = _en_cok_olay(zamanlar)
        if n >= 3:
            bulgular.append(_bulgu(
                "pin_yagmuru", "yuksek",
                f"{kimlik} kimliğinde {PENCERE_DK} dk içinde {n} hatalı PIN denemesi", kimlik=kimlik))

    # 2) Farklı kimliklere dağılmış hatalı PIN denemeleri (tarama / sızma)
    pencere = datetime.timedelta(minutes=PENCERE_DK)
    en_iyi = None
    j = 0
    for i, (t, _) in enumerate(tum_pin):
        while t - tum_pin[j][0] > pencere:
            j += 1
        kimlikler = {x[1] for x in tum_pin[j:i + 1]}
        if i - j + 1 >= 5 and len(kimlikler) >= 3:
            en_iyi = (i - j + 1, len(kimlikler))
    if en_iyi:
        bulgular.append(_bulgu(
            "dagitik_pin", "yuksek",
            f"{PENCERE_DK} dk içinde {en_iyi[1]} farklı kimlikte toplam {en_iyi[0]} hatalı PIN denemesi"))

    # 3) Aynı bilinmeyen kartın tekrar tekrar denenmesi
    bilinmeyen = defaultdict(list)
    for k, v in kayitlar:
        if k.olay_turu == "bilinmeyen_kart":
            bilinmeyen[v.get("uid", "?")].append(k.zaman)
    for uid, zamanlar in bilinmeyen.items():
        n = _en_cok_olay(zamanlar)
        if n >= 3:
            bulgular.append(_bulgu(
                "bilinmeyen_kart_tekrar", "orta",
                f"Tanımsız kart {uid} {PENCERE_DK} dk içinde {n} kez okutuldu", uid=uid))

    # 4) Bloke kartla ısrarlı deneme
    bloke = defaultdict(int)
    for k, v in kayitlar:
        if k.olay_turu == "bloke_kart_denemesi":
            bloke[v.get("kimlik", "?")] += 1
    for kimlik, n in bloke.items():
        if n >= 2:
            bulgular.append(_bulgu(
                "bloke_kart_denemesi", "orta", f"Bloke kimlik {kimlik} ile {n} deneme", kimlik=kimlik))

    # 5) Kısa sürede arka arkaya çok sayıda büyük işlem talebi (aynı hesap)
    talepler = defaultdict(list)
    for k, v in kayitlar:
        if k.olay_turu == "talep_acildi":
            talepler[v.get("hesap_no", "?")].append(k.zaman)
    for hesap_no, zamanlar in talepler.items():
        n = _en_cok_olay(zamanlar)
        if n >= 3:
            bulgular.append(_bulgu(
                "hizli_talep", "orta",
                f"Hesap {hesap_no} için {PENCERE_DK} dk içinde {n} onay talebi", hesap_no=hesap_no))

    # 6) Gece yarısı ile 05:00 arasında yetkili işlemi (onay, kasa açma)
    for k, v in kayitlar:
        if k.olay_turu in ("onay_verildi", "kasa_acma_talebi", "kasa_acildi"):
            yerel = timezone.localtime(k.zaman)
            if yerel.hour < 5:
                ciddiyet = "yuksek" if k.olay_turu == "kasa_acildi" else "orta"
                bulgular.append(_bulgu(
                    "gece_yetkili_islemi", ciddiyet,
                    f"Gece {yerel:%H:%M} saatinde '{k.olay_turu}' (log #{k.sira})", sira=k.sira))

    bulgular.sort(key=lambda b: CIDDIYET_SIRASI[b["ciddiyet"]])
    return bulgular
