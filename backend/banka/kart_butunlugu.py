"""Müşteri kartlarının bütünlüğü (kart BAŞINA doğrulama).

Veritabanına doğrudan sahte bir kart eklenir (bilinen PIN'li), bir kartın PIN'i değiştirilir ya da başka bir hesaba
taşınırsa, o hesabın parası çalınabilir. Bu yüzden her ONAYLI kart değişikliğinde kartların listesi (UID, hesap no,
PIN hash'inin özeti) hash zincirine yazılır. Terminalde bir kart okutulduğunda ya da giriş yapıldığında kartın satırı
bu onaylı listede aranır: yoksa o kart doğrulanamaz ("bloke" gibi davranır). Yetkililerden farkı: yalnızca ilgili kart
kapanır, diğer müşteriler etkilenmez (hizmet reddine yol açmaz).

Özete girmeyenler: bloke ve hatalı deneme sayacı (sistem kendi günceller).
"""
import hashlib
import json
from datetime import timedelta

from django.utils import timezone

from .hashlog import kanonik_json, kayit_ekle
from .models import Kart, LogKaydi


def _pin_ozeti(kart):
    return hashlib.sha256(kart.pin_hash.encode("utf-8")).hexdigest()


def satirlar():
    return sorted([k.uid, k.hesap.hesap_no, _pin_ozeti(k)] for k in Kart.objects.select_related("hesap"))


def ozet():
    return hashlib.sha256(kanonik_json(satirlar()).encode("utf-8")).hexdigest()


def goruntu_kaydet(neden, **ek):
    """Şu anki kart listesini 'onaylı sürüm' olarak hash zincirine yazar."""
    liste = satirlar()
    return kayit_ekle("kart_goruntusu", {
        "ozet": hashlib.sha256(kanonik_json(liste).encode("utf-8")).hexdigest(),
        "satirlar": liste, "adet": len(liste), "neden": neden, **ek,
    })


def son_goruntu():
    """(ozet, satirlar) ya da onaylı sürüm yoksa None."""
    kayit = LogKaydi.objects.filter(olay_turu="kart_goruntusu").order_by("-sira").first()
    if kayit is None:
        return None
    try:
        veri = json.loads(kayit.veri)
        return veri["ozet"], veri["satirlar"]
    except (ValueError, KeyError):
        return None


def kart_dogrulandi_mi(kart):
    """True: kartın satırı onaylı listede. False: yok ya da farklı. None: onaylı sürüm yok."""
    onayli = son_goruntu()
    if onayli is None:
        return None
    return [kart.uid, kart.hesap.hesap_no, _pin_ozeti(kart)] in onayli[1]


def tutarli_mi():
    """Tüm küme son onaylı sürümle aynı mı? (mutabakat için). None: onaylı sürüm yok."""
    onayli = son_goruntu()
    if onayli is None:
        return None
    return onayli[0] == ozet()


def durum():
    return {None: "baslangic_yok", True: "tutarli", False: "tutarsiz"}[tutarli_mi()]


def ihlal_kaydet(kart):
    """Onaysız kart değişikliği tespit edildi: kart başına saatte bir log."""
    sinir = timezone.now() - timedelta(hours=1)
    if LogKaydi.objects.filter(olay_turu="kart_butunluk_ihlali", zaman__gte=sinir,
                               veri__contains=f'"uid":"{kart.uid}"').exists():
        return
    kayit_ekle("kart_butunluk_ihlali", {"uid": kart.uid, "hesap_no": kart.hesap.hesap_no})
