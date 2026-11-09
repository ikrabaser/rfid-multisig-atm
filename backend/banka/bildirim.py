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

from . import sms
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


def _eposta_kullaniciya(alici, adres, olay, konu, metin):
    try:
        send_mail(konu, metin, settings.DEFAULT_FROM_EMAIL, [adres])
        durum, hata = Bildirim.Durum.GONDERILDI, ""
    except Exception as e:
        log.warning("E-posta gonderilemedi (%s): %s", adres, e)
        durum, hata = Bildirim.Durum.BASARISIZ, str(e)[:200]
    Bildirim.objects.create(
        kanal=Bildirim.Kanal.EPOSTA, durum=durum, alici=alici, adres=adres,
        konu=konu[:120], metin=metin, olay=olay, hata=hata,
    )


def politika_degisikligi_acildi(talep):
    """Politika değişikliği talebi: onay yetkisi olan yönetim kullanıcılarına (talep sahibi hariç) e-posta."""
    try:
        from django.contrib.auth import get_user_model
        konu = f"Politika değişikliği talebi #{talep.id}"
        metin = (f"{talep.talep_eden.get_username()} politika değişikliği talep etti "
                 f"({talep.get_islem_display()}). Gerekçe: {talep.gerekce[:200]} "
                 f"Yönetim panelinde inceleyip onaylayın: /admin/banka/politikadegisikligi/")
        sayi = 0
        for u in get_user_model().objects.filter(is_active=True, is_staff=True).exclude(id=talep.talep_eden_id):
            if u.email and u.has_perm("banka.politika_onayla"):
                _eposta_kullaniciya(u.get_username(), u.email, "politika_talebi", konu, metin)
                sayi += 1
        return sayi
    except Exception:
        log.exception("Politika talebi bildirimi basarisiz")
        return 0


def ters_kayit(ters):
    """Para teslim edilmediği için işlem ters kaydedildi: müdür yardımcısı ve üstüne bilgi."""
    orijinal = ters.ters_islem
    konu = f"Ters kayıt: işlem #{orijinal.id}"
    if ters.tutar != orijinal.tutar:  # eksik verme: yalnizca fark iade edildi
        metin = (f"İşlem #{orijinal.id} ({tl(orijinal.tutar)}, hesap {orijinal.hesap.hesap_no}) eksik verildi; "
                 f"fark {tl(ters.tutar)} iade (ters kayıt) edildi. Neden: {ters.ters_neden}. "
                 f"Terminal: {orijinal.terminal or '-'}.")
    else:
        metin = (f"İşlem #{orijinal.id} ({tl(orijinal.tutar)}, hesap {orijinal.hesap.hesap_no}) ters kaydedildi. "
                 f"Neden: {ters.ters_neden}. Terminal: {orijinal.terminal or '-'}.")
    return yetkililere_bildir("ters_kayit", konu, metin, en_az_rol=Rol.MUDUR_YRD)


def dagitici_arizasi(islem, neden):
    """Sıkışma / eksik verme: para dağıtıcı kapandı, teknik servis gerekir. Müdür yardımcısı ve üstüne bilgi."""
    terminal = islem.terminal or "-"
    konu = f"Para dağıtıcı kapandı: {terminal}"
    metin = (f"{terminal} terminalinde işlem #{islem.id} ({tl(islem.tutar)}) için dağıtıcı sorunu bildirildi: {neden}. "
             f"Nakit dağıtımı kapatıldı. Teknik servis kontrolünden sonra "
             f"'python manage.py dagitici_ac' ile açılır; kasetler sayılmalıdır.")
    return yetkililere_bildir("dagitici_arizasi", konu, metin, en_az_rol=Rol.MUDUR_YRD)


def yonetim_talebi_acildi(talep):
    """Yönetim talebi: onay yetkisi olan kullanıcılara (talep sahibi hariç) e-posta."""
    try:
        from django.contrib.auth import get_user_model
        konu = f"Yönetim talebi #{talep.id}"
        metin = (f"{talep.talep_eden.get_username()} yönetim işlemi talep etti: {talep.ozet}. "
                 f"{talep.gereken_onay} onay gerekli. Gerekçe: {talep.gerekce[:200]} "
                 f"Yönetim panelinde inceleyin: /admin/banka/yonetimtalebi/")
        sayi = 0
        for u in get_user_model().objects.filter(is_active=True, is_staff=True).exclude(id=talep.talep_eden_id):
            if u.email and u.has_perm("banka.yonetim_onayla"):
                _eposta_kullaniciya(u.get_username(), u.email, "yonetim_talebi", konu, metin)
                sayi += 1
        return sayi
    except Exception:
        log.exception("Yonetim talebi bildirimi basarisiz")
        return 0


def pin_ilet(personel, pin, neden):
    """Yeni PIN'i alıcının (yetkili ya da müşteri) KENDİ telefonuna SMS ile iletir.

    PIN yalnızca SMS sağlayıcısına verilir (bkz. sms.py); log'a, veritabanına, panele ve e-postaya YAZILMAZ.
    Simülasyonda sağlayıcı, telefonu temsil eden ayrı bir dosyaya yazar: `python manage.py sms_kutusu`.
    """
    try:
        sms.gonder(personel.telefon, f"{neden}. Yeni PIN'iniz: {pin}")
        log.info("PIN SMS ile gonderildi (icerik gizli) -> %s", personel.telefon)
        Bildirim.objects.create(
            kanal=Bildirim.Kanal.SMS, durum=Bildirim.Durum.SIMULASYON, alici=personel.ad, adres=personel.telefon,
            konu="Yeni PIN iletildi", metin=f"{neden}. Yeni PIN SMS ile iletildi (içerik kayıtlarda tutulmaz).",
            olay="pin_iletildi",
        )
    except Exception:
        log.exception("PIN iletimi kaydedilemedi")
