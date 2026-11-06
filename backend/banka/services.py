"""Is mantigi: PIN, oturum, islem, politika, onay akisi, defter, kasa acma.

Kural: Veritabanini degistiren her sey bu modulden gecer ve denetim loguna (hash zinciri)
yazilir. Hata (IslemHatasi) firlatilmadan ONCE yazilan loglar kaybolmasin diye hatalar
atomic bloklarin DISINDA firlatilir.
"""
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core import signing
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from . import baglam, bildirim, ledger
from .hashlog import kayit_ekle
from .models import (Hesap, HesapHareketi, Islem, Kart, Onay, OnayTalebi, Personel, Politika,
                     Rol, uid_duzelt)

TOKEN_TUZU = "banka.musteri-oturumu"
MUSTERI_ISLEMLERI = {"CEKME", "YATIRMA", "HAVALE"}
ANAHTAR_DESENI = re.compile(r"[A-Za-z0-9_-]{1,40}")


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
    """Tutarı çözer. Virgül ya da nokta ondalık ayracı olabilir; en fazla 2 ondalık hane (kuruş)."""
    try:
        ham = Decimal(str(tutar).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        raise IslemHatasi("Geçersiz tutar", "tutar")
    if not ham.is_finite() or ham <= 0 or ham > Decimal("100000000"):
        raise IslemHatasi("Geçersiz tutar", "tutar")
    if ham.as_tuple().exponent < -2:
        raise IslemHatasi("En fazla 2 ondalık hane", "tutar")
    return ham.quantize(Decimal("0.01"))


def _anahtar_coz(anahtar):
    if anahtar in (None, ""):
        return None
    anahtar = str(anahtar).strip()
    if not ANAHTAR_DESENI.fullmatch(anahtar):
        raise IslemHatasi("Geçersiz işlem anahtarı", "anahtar")
    return anahtar


def _kasa_hesabi():
    kasa = Hesap.objects.filter(tur=Hesap.Tur.KASA).first()
    if kasa is None:
        raise IslemHatasi("Kasa hesabı tanımlı değil", "kasa")
    return kasa


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


def gunluk_toplam(hesap, an=None):
    """Bugun (yerel saat) bu hesaptan cikan ya da cikmak uzere bekleyen cekme/havale toplami."""
    yerel = timezone.localtime(an or simdi())
    gun_basi = yerel.replace(hour=0, minute=0, second=0, microsecond=0)
    toplam = Islem.objects.filter(
        hesap=hesap,
        tur__in=[Islem.Tur.CEKME, Islem.Tur.HAVALE],
        durum__in=[Islem.Durum.UYGULANDI, Islem.Durum.ONAY_BEKLIYOR],
        olusturma__gte=gun_basi,
    ).aggregate(t=Sum("tutar"))["t"]
    return toplam or Decimal("0.00")


def _islem_sonucu(islem, tekrar=False):
    """Mevcut bir islemin durumunu, islem_baslat cevabi bicimine cevirir."""
    if islem.durum == Islem.Durum.ONAY_BEKLIYOR:
        talep_durumu(islem.talep.id)  # suresi dolduysa burada kapanir
        islem.refresh_from_db()
    if islem.durum in (Islem.Durum.UYGULANDI, Islem.Durum.BASARISIZ):
        hesap = Hesap.objects.get(id=islem.hesap_id)
        return {"durum": "uygulandi", "islem_id": islem.id, "bakiye": para(hesap.bakiye),
                "basarili": islem.durum == Islem.Durum.UYGULANDI, "tekrar": tekrar}
    if islem.durum == Islem.Durum.ONAY_BEKLIYOR:
        return {"durum": "onay_bekliyor", "talep_id": islem.talep.id, "islem_id": islem.id,
                "gereken": islem.talep.gereken_onay, "tekrar": tekrar}
    if islem.durum == Islem.Durum.REDDEDILDI:
        raise IslemHatasi("İşlem reddedildi", "reddedildi")
    if islem.durum == Islem.Durum.SURESI_DOLDU:
        raise IslemHatasi("Onay süresi doldu", "sure")
    raise IslemHatasi("İşlem durumu belirsiz", "durum")


def _tekrar_istegi(islem, tur, tutar, hedef_hesap_no):
    """Ayni idempotency anahtariyla gelen istek: ayni islemse eski sonucu dondur."""
    ayni_hedef = (
        (islem.hedef_hesap.hesap_no if islem.hedef_hesap_id else "")
        == (hedef_hesap_no or "").strip()
    )
    if islem.tur != tur or islem.tutar != tutar or not ayni_hedef:
        kayit_ekle("anahtar_cakismasi", {"islem_id": islem.id, "anahtar": islem.anahtar})
        raise IslemHatasi("Aynı anahtar farklı işlemle kullanılamaz", "anahtar")
    kayit_ekle("tekrar_istek", {"islem_id": islem.id, "anahtar": islem.anahtar})
    return _islem_sonucu(islem, tekrar=True)


def islem_baslat(token, tur, tutar, hedef_hesap_no=None, anahtar=None):
    """Musteri islemi baslatir.

    anahtar (idempotency): ayni (hesap, anahtar) ciftiyle gelen tekrar istek yeni islem
    olusturmaz, ilk islemin sonucunu dondurur. Ag kopup istek iki kez gelse bile para
    iki kez cekilmez.
    """
    kart = _token_coz(token)
    hesap = kart.hesap
    tur = (tur or "").upper()
    if tur not in MUSTERI_ISLEMLERI:
        raise IslemHatasi("Geçersiz işlem türü", "tur")
    tutar = _tutar_coz(tutar)
    anahtar = _anahtar_coz(anahtar)

    if anahtar:
        onceki = Islem.objects.select_related("hedef_hesap").filter(hesap=hesap, anahtar=anahtar).first()
        if onceki:
            return _tekrar_istegi(onceki, tur, tutar, hedef_hesap_no)

    hedef = None
    if tur == Islem.Tur.HAVALE:
        hedef = Hesap.objects.filter(hesap_no=(hedef_hesap_no or "").strip(), tur=Hesap.Tur.MUSTERI).first()
        if hedef is None:
            raise IslemHatasi("Hedef hesap bulunamadı", "hedef")
        if hedef.id == hesap.id:
            raise IslemHatasi("Kendi hesabınıza havale olmaz", "hedef")

    gereken, en_az = 0, int(Rol.GOREVLI)
    if tur in (Islem.Tur.CEKME, Islem.Tur.HAVALE):
        hesap.refresh_from_db()
        if hesap.bakiye < tutar:
            kayit_ekle("islem_reddi_bakiye", {"hesap_no": hesap.hesap_no, "tur": tur, "tutar": para(tutar)})
            raise IslemHatasi("Yetersiz bakiye", "bakiye")
        if tur == Islem.Tur.CEKME and _kasa_hesabi().bakiye < tutar:
            kayit_ekle("islem_reddi_nakit", {"hesap_no": hesap.hesap_no, "tutar": para(tutar)})
            raise IslemHatasi("ATM'de yeterli nakit yok", "nakit")
        kullanilan = gunluk_toplam(hesap)
        if kullanilan + tutar > hesap.gunluk_limit:
            kalan = max(Decimal("0.00"), hesap.gunluk_limit - kullanilan)
            kayit_ekle("islem_reddi_limit", {"hesap_no": hesap.hesap_no, "tutar": para(tutar),
                                             "kalan_limit": para(kalan)})
            raise IslemHatasi("Günlük limit aşıldı", "limit", kalan_limit=para(kalan))
        gereken, en_az = gerekli_onay(tutar)
    elif tur == Islem.Tur.YATIRMA:
        _kasa_hesabi()

    with transaction.atomic():
        try:
            with transaction.atomic():
                islem = Islem.objects.create(
                    hesap=hesap, hedef_hesap=hedef, tur=tur, tutar=tutar,
                    baslatan_uid=kart.uid, anahtar=anahtar, terminal=baglam.terminal(),
                )
        except IntegrityError:
            # Ayni anahtarla es zamanli ikinci istek: ilk islemin sonucunu ver
            onceki = Islem.objects.select_related("hedef_hesap").filter(hesap=hesap, anahtar=anahtar).first()
            if onceki is None:
                raise
            return _tekrar_istegi(onceki, tur, tutar, hedef_hesap_no)

        if gereken == 0:
            _uygula(islem)
            hesap.refresh_from_db()
            return {"durum": "uygulandi", "islem_id": islem.id, "bakiye": para(hesap.bakiye),
                    "basarili": islem.durum == Islem.Durum.UYGULANDI, "tekrar": False}

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
    bildirim.talep_acildi(talep)  # commit sonrasi; hata verirse islemi bozmaz
    return {"durum": "onay_bekliyor", "talep_id": talep.id, "islem_id": islem.id,
            "gereken": gereken, "tekrar": False}


def _uygula(islem):
    """Defter hareketlerini yazar, bakiyeleri gunceller. transaction.atomic() ICINDE cagrilmali."""
    if islem.tur == Islem.Tur.KASA_ACMA:
        islem.durum = Islem.Durum.UYGULANDI
        islem.tamamlanma = simdi()
        islem.save(update_fields=["durum", "tamamlanma"])
        kayit_ekle("kasa_acildi", {"islem_id": islem.id, "baslatan_uid": islem.baslatan_uid})
        return True

    kasa_id = None
    if islem.tur in (Islem.Tur.CEKME, Islem.Tur.YATIRMA):
        kasa = Hesap.objects.filter(tur=Hesap.Tur.KASA).first()
        kasa_id = kasa.id if kasa else None

    idler = sorted({i for i in (islem.hesap_id, islem.hedef_hesap_id, kasa_id) if i})
    kilitli = {h.id: h for h in Hesap.objects.select_for_update().filter(id__in=idler).order_by("id")}
    kaynak = kilitli[islem.hesap_id]
    tutar = islem.tutar
    Y = HesapHareketi.Yon

    basarisizlik = None
    if islem.tur == Islem.Tur.CEKME:
        if kasa_id is None:
            basarisizlik = "kasa hesabı yok"
        elif kaynak.bakiye < tutar:
            basarisizlik = "yetersiz bakiye"
        elif kilitli[kasa_id].bakiye < tutar:
            basarisizlik = "kasada nakit yok"
    elif islem.tur == Islem.Tur.YATIRMA:
        if kasa_id is None:
            basarisizlik = "kasa hesabı yok"
    elif islem.tur == Islem.Tur.HAVALE:
        if kaynak.bakiye < tutar:
            basarisizlik = "yetersiz bakiye"

    if basarisizlik:
        islem.durum = Islem.Durum.BASARISIZ
        islem.tamamlanma = simdi()
        islem.save(update_fields=["durum", "tamamlanma"])
        kayit_ekle("islem_basarisiz", {"islem_id": islem.id, "sebep": basarisizlik})
        return False

    if islem.tur == Islem.Tur.CEKME:
        kasa = kilitli[kasa_id]
        ledger.hareket_ekle(islem, kaynak, Y.BORC, tutar)
        ledger.hareket_ekle(islem, kasa, Y.ALACAK, tutar)
        kasa.save(update_fields=["bakiye"])
    elif islem.tur == Islem.Tur.YATIRMA:
        kasa = kilitli[kasa_id]
        ledger.hareket_ekle(islem, kasa, Y.BORC, tutar)
        ledger.hareket_ekle(islem, kaynak, Y.ALACAK, tutar)
        kasa.save(update_fields=["bakiye"])
    else:  # HAVALE
        hedef = kilitli[islem.hedef_hesap_id]
        ledger.hareket_ekle(islem, kaynak, Y.BORC, tutar)
        ledger.hareket_ekle(islem, hedef, Y.ALACAK, tutar)
        hedef.save(update_fields=["bakiye"])
    kaynak.save(update_fields=["bakiye"])

    islem.durum = Islem.Durum.UYGULANDI
    islem.tamamlanma = simdi()
    islem.save(update_fields=["durum", "tamamlanma"])
    kayit_ekle("islem_uygulandi", {
        "islem_id": islem.id, "tur": islem.tur, "tutar": para(tutar),
        "hesap_no": kaynak.hesap_no, "yeni_bakiye": para(kaynak.bakiye),
        "hedef_hesap_id": islem.hedef_hesap_id,
    })
    return True


# ------------------------------------------------------------------ kasa dairesi

def kasa_acma_baslat(uid, pin):
    """Yetkili, kasa dairesini acmak icin onay talebi baslatir.

    Kural ayarlardan gelir (KASA_ACMA_GEREKEN_ONAY, KASA_ACMA_EN_AZ_ROL). Talebi baslatan
    kendi talebini onaylayamaz; bu yuzden onaylayabilecek yeterli baska yetkili de olmali.
    """
    personel = personel_dogrula(uid, pin)
    kasa = _kasa_hesabi()
    gereken = int(settings.KASA_ACMA_GEREKEN_ONAY)
    en_az = int(settings.KASA_ACMA_EN_AZ_ROL)
    adaylar = Personel.objects.filter(aktif=True, bloke=False).exclude(id=personel.id)
    if gereken > adaylar.count() or not adaylar.filter(rol__gte=en_az).exists():
        kayit_ekle("kasa_acma_reddi", {"personel_id": personel.id, "sebep": "yeterli onaylayici yok"})
        raise IslemHatasi("Yeterli yetkili yok", "yetkili_yok")

    with transaction.atomic():
        islem = Islem.objects.create(
            hesap=kasa, tur=Islem.Tur.KASA_ACMA, tutar=Decimal("0.00"),
            baslatan_uid=personel.kart_uid, durum=Islem.Durum.ONAY_BEKLIYOR,
            terminal=baglam.terminal(),
        )
        talep = OnayTalebi.objects.create(
            islem=islem, gereken_onay=gereken, en_az_rol=en_az,
            son_tarih=simdi() + timedelta(seconds=settings.ONAY_PENCERESI_SN),
        )
        kayit_ekle("kasa_acma_talebi", {
            "talep_id": talep.id, "islem_id": islem.id, "baslatan_personel_id": personel.id,
            "gereken_onay": gereken, "en_az_rol": en_az,
        })
    bildirim.talep_acildi(talep)
    return {"durum": "onay_bekliyor", "talep_id": talep.id, "islem_id": islem.id, "gereken": gereken}


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
    kasa_acildi = None
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
                    if basarili and islem.tur == Islem.Tur.KASA_ACMA:
                        kasa_acildi = islem
        ozet = _ozet(talep)

    if kasa_acildi:
        bildirim.kasa_acildi(kasa_acildi)  # commit sonrasi
    if hata:
        raise IslemHatasi(hata[0], hata[1], **ozet)
    return ozet
