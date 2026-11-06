"""Hash zincirli denetim kaydi.

Her kayit sunu icerir:  hash = HMAC-SHA256(anahtar, onceki_hash | sira | zaman | olay | veri)

Bir kaydi degistirirsen o kayittan sonrasinin hash'leri tutmaz, zincir bozulur.
Dikkat: Zincirin KENDISI "kimse kaydi sessizce degistirmedi" garantisini verir;
zincirin SON kayitlarini silmek ise tek basina fark edilmez. Bunun icin son hash'i
disariya yaz (log_dogrula --demir) ve sonra --beklenen-son-hash ile karsilastir.
"""
import datetime
import hashlib
import hmac
import json

from django.conf import settings
from django.db import transaction
from django.utils import timezone

GENESIS = "0" * 64


def kanonik_json(veri):
    return json.dumps(veri, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def hash_hesapla(onceki_hash, sira, zaman, olay_turu, veri_json):
    zaman = zaman.astimezone(datetime.timezone.utc)
    mesaj = "|".join([onceki_hash, str(sira), zaman.isoformat(), olay_turu, veri_json])
    anahtar = settings.LOG_HMAC_ANAHTARI.encode("utf-8")
    return hmac.new(anahtar, mesaj.encode("utf-8"), hashlib.sha256).hexdigest()


def kayit_ekle(olay_turu, veri):
    from .models import LogKaydi

    with transaction.atomic():
        son = LogKaydi.objects.select_for_update().order_by("-sira").first()
        sira = 1 if son is None else son.sira + 1
        onceki = GENESIS if son is None else son.hash
        zaman = timezone.now().astimezone(datetime.timezone.utc)
        veri_json = kanonik_json(veri)
        return LogKaydi.objects.create(
            sira=sira,
            zaman=zaman,
            olay_turu=olay_turu,
            veri=veri_json,
            onceki_hash=onceki,
            hash=hash_hesapla(onceki, sira, zaman, olay_turu, veri_json),
        )


def zinciri_dogrula():
    """Zinciri bastan sona kontrol eder. Sozluk dondurur."""
    from .models import LogKaydi

    onceki = GENESIS
    beklenen = 1
    sayi = 0
    for k in LogKaydi.objects.order_by("sira").iterator():
        if k.sira != beklenen:
            return {"saglam": False, "bozuk_sira": beklenen, "kayit_sayisi": sayi,
                    "sebep": "Eksik kayıt: sıra numarası atlandı"}
        if k.onceki_hash != onceki:
            return {"saglam": False, "bozuk_sira": k.sira, "kayit_sayisi": sayi,
                    "sebep": "Önceki hash uyuşmuyor"}
        if hash_hesapla(k.onceki_hash, k.sira, k.zaman, k.olay_turu, k.veri) != k.hash:
            return {"saglam": False, "bozuk_sira": k.sira, "kayit_sayisi": sayi,
                    "sebep": "Hash uyuşmuyor: kayıt değiştirilmiş"}
        onceki = k.hash
        beklenen += 1
        sayi += 1
    return {"saglam": True, "kayit_sayisi": sayi, "son_hash": onceki}
