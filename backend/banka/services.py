"""Is mantigi: PIN, oturum, islem, politika, onay akisi.

Kural: Veritabanini degistiren her sey islem_*/onay_* fonksiyonlarindan gecer ve
denetim loguna (hash zinciri) yazilir. Hata (IslemHatasi) firlatilmadan ONCE
yazilan loglar kaybolmasin diye hatalar atomic bloklarin DISINDA firlatilir.
"""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .hashlog import kayit_ekle
from .models import (Hesap, Islem, Kart, Onay, OnayTalebi, Personel, Politika, Rol,
                     uid_duzelt)

TOKEN_TUZU = "banka.musteri-oturumu"


class IslemHatasi(Exception):
    def __init__(self, mesaj, kod="hata", **ekstra):
        super().__init__(mesaj)
        self.mesaj = mesaj
        self.kod = kod
        self.ekstra = ekstra


def simdi():
    """Testlerde zamani oynatabilmek icin tek noktadan."""
    return timezone.now()


def para(d):
    return f"{Decimal(d):.2f}"


# ------------------------------------------------------------------ kimlik / PIN

def kart_tipi(uid):
    """MUSTERI | PERSONEL | BLOKE | BILINMIYOR"""
    uid = uid_duzelt(uid)
    personel = Personel.objects.filter(kart_uid=uid, aktif=True).first()
    if personel:
        return "BLOKE" if personel.bloke else "PERSONEL"
    kart = Kart.objects.filter(uid=uid).first()
    if kart:
        return "BLOKE" if kart.bloke else "MUSTERI"
    kayit_ekle("bilinmeyen_kart", {"uid": uid})
    return "BILINMIYOR"


def _pin_kontrol(nesne, pin, kimlik):
    """Kart veya Personel icin PIN'i dener; sayac ve bloke mantigi burada.

    Atomic blogun DISINDA cagrilmali (hatali deneme sayaci geri alinmasin).
    """
    if nesne.bloke:
        kayit_ekle("bloke_kart_denemesi", {"kimlik": kimlik})
        raise IslemHatasi("Kart bloke edildi", "bloke")
    if nesne.pin_dogru_mu(pin):
        if nesne.hatali_deneme:
            nesne.hatali_deneme = 0
            nesne.save(update_fields=["hatali_deneme"])
        return
    nesne.hatali_deneme += 1
    kalan = max(0, settings.MAKS_PIN_DENEME - nesne.hatali_deneme)
    if kalan == 0:
        nesne.bloke = True
    nesne.save(update_fields=["hatali_deneme", "bloke"])
    kayit_ekle("pin_hatali", {"kimlik": kimlik, "kalan_hak": kalan, "bloke": nesne.bloke})
    if nesne.bloke:
        raise IslemHatasi("PIN hatalı, kart bloke edildi", "bloke", kalan_hak=0)
    raise IslemHatasi(f"Yanlış PIN, kalan hak: {kalan}", "pin", kalan_hak=kalan)


def musteri_giris(uid, pin):
    uid = uid_duzelt(uid)
    kart = Kart.objects.select_related("hesap__musteri").filter(uid=uid).first()
    if kart is None:
        kayit_ekle("bilinmeyen_kart", {"uid": uid})
        raise IslemHatasi("Kart tanımlı değil", "bilinmiyor")
    _pin_kontrol(kart, pin, uid)
    kayit_ekle("giris", {"uid": uid, "hesap_no": kart.hesap.hesap_no})
    token = signing.TimestampSigner(salt=TOKEN_TUZU).sign(str(kart.id))
    return {"token": token, "ad": kart.hesap.musteri.ad, "hesap_no": kart.hesap.hesap_no}


def _token_coz(token):
    try:
        deger = signing.TimestampSigner(salt=TOKEN_TUZU).unsign(
            token or "", max_age=settings.OTURUM_SURESI_SN
        )
    except signing.BadSignature:  # SignatureExpired de buna dahil
        raise IslemHatasi("Oturum süresi doldu", "oturum")
    kart = Kart.objects.select_related("hesap").filter(id=int(deger)).first()
    if kart is None or kart.bloke:
        raise IslemHatasi("Oturum geçersiz", "oturum")
    return kart


