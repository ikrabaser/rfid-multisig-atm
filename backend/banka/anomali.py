"""Denetim logu üzerinde basit kural tabanlı anomali tespiti.

Kuralların hepsi hash zincirli logdaki olaylara bakar. Gerçek sistemlerde bunun yerine
(ya da yanında) istatistiksel/ML skorlama olur; burada amaç, loğun neye yaradığını
göstermek ve kuralların ne kadar basit başlayabileceğini görmek.
"""
import datetime
import json
import math
from collections import defaultdict

from django.conf import settings
from django.utils import timezone

from .models import LogKaydi, Terminal

PENCERE_DK = 10
IMKANSIZ_SEYAHAT_MIN_KM = 1.0       # bundan yakin terminaller ayni sube sayilir
KONUMSUZ_KISA_SURE_SN = 120
UZUN_CEVRIMDISI_SN = 1800
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


def _mesafe_km(enlem1, boylam1, enlem2, boylam2):
    """İki nokta arasındaki kuş uçuşu mesafe (haversine), km."""
    r = 6371.0
    p1, p2 = math.radians(enlem1), math.radians(enlem2)
    dp, dl = p2 - p1, math.radians(boylam2 - boylam1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


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

    # 7) Imkansiz seyahat: ayni kimlik, kisa surede birbirine uzak iki terminalde
    terminaller = {t.kod: t for t in Terminal.objects.all()}
    son_gorulen = {}
    for k, v in kayitlar:
        terminal = v.get("terminal")
        if not terminal:
            continue
        if k.olay_turu == "giris":
            kimlik = f"kart:{v.get('uid', '?')}"
        elif k.olay_turu in ("onay_verildi", "kasa_acma_talebi"):
            kimlik = f"personel:{v.get('personel_id', v.get('baslatan_personel_id', '?'))}"
        else:
            continue
        onceki = son_gorulen.get(kimlik)
        if onceki and onceki[1] != terminal:
            saniye = max((k.zaman - onceki[0]).total_seconds(), 1.0)
            a, b = terminaller.get(onceki[1]), terminaller.get(terminal)
            if a and b and None not in (a.enlem, a.boylam, b.enlem, b.boylam):
                km = _mesafe_km(a.enlem, a.boylam, b.enlem, b.boylam)
                hiz = km / (saniye / 3600)
                if km >= IMKANSIZ_SEYAHAT_MIN_KM and hiz > settings.MAKS_SEYAHAT_HIZI_KMH:
                    bulgular.append(_bulgu(
                        "imkansiz_seyahat", "yuksek",
                        f"{kimlik}: {onceki[1]} ile {terminal} arası {km:.0f} km, {saniye / 60:.1f} dk "
                        f"(yaklaşık {hiz:.0f} km/sa)", kimlik=kimlik))
            elif saniye <= KONUMSUZ_KISA_SURE_SN:
                bulgular.append(_bulgu(
                    "farkli_terminal_kisa_surede", "orta",
                    f"{kimlik}: {saniye:.0f} sn arayla {onceki[1]} ve {terminal} terminallerinde "
                    f"(konum bilinmiyor)", kimlik=kimlik))
        son_gorulen[kimlik] = (k.zaman, terminal)

    # 8) Terminal uzun sure cevrimdisi kaldi (terminalin kendi bildirdigi sure)
    for k, v in kayitlar:
        if k.olay_turu == "terminal_gunlugu" and v.get("olay") == "cevrimdisi_bitti":
            try:
                sure = int(float(v.get("veri", {}).get("sure_sn", 0)))
            except (TypeError, ValueError):
                continue
            if sure >= UZUN_CEVRIMDISI_SN:
                bulgular.append(_bulgu(
                    "uzun_cevrimdisi", "orta",
                    f"Terminal {v.get('terminal', '?')} {sure // 60} dk çevrimdışı kaldı (log #{k.sira})",
                    sira=k.sira))

    bulgular.sort(key=lambda b: CIDDIYET_SIRASI[b["ciddiyet"]])
    return bulgular
