"""Bloke durumunun bütünlüğü: beklenen durum, tamper-evident log'dan türetilir.

Tehdit: Bloke kaldırma artık yalnızca onaylı talep ile yapılıyor, ama veritabanına doğrudan müdahale eden biri `bloke`
alanını sıfırlayıp kilitli bir kartı/yetkiliyi açabilir (ya da tersine, meşru bir kullanıcıyı bloke edebilir).

Çözüm: `bloke` alanına GÜVENİLMEZ; beklenen durum hash zincirli log'dan hesaplanır:
  - Sistem, 3. yanlış PIN'de `pin_hatali` olayını `bloke: true` ile yazar            -> bloke
  - Risk motoru yüksek puanda `risk_bloke` olayını `bloke: true` ile yazar            -> bloke
  - Onaylı BLOKE_KALDIR talebi: `yonetim_talebi` (hedef: bloke:kart:ID) ve
    `yonetim_talebi_uygulandi` (aynı talep no) olayları                               -> bloke kalktı
Bir kimliğin beklenen durumu, bu olaylardan SON olanın sonucudur. Beklenen durum "bloke" iken veritabanı "bloke değil"
diyorsa, kimlik yine bloke sayılır (kapalı kal) ve `bloke_butunluk_ihlali` loga yazılır. Mutabakat her iki yöndeki sapmayı
raporlar. Yetki: yalnızca log zincirini üretebilen (HMAC anahtarını bilen) taraf beklenen durumu değiştirebilir.
"""
import json
from datetime import timedelta

from django.utils import timezone

from .hashlog import kayit_ekle
from .models import Kart, LogKaydi, Personel


def _json(veri):
    try:
        return json.loads(veri)
    except ValueError:
        return {}


def beklenen_bloke_mu(hedef_turu, nesne_id, uid):
    """Log'a göre bu kimlik şu an bloke mi olmalı? hedef_turu: 'kart' ya da 'personel'."""
    olaylar = []  # (sira, bloke_mu)
    for sira, veri in LogKaydi.objects.filter(
        olay_turu="pin_hatali", veri__contains=f'"kimlik":"{uid}"'
    ).values_list("sira", "veri"):
        if _json(veri).get("bloke") is True:
            olaylar.append((sira, True))
    for sira, veri in LogKaydi.objects.filter(          # risk motoru kartı bloke etti (risk.py)
        olay_turu="risk_bloke", veri__contains=f'"kimlik":"{uid}"'
    ).values_list("sira", "veri"):
        if _json(veri).get("bloke") is True:
            olaylar.append((sira, True))

    talepler = set()
    anahtar = f"bloke:{hedef_turu}:{nesne_id}"
    for veri in LogKaydi.objects.filter(
        olay_turu="yonetim_talebi", veri__contains=f'"hedef":"{anahtar}"'
    ).values_list("veri", flat=True):
        v = _json(veri)
        if v.get("tur") == "BLOKE_KALDIR":
            talepler.add(v.get("talep_id"))
    if talepler:
        for sira, veri in LogKaydi.objects.filter(olay_turu="yonetim_talebi_uygulandi").values_list("sira", "veri"):
            if _json(veri).get("talep_id") in talepler:
                olaylar.append((sira, False))
    return sorted(olaylar)[-1][1] if olaylar else False


def sapmalar():
    """Veritabanındaki bloke alanı ile log'dan beklenen durum uyuşmayan kimlikler."""
    sonuc = []
    for k in Kart.objects.all():
        beklenen = beklenen_bloke_mu("kart", k.id, k.uid)
        if beklenen != k.bloke:
            sonuc.append({"tur": "kart", "uid": k.uid, "veritabani": k.bloke, "beklenen": beklenen})
    for p in Personel.objects.all():
        beklenen = beklenen_bloke_mu("personel", p.id, p.kart_uid)
        if beklenen != p.bloke:
            sonuc.append({"tur": "personel", "uid": p.kart_uid, "veritabani": p.bloke, "beklenen": beklenen})
    return sonuc


def ihlal_kaydet(kimlik, yon):
    """yon: 'kaldirilmis' (log bloke diyor, veritabanı değil) ya da 'eklenmis'. Kimlik başına saatte bir."""
    sinir = timezone.now() - timedelta(hours=1)
    if LogKaydi.objects.filter(olay_turu="bloke_butunluk_ihlali", zaman__gte=sinir,
                               veri__contains=f'"kimlik":"{kimlik}"').exists():
        return
    kayit_ekle("bloke_butunluk_ihlali", {"kimlik": kimlik, "yon": yon})