def personel_dogrula(uid, pin):
    uid = uid_duzelt(uid)
    personel = Personel.objects.filter(kart_uid=uid, aktif=True).first()
    if personel is None:
        kayit_ekle("yetkisiz_onay_denemesi", {"uid": uid})
        raise IslemHatasi("Yetkili kartı değil", "yetkisiz")
    _pin_kontrol(personel, pin, uid)
    return personel


# ------------------------------------------------------------------ bakiye / islem

def bakiye_sorgula(token):
    kart = _token_coz(token)
    kayit_ekle("bakiye_sorgu", {"hesap_no": kart.hesap.hesap_no})
    kart.hesap.refresh_from_db()
    return {"bakiye": para(kart.hesap.bakiye)}


def _tutar_coz(tutar):
    try:
        t = Decimal(str(tutar)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise IslemHatasi("Geçersiz tutar", "tutar")
    if not t.is_finite() or t <= 0 or t > Decimal("100000000"):
        raise IslemHatasi("Geçersiz tutar", "tutar")
    return t


def mesai_disi_mi(an=None):
    yerel = timezone.localtime(an or simdi())
    if yerel.weekday() >= 5:
        return True
    return not (settings.MESAI_BASLANGIC <= yerel.hour < settings.MESAI_BITIS)


def gerekli_onay(tutar, an=None):
    """(gereken_onay, en_az_rol) dondurur. Politika bulunamazsa en siki kural."""
    politika = (
        Politika.objects.filter(alt_limit__lt=tutar)
        .filter(Q(ust_limit__isnull=True) | Q(ust_limit__gte=tutar))
        .order_by("-alt_limit")
        .first()
    )
    if politika is None:
        gereken, en_az = 3, int(Rol.MUDUR)  # yapilandirma eksikse kapali kal
    else:
        gereken, en_az = politika.gereken_onay, int(politika.en_az_rol)

    if gereken == 0:
        return 0, en_az

    aktif = Personel.objects.filter(aktif=True, bloke=False)
    aktif_sayi = aktif.count()
    if gereken > aktif_sayi:
        raise IslemHatasi("Yeterli aktif yetkili yok", "yetkili_yok")

    if settings.MESAI_DISI_KURALI and mesai_disi_mi(an):
        gereken = min(gereken + 1, max(gereken, aktif_sayi))
        en_az = min(int(Rol.MUDUR), en_az + 1)

    if not aktif.filter(rol__gte=en_az).exists():
        raise IslemHatasi("Gerekli rolde yetkili yok", "yetkili_yok")
    return gereken, en_az


def islem_baslat(token, tur, tutar, hedef_hesap_no=None):
    kart = _token_coz(token)
    hesap = kart.hesap
    tur = (tur or "").upper()
    if tur not in Islem.Tur.values:
        raise IslemHatasi("Geçersiz işlem türü", "tur")
    tutar = _tutar_coz(tutar)

    hedef = None
    if tur == Islem.Tur.HAVALE:
        hedef = Hesap.objects.filter(hesap_no=(hedef_hesap_no or "").strip()).first()
        if hedef is None:
            raise IslemHatasi("Hedef hesap bulunamadı", "hedef")
        if hedef.id == hesap.id:
            raise IslemHatasi("Kendi hesabınıza havale olmaz", "hedef")

    if tur in (Islem.Tur.CEKME, Islem.Tur.HAVALE):
        hesap.refresh_from_db()
        if hesap.bakiye < tutar:
            kayit_ekle("islem_reddi_bakiye", {"hesap_no": hesap.hesap_no, "tur": tur, "tutar": para(tutar)})
            raise IslemHatasi("Yetersiz bakiye", "bakiye")
        gereken, en_az = gerekli_onay(tutar)
    else:
        gereken, en_az = 0, int(Rol.GOREVLI)

    with transaction.atomic():
        islem = Islem.objects.create(
            hesap=hesap, hedef_hesap=hedef, tur=tur, tutar=tutar, baslatan_uid=kart.uid
        )
        if gereken == 0:
            _uygula(islem)
            hesap.refresh_from_db()
            return {"durum": "uygulandi", "islem_id": islem.id, "bakiye": para(hesap.bakiye),
                    "basarili": islem.durum == Islem.Durum.UYGULANDI}

        talep = OnayTalebi.objects.create(
            islem=islem,
            gereken_onay=gereken,
            en_az_rol=en_az,
            son_tarih=simdi() + timedelta(seconds=settings.ONAY_PENCERESI_SN),
        )
        islem.durum = Islem.Durum.ONAY_BEKLIYOR
        islem.save(update_fields=["durum"])
        kayit_ekle("talep_acildi", {
            "talep_id": talep.id, "islem_id": islem.id, "tur": tur, "tutar": para(tutar),
            "hesap_no": hesap.hesap_no, "gereken_onay": gereken, "en_az_rol": en_az,
        })
    return {"durum": "onay_bekliyor", "talep_id": talep.id, "islem_id": islem.id, "gereken": gereken}


def _uygula(islem):
    """Bakiyeleri degistirir. transaction.atomic() ICINDE cagrilmali."""
    idler = sorted({i for i in (islem.hesap_id, islem.hedef_hesap_id) if i})
    kilitli = {h.id: h for h in Hesap.objects.select_for_update().filter(id__in=idler).order_by("id")}
    kaynak = kilitli[islem.hesap_id]

    if islem.tur in (Islem.Tur.CEKME, Islem.Tur.HAVALE):
        if kaynak.bakiye < islem.tutar:
            islem.durum = Islem.Durum.BASARISIZ
            islem.tamamlanma = simdi()
            islem.save(update_fields=["durum", "tamamlanma"])
            kayit_ekle("islem_basarisiz", {"islem_id": islem.id, "sebep": "yetersiz bakiye"})
            return False
        kaynak.bakiye -= islem.tutar
        if islem.tur == Islem.Tur.HAVALE:
            hedef = kilitli[islem.hedef_hesap_id]
            hedef.bakiye += islem.tutar
            hedef.save(update_fields=["bakiye"])
    else:
        kaynak.bakiye += islem.tutar
    kaynak.save(update_fields=["bakiye"])

    islem.durum = Islem.Durum.UYGULANDI
    islem.tamamlanma = simdi()
    islem.save(update_fields=["durum", "tamamlanma"])
    kayit_ekle("islem_uygulandi", {
        "islem_id": islem.id, "tur": islem.tur, "tutar": para(islem.tutar),
        "hesap_no": kaynak.hesap_no, "yeni_bakiye": para(kaynak.bakiye),
        "hedef_hesap_id": islem.hedef_hesap_id,
    })
    return True


# ------------------------------------------------------------------ onay akisi

def _suresi_dolduysa_kapat(talep):
    """Talep kilitliyken (atomic icinde) cagrilir. Kapattiysa True."""
    if talep.durum == OnayTalebi.Durum.BEKLIYOR and simdi() > talep.son_tarih:
        talep.durum = OnayTalebi.Durum.SURESI_DOLDU
        talep.save(update_fields=["durum"])
        talep.islem.durum = Islem.Durum.SURESI_DOLDU
        talep.islem.tamamlanma = simdi()
        talep.islem.save(update_fields=["durum", "tamamlanma"])
        kayit_ekle("talep_suresi_doldu", {"talep_id": talep.id, "islem_id": talep.islem_id})
        return True
    return False


def _ozet(talep):
    oylar = list(talep.onaylar.filter(karar=Onay.Karar.ONAY).select_related("personel"))
    verilen = len(oylar)
    en_yuksek = max((o.personel.rol for o in oylar), default=0)
    kalan = 0
    if talep.durum == OnayTalebi.Durum.BEKLIYOR:
        kalan = max(0, int((talep.son_tarih - simdi()).total_seconds()))
    return {
        "talep_id": talep.id,
        "islem_id": talep.islem_id,
        "tur": talep.islem.tur,
        "tutar": para(talep.islem.tutar),
        "durum": talep.durum,
        "verilen": verilen,
        "gereken": talep.gereken_onay,
        "kalan_sn": kalan,
        "ust_yetkili_gerekli": verilen >= talep.gereken_onay and en_yuksek < talep.en_az_rol
        and talep.durum == OnayTalebi.Durum.BEKLIYOR,
    }


def _talep_no(talep_id):
    try:
        return int(talep_id)
    except (TypeError, ValueError):
        raise IslemHatasi("Talep bulunamadı", "yok")


def talep_durumu(talep_id):
    talep_id = _talep_no(talep_id)
    with transaction.atomic():
        talep = (OnayTalebi.objects.select_for_update().select_related("islem")
                 .filter(id=talep_id).first())
        if talep is None:
            raise IslemHatasi("Talep bulunamadı", "yok")
        _suresi_dolduysa_kapat(talep)
        return _ozet(talep)


def suresi_dolanlari_kapat():
    for talep_id in OnayTalebi.objects.filter(
        durum=OnayTalebi.Durum.BEKLIYOR, son_tarih__lt=simdi()
    ).values_list("id", flat=True):
        talep_durumu(talep_id)


def bekleyen_talep():
    """En eski, suresi dolmamis talebi dondurur; yoksa None."""
    suresi_dolanlari_kapat()
    talep = (OnayTalebi.objects.select_related("islem")
             .filter(durum=OnayTalebi.Durum.BEKLIYOR).order_by("id").first())
    return _ozet(talep) if talep else None


def onay_karar(talep_id, uid, pin, karar):
    """karar: 'ONAY' veya 'RED'. Sonuc ozet sozlugu; kural ihlali IslemHatasi."""
    karar = Onay.Karar.RED if str(karar).upper() in ("RED", "H") else Onay.Karar.ONAY
    talep_id = _talep_no(talep_id)
    personel = personel_dogrula(uid, pin)  # atomic disinda: hatali PIN sayaci kalici olsun
    hata = None
    with transaction.atomic():
        talep = (OnayTalebi.objects.select_for_update().select_related("islem")
                 .filter(id=talep_id).first())
        if talep is None:
            raise IslemHatasi("Talep bulunamadı", "yok")

        if not _suresi_dolduysa_kapat(talep) and talep.durum == OnayTalebi.Durum.BEKLIYOR:
            islem = talep.islem
            if Onay.objects.filter(talep=talep, personel=personel).exists():
                kayit_ekle("onay_reddi_tekrar_oy", {"talep_id": talep.id, "personel_id": personel.id})
                hata = ("Bu yetkili zaten oy kullandı", "tekrar")
            elif karar == Onay.Karar.ONAY and islem.baslatan_uid == personel.kart_uid:
                kayit_ekle("onay_reddi_kendi_islemi", {"talep_id": talep.id, "personel_id": personel.id})
                hata = ("Kendi işlemini onaylayamaz", "kendi_islemi")
            elif karar == Onay.Karar.RED:
                Onay.objects.create(talep=talep, personel=personel, karar=karar)
                talep.durum = OnayTalebi.Durum.REDDEDILDI
                talep.save(update_fields=["durum"])
                islem.durum = Islem.Durum.REDDEDILDI
                islem.tamamlanma = simdi()
                islem.save(update_fields=["durum", "tamamlanma"])
                kayit_ekle("talep_reddedildi", {"talep_id": talep.id, "personel_id": personel.id})
            else:
                Onay.objects.create(talep=talep, personel=personel, karar=karar)
                kayit_ekle("onay_verildi", {"talep_id": talep.id, "personel_id": personel.id,
                                            "rol": personel.rol})
                oylar = list(talep.onaylar.filter(karar=Onay.Karar.ONAY).select_related("personel"))
                en_yuksek = max(o.personel.rol for o in oylar)
                if len(oylar) >= talep.gereken_onay and en_yuksek >= talep.en_az_rol:
                    talep.durum = OnayTalebi.Durum.ONAYLANDI
                    talep.save(update_fields=["durum"])
                    kayit_ekle("talep_onaylandi", {"talep_id": talep.id, "onay_sayisi": len(oylar)})
                    basarili = _uygula(islem)
                    talep.durum = OnayTalebi.Durum.UYGULANDI if basarili else OnayTalebi.Durum.BASARISIZ
                    talep.save(update_fields=["durum"])
        ozet = _ozet(talep)

    if hata:
        raise IslemHatasi(hata[0], hata[1], **ozet)
    return ozet
