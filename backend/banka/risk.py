"""Satır içi risk motoru: şüpheli işlemi UYGULANMADAN önce puanlar ve karar verir.

`anomali.py` olaylara sonradan bakar ve yalnızca rapor eder. Bu modül ise para çekme ve havale anında, hesap satırı ve zincir
kilidi ALTINDA çalışır ve işlemi durdurabilir:

    puan <  RISK_EK_ONAY_ESIGI            -> GEC      (normal akış)
    puan >= RISK_EK_ONAY_ESIGI            -> EK_ONAY  (politika ne derse desin en az RISK_EK_ONAY_SAYISI yetkili onayı)
    puan >= RISK_RED_ESIGI                -> RED      (işlem reddedilir)
    puan >= RISK_BLOKE_ESIGI              -> BLOKE    (reddedilir ve kart bloke edilir; kaldırma maker-checker ile)

Puan, bağımsız faktörlerin toplamıdır; her faktörün kodu ve gerekçesi işleme ve hash zincirine yazılır, yani karar açıklanabilir.
Faktörler (puan):
    pin_hatali           son 30 dk'da bu kartta hatalı PIN: her biri 15 (en çok 45)
    imkansiz_seyahat     kart son 30 dk'da uzak bir terminalde de kullanıldı (hız eşiği aşılıyor): 60
    farkli_terminal      kart son 10 dk'da başka terminalde de kullanıldı (konum yok ya da hız makul): 25
    olagan_disi_tutar    tutar hesabın 90 günlük ortalamasının 3 katından ve geçmiş en büyüğünden fazla (en az 3 işlem): 30
    yeni_hesap_buyuk     geçmişi az (3'ten az işlem) hesapta RISK_YENI_HESAP_TUTAR ve üstü: 15
    islem_hizi           hesapta son 10 dk'da 3+ (20) ya da 5+ (35) çekme/havale
    gece                 00:00-05:00 arası: 10
    kart_imzasi          son 24 saatte bu kartta imza ihlali (klon/tekrar/imzasız): 40

Dürüst sınırlar: kurallar ve eşikler elle seçilmiştir, gerçek dolandırıcılık verisiyle ayarlanmamıştır. Yanlış pozitif müşteriyi
engelleyebilir; bu yüzden önce "ek onay" basamağı vardır, bloke yalnızca birkaç güçlü sinyal birleşince olur. Eşikler ayarlardan
gelir (Politika tablosunda değil); ML katmanı ayrı bir adımdır ve bu skoru değiştirmez, yanına sinyal ekler.
"""
import datetime
import json
import math

from django.conf import settings
from django.db.models import Avg, Count, Max
from django.utils import timezone

from .hashlog import kayit_ekle
from .models import Islem, LogKaydi, Personel, Terminal

PIN_PENCERE_DK = 30
PIN_PUANI, PIN_EN_COK = 15, 45
SEYAHAT_PENCERE_DK = 30
FARKLI_TERMINAL_DK = 10
FARKLI_TERMINAL_PUANI = 25
IMKANSIZ_SEYAHAT_PUANI = 60
IMKANSIZ_SEYAHAT_MIN_KM = 1.0
TUTAR_PENCERE_GUN = 90
TUTAR_EN_AZ_GECMIS = 3
TUTAR_KATI = 3
TUTAR_PUANI = 30
YENI_HESAP_PUANI = 15
HIZ_PENCERE_DK = 10
HIZ_ESIKLERI = ((5, 35), (3, 20))             # (önceki işlem sayısı, puan)
GECE_PUANI = 10
IMZA_PUANI = 40
IMZA_OLAYLARI = ("kart_imza_gecersiz", "kart_tekrar_oynatma", "kart_imzasiz")


def _simdi():
    from .services import simdi  # testlerde simdi() oynatilabilsin
    return simdi()


