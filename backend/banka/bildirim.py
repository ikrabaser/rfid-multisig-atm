"""Yetkililere bildirim: e-posta (Django e-posta altyapısı) ve SMS (simülasyon).

Bildirim hataları iş akışını ASLA bozmaz: her şey try/except içinde, hata Bildirim tablosuna
"BASARISIZ" olarak yazılır. Bildirimler işlem commit edildikten SONRA gönderilir.

E-posta varsayılan olarak konsola yazar (DJANGO_EMAIL_BACKEND ile gerçek SMTP'ye geçilir).
SMS için gerçek bir sağlayıcı yok; kayıt tutulur ve loga yazılır.
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .hashlog import kanonik_json, kayit_ekle
from .models import Bildirim, Islem, LogKaydi, Personel, Rol

log = logging.getLogger("banka.bildirim")
ANOMALI_TEKRAR_SAAT = 6


def tl(tutar):
    """60000 -> '60.000,00 TL'"""
    s = f"{Decimal(tutar):,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".") + " TL"


def _gonder(personel, olay, konu, metin, talep_id=None):
    if personel.eposta:
        try:
            send_mail(konu, metin, settings.DEFAULT_FROM_EMAIL, [personel.eposta])
            durum, hata = Bildirim.Durum.GONDERILDI, ""
        except Exception as e:  # bildirim hatasi islemi bozmamali
            log.warning("E-posta gonderilemedi (%s): %s", personel.eposta, e)
            durum, hata = Bildirim.Durum.BASARISIZ, str(e)[:200]
        Bildirim.objects.create(
            kanal=Bildirim.Kanal.EPOSTA, durum=durum, alici=personel.ad, adres=personel.eposta,
            konu=konu[:120], metin=metin, olay=olay, talep_id=talep_id, hata=hata,
        )
    if personel.telefon:
        kisa = metin if len(metin) <= 160 else metin[:157] + "..."
        log.info("SMS (simulasyon) -> %s: %s", personel.telefon, kisa)
        Bildirim.objects.create(
            kanal=Bildirim.Kanal.SMS, durum=Bildirim.Durum.SIMULASYON, alici=personel.ad,
            adres=personel.telefon, konu=konu[:120], metin=kisa, olay=olay, talep_id=talep_id,
        )


def yetkililere_bildir(olay, konu, metin, talep_id=None, en_az_rol=0, haric_uid=None):
    """Aktif ve bloke olmayan yetkililere (isteğe bağlı rol şartı) bildirir. Kaç kişiye gittiğini döndürür."""
    try:
        adaylar = Personel.objects.filter(aktif=True, bloke=False, rol__gte=int(en_az_rol))
        if haric_uid:
            adaylar = adaylar.exclude(kart_uid=haric_uid)
        sayi = 0
        for p in adaylar:
            _gonder(p, olay, konu, metin, talep_id)
            sayi += 1
        return sayi
    except Exception:
        log.exception("Bildirim gonderimi basarisiz (olay=%s)", olay)
        return 0


def talep_acildi(talep):
    """Onay talebi açılınca, talebi başlatan hariç tüm yetkililere."""
    islem = talep.islem
    rol_adi = Rol(talep.en_az_rol).label
    son = settings.ONAY_PENCERESI_SN
    if islem.tur == Islem.Tur.KASA_ACMA:
        konu = f"Kasa dairesi açma talebi #{talep.id}"
        metin = (f"Kasa dairesi açma talebi #{talep.id}. {talep.gereken_onay} onay gerekli "
                 f"(en az biri: {rol_adi}). {son} sn içinde terminalde kartınızı okutun.")
    else:
        konu = f"Onay bekleyen işlem #{talep.id}"
        metin = (f"{islem.get_tur_display()} {tl(islem.tutar)}, hesap {islem.hesap.hesap_no}. "
                 f"{talep.gereken_onay} onay gerekli (en az biri: {rol_adi}). "
                 f"{son} sn içinde terminalde kartınızı okutun.")
    return yetkililere_bildir("talep_acildi", konu, metin, talep.id, haric_uid=islem.baslatan_uid)


def kasa_acildi(islem):
    """Kasa dairesi açılınca müdür yardımcısı ve üstüne bilgi."""
    konu = "Kasa dairesi açıldı"
    metin = f"Kasa dairesi açıldı (işlem #{islem.id}, terminal {islem.terminal or '-'})."
    return yetkililere_bildir("kasa_acildi", konu, metin, en_az_rol=Rol.MUDUR_YRD)


def anomali_bildir(bulgular):
    """Yüksek ciddiyetteki bulguları müdür yardımcısı ve üstüne bildirir; aynı bulgu 6 saat içinde tekrarlanmaz."""
    gonderilen = 0
    sinir = timezone.now() - timedelta(hours=ANOMALI_TEKRAR_SAAT)
    for b in bulgular:
        if b["ciddiyet"] != "yuksek":
            continue
        veri = {"kural": b["kural"], "aciklama": b["aciklama"]}
        if LogKaydi.objects.filter(olay_turu="anomali_bildirimi", zaman__gte=sinir,
                                   veri=kanonik_json(veri)).exists():
            continue
        konu = f"Anomali: {b['kural']}"
        gonderilen += yetkililere_bildir("anomali", konu, b["aciklama"], en_az_rol=Rol.MUDUR_YRD)
        kayit_ekle("anomali_bildirimi", veri)
    return gonderilen
