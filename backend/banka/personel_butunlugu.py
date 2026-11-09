"""Yetkili (Personel) kayıtlarının bütünlüğü.

N-of-M onayın güvencesi, onaylayanların kim olduğuna bağlıdır. Veritabanına doğrudan sahte bir yetkili
eklenir, rolü yükseltilir ya da PIN'i değiştirilirse bütün düzen boşa gider. Bu yüzden (politikada olduğu gibi):
her ONAYLI değişiklikte yetkili kümesinin özeti hash zincirine yazılır; küme bu görüntüden farklıysa onaylar
ve kasa açma talepleri KAPALI kalır (`butunluk` hatası) ve mutabakat hata verir.

Özete girenler: kart UID, rol, aktiflik ve PIN hash'inin özeti. Girmeyenler: bloke/hatalı deneme (sistem kendi
günceller), ad, e-posta, telefon (güvenlik açısından belirleyici değil).
"""
import hashlib
import json
from datetime import timedelta

from django.utils import timezone

from .hashlog import kanonik_json, kayit_ekle
from .models import LogKaydi, Personel


def satirlar():
    return sorted(
        [p.kart_uid, int(p.rol), bool(p.aktif), hashlib.sha256(p.pin_hash.encode("utf-8")).hexdigest()]
        for p in Personel.objects.all()
    )


def ozet():
    return hashlib.sha256(kanonik_json(satirlar()).encode("utf-8")).hexdigest()


def goruntu_kaydet(neden, **ek):
    """Şu anki yetkili kümesini 'onaylı sürüm' olarak hash zincirine yazar."""
    return kayit_ekle("personel_goruntusu", {"ozet": ozet(), "adet": Personel.objects.count(), "neden": neden, **ek})


def son_onayli_ozet():
    kayit = LogKaydi.objects.filter(olay_turu="personel_goruntusu").order_by("-sira").first()
    if kayit is None:
        return None
    try:
        return json.loads(kayit.veri)["ozet"]
    except (ValueError, KeyError):
        return None


def tutarli_mi():
    """True: küme son onaylı sürümle aynı. False: farklı (onaysız değişiklik). None: onaylı sürüm yok."""
    onayli = son_onayli_ozet()
    if onayli is None:
        return None
    return onayli == ozet()


def durum():
    return {None: "baslangic_yok", True: "tutarli", False: "tutarsiz"}[tutarli_mi()]


def ihlal_kaydet():
    """Onaysız değişiklik tespit edildi: loga yaz (saatte bir, log şişmesin)."""
    sinir = timezone.now() - timedelta(hours=1)
    if LogKaydi.objects.filter(olay_turu="personel_butunluk_ihlali", zaman__gte=sinir).exists():
        return
    kayit_ekle("personel_butunluk_ihlali", {"beklenen_ozet": son_onayli_ozet(), "mevcut_ozet": ozet()})