def _mesafe_km(e1, b1, e2, b2):
    r = 6371.0
    p1, p2 = math.radians(e1), math.radians(e2)
    dp, dl = p2 - p1, math.radians(b2 - b1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _json(veri):
    try:
        return json.loads(veri)
    except ValueError:
        return {}


def faktorler(kart, hesap, tutar, an=None):
    """[(kod, puan, açıklama)] listesi. Veritabanını yalnızca okur."""
    an = an or _simdi()
    uid = kart.uid
    sonuc = []

    # --- hatali PIN
    hatali = LogKaydi.objects.filter(
        olay_turu="pin_hatali", veri__contains=f'"kimlik":"{uid}"',
        zaman__gte=an - datetime.timedelta(minutes=PIN_PENCERE_DK), zaman__lte=an).count()
    if hatali:
        sonuc.append(("pin_hatali", min(PIN_EN_COK, hatali * PIN_PUANI), f"{hatali} hatalı PIN ({PIN_PENCERE_DK} dk)"))

    # --- ayni kart, baska terminal
    girisler = list(LogKaydi.objects.filter(
        olay_turu="giris", veri__contains=f'"uid":"{uid}"',
        zaman__gte=an - datetime.timedelta(minutes=SEYAHAT_PENCERE_DK), zaman__lte=an,
    ).order_by("-sira")[:20])
    if girisler:
        simdiki = _json(girisler[0].veri).get("terminal")
        onceki = None
        if simdiki:
            for k in girisler[1:]:
                t = _json(k.veri).get("terminal")
                if t and t != simdiki:
                    onceki = (k.zaman, t)
                    break
        if onceki:
            saniye = max((girisler[0].zaman - onceki[0]).total_seconds(), 1.0)
            terminaller = {t.kod: t for t in Terminal.objects.filter(kod__in=[simdiki, onceki[1]])}
            a, b = terminaller.get(onceki[1]), terminaller.get(simdiki)
            imkansiz = False
            if a and b and None not in (a.enlem, a.boylam, b.enlem, b.boylam):
                km = _mesafe_km(a.enlem, a.boylam, b.enlem, b.boylam)
                hiz = km / (saniye / 3600)
                if km >= IMKANSIZ_SEYAHAT_MIN_KM and hiz > settings.MAKS_SEYAHAT_HIZI_KMH:
                    imkansiz = True
                    sonuc.append(("imkansiz_seyahat", IMKANSIZ_SEYAHAT_PUANI,
                                  f"{onceki[1]} -> {simdiki}: {km:.0f} km, {saniye / 60:.1f} dk"))
            if not imkansiz and saniye <= FARKLI_TERMINAL_DK * 60:
                sonuc.append(("farkli_terminal", FARKLI_TERMINAL_PUANI,
                              f"{onceki[1]} ve {simdiki}, {saniye / 60:.1f} dk arayla"))

    # --- alisilmadik tutar / yeni hesapta buyuk tutar
    gecmis = Islem.objects.filter(
        hesap=hesap, tur__in=[Islem.Tur.CEKME, Islem.Tur.HAVALE], durum=Islem.Durum.UYGULANDI,
        olusturma__gte=an - datetime.timedelta(days=TUTAR_PENCERE_GUN),
    ).aggregate(n=Count("id"), ort=Avg("tutar"), en_buyuk=Max("tutar"))
    if gecmis["n"] >= TUTAR_EN_AZ_GECMIS:
        if tutar > TUTAR_KATI * gecmis["ort"] and tutar > gecmis["en_buyuk"]:
            sonuc.append(("olagan_disi_tutar", TUTAR_PUANI,
                          f"{tutar:.2f} TL, ortalama {gecmis['ort']:.2f} TL"))
    elif tutar >= settings.RISK_YENI_HESAP_TUTAR:
        sonuc.append(("yeni_hesap_buyuk", YENI_HESAP_PUANI, f"geçmişi az hesapta {tutar:.2f} TL"))

    # --- islem hizi
    son = Islem.objects.filter(
        hesap=hesap, tur__in=[Islem.Tur.CEKME, Islem.Tur.HAVALE],
        olusturma__gte=an - datetime.timedelta(minutes=HIZ_PENCERE_DK),
    ).exclude(durum=Islem.Durum.BASARISIZ).count()
    for esik, puan in HIZ_ESIKLERI:
        if son >= esik:
            sonuc.append(("islem_hizi", puan, f"{son} işlem ({HIZ_PENCERE_DK} dk)"))
            break

    # --- gece
    if 0 <= timezone.localtime(an).hour < 5:
        sonuc.append(("gece", GECE_PUANI, "00:00-05:00"))

    # --- kart imzasi
    imza = LogKaydi.objects.filter(
        olay_turu__in=IMZA_OLAYLARI, veri__contains=f'"uid":"{uid}"',
        zaman__gte=an - datetime.timedelta(hours=24), zaman__lte=an).count()
    if imza:
        sonuc.append(("kart_imzasi", IMZA_PUANI, f"{imza} imza ihlali (24 sa)"))
    return sonuc


def karar_ver(puan):
    if puan >= settings.RISK_BLOKE_ESIGI:
        return "BLOKE"
    if puan >= settings.RISK_RED_ESIGI:
        return "RED"
    if puan >= settings.RISK_EK_ONAY_ESIGI:
        return "EK_ONAY"
    return "GEC"


def kontrol(kart, hesap, tur, tutar, gereken, en_az):
    """islem_baslat'ta, hesap satiri ve zincir kilidi ALTINDA, politika kontrollerinden sonra cagrilir.

    (hata, gereken, en_az, sonuc) dondurur. hata None ya da (mesaj, kod, ekstra); cagiran hatayi atomic bloktan
    CIKTIKTAN sonra firlatir (log ve bloke geri alinmasin). sonuc: {"skor", "karar", "nedenler", "not"} ya da None (motor kapali).
    """
    if not settings.RISK_MOTORU_AKTIF:
        return None, gereken, en_az, None
    liste = faktorler(kart, hesap, tutar)
    puan = sum(p for _, p, _ in liste)
    karar = karar_ver(puan)
    sonuc = {"skor": puan, "karar": karar, "nedenler": [k for k, _, _ in liste],
             "not": "; ".join(f"{k}: {a}" for k, _, a in liste)[:200]}
    if karar == "GEC":
        return None, gereken, en_az, sonuc

    ortak = {"hesap_no": hesap.hesap_no, "uid": kart.uid, "tur": tur, "tutar": f"{tutar:.2f}",
             "skor": puan, "karar": karar, "nedenler": sonuc["nedenler"]}
    if karar == "EK_ONAY":
        gerekli = int(settings.RISK_EK_ONAY_SAYISI)
        if Personel.objects.filter(aktif=True, bloke=False).count() < gerekli:
            kayit_ekle("risk_karari", {**ortak, "karar": "RED", "sebep": "ek onay icin yetkili yok"})
            sonuc["karar"] = "RED"
            return ("Güvenlik: işlem yapılamıyor", "risk", {}), gereken, en_az, sonuc
        kayit_ekle("risk_karari", ortak)
        return None, max(gereken, gerekli), en_az, sonuc

    kayit_ekle("risk_karari", ortak)
    if karar == "BLOKE":
        type(kart).objects.filter(pk=kart.pk).update(bloke=True)
        # bloke_butunlugu.py bu olaydan beklenen durumu turetir: veritabani kurcalanip bloke kaldirilsa bile kart kapali kalir
        kayit_ekle("risk_bloke", {"kimlik": kart.uid, "bloke": True, "skor": puan})
        return ("Güvenlik: kart bloke edildi", "bloke", {}), gereken, en_az, sonuc
    return ("Güvenlik nedeniyle reddedildi", "risk", {}), gereken, en_az, sonuc
