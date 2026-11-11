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
from django.db.models import F, Q, Sum
from django.utils import timezone

from . import (baglam, bildirim, bloke_butunlugu, kart_butunlugu, kart_imzasi, kilit, ledger, nakit, personel_butunlugu, risk,
               politika_degisikligi, rbac)
from .hashlog import kayit_ekle
from .models import (Hesap, HesapHareketi, Islem, Kart, Onay, OnayTalebi, Personel, Politika,
                     Rol, Terminal, uid_duzelt)

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

def _imza_kontrol(nesne, uid, imza):
    """Imzali kart (kart_imzasi.py): ImzaHatasi'ni IslemHatasi'na cevirir."""
    try:
        kart_imzasi.kontrol(nesne, uid, imza)
    except kart_imzasi.ImzaHatasi as e:
        raise IslemHatasi(e.mesaj, e.kod)


def imza_coz(sayac=None, mac=None):
    """Protokoldeki (sayac, mac) alanlarindan imza; yoksa None. Bicim hatasi IslemHatasi."""
    try:
        return kart_imzasi.coz(sayac, mac)
    except kart_imzasi.ImzaHatasi as e:
        raise IslemHatasi(e.mesaj, e.kod)


def kart_tipi(uid, imza=None):
    """MUSTERI | PERSONEL | BLOKE | BILINMIYOR | IMZASIZ (imza yok/gecersiz/tekrar: klon suphesi)"""
    uid = uid_duzelt(uid)
    personel = Personel.objects.filter(kart_uid=uid, aktif=True).first()
    if personel:
        bloke = personel.bloke or bloke_butunlugu.beklenen_bloke_mu("personel", personel.id, uid)
        if bloke:
            return "BLOKE"
        try:
            _imza_kontrol(personel, uid, imza)
        except IslemHatasi:
            return "IMZASIZ"
        return "PERSONEL"
    kart = Kart.objects.select_related("hesap").filter(uid=uid).first()
    if kart:
        if kart_butunlugu.kart_dogrulandi_mi(kart) is False:  # onaysiz eklenmis/degistirilmis kart
            kart_butunlugu.ihlal_kaydet(kart)
            return "BLOKE"
        bloke = kart.bloke or bloke_butunlugu.beklenen_bloke_mu("kart", kart.id, uid)
        if bloke:
            return "BLOKE"
        try:
            _imza_kontrol(kart, uid, imza)
        except IslemHatasi:
            return "IMZASIZ"
        return "MUSTERI"
    kayit_ekle("bilinmeyen_kart", {"uid": uid})
    return "BILINMIYOR"


def _pin_kontrol(nesne, pin, kimlik):
    """Kart veya Personel icin PIN'i dener; sayac ve bloke mantigi burada.

    Sayac guncellemesi SATIR KILIDI altinda yapilir: PostgreSQL'de ayni karta es zamanli yanlis denemeler
    sayaci ezemez, yani "3 yanlis PIN'de bloke" siniri paralel isteklerle asilamaz. Agir hash hesabi kilit
    DISINDA yapilir. Hatalar kilit blogunun DISINDA firlatilir (hatali deneme sayaci ve log geri alinmasin).
    """
    tur = "kart" if isinstance(nesne, Kart) else "personel"
    dogru = nesne.pin_dogru_mu(pin)
    hata = None
    with transaction.atomic():
        kilit.zincir_kilidi()
        taze = type(nesne).objects.select_for_update().get(pk=nesne.pk)
        if taze.pin_hash != nesne.pin_hash:  # bu arada PIN degisti: guncel hash'e gore yeniden dene
            dogru = taze.pin_dogru_mu(pin)
        if not taze.bloke and bloke_butunlugu.beklenen_bloke_mu(tur, taze.id, kimlik):
            # log bloke diyor, veritabani degil: bloke alani onaysiz sifirlanmis, kapali kal
            bloke_butunlugu.ihlal_kaydet(kimlik, "kaldirilmis")
            hata = ("Kart bloke edildi", "bloke", {})
        elif taze.bloke:
            kayit_ekle("bloke_kart_denemesi", {"kimlik": kimlik})
            hata = ("Kart bloke edildi", "bloke", {})
        elif dogru:
            if taze.hatali_deneme:
                taze.hatali_deneme = 0
                taze.save(update_fields=["hatali_deneme"])
        else:
            taze.hatali_deneme += 1
            kalan = max(0, settings.MAKS_PIN_DENEME - taze.hatali_deneme)
            if kalan == 0:
                taze.bloke = True
            taze.save(update_fields=["hatali_deneme", "bloke"])
            kayit_ekle("pin_hatali", {"kimlik": kimlik, "kalan_hak": kalan, "bloke": taze.bloke})
            if taze.bloke:
                hata = ("PIN hatalı, kart bloke edildi", "bloke", {"kalan_hak": 0})
            else:
                hata = (f"Yanlış PIN, kalan hak: {kalan}", "pin", {"kalan_hak": kalan})
    nesne.hatali_deneme, nesne.bloke = taze.hatali_deneme, taze.bloke
    if hata:
        raise IslemHatasi(hata[0], hata[1], **hata[2])


def musteri_giris(uid, pin, imza=None):
    uid = uid_duzelt(uid)
    kart = Kart.objects.select_related("hesap__musteri").filter(uid=uid).first()
    if kart is None:
        kayit_ekle("bilinmeyen_kart", {"uid": uid})
        raise IslemHatasi("Kart tanımlı değil", "bilinmiyor")
    if kart_butunlugu.kart_dogrulandi_mi(kart) is False:
        kart_butunlugu.ihlal_kaydet(kart)
        raise IslemHatasi("Kart doğrulanamadı; şubeye başvurun", "kart_butunluk")
    _imza_kontrol(kart, uid, imza)   # PIN'den ONCE: imzasiz/klon kart PIN denemesi sayacini da kurcalayamaz
    _pin_kontrol(kart, pin, uid)
    kayit_ekle("giris", {"uid": uid, "hesap_no": kart.hesap.hesap_no})
    token = signing.TimestampSigner(salt=TOKEN_TUZU).sign(str(kart.id))
    return {"token": token, "ad": kart.hesap.musteri.ad, "hesap_no": kart.hesap.hesap_no}


PIN_ARDISIK = {"0123", "1234", "2345", "3456", "4567", "5678", "6789", "9876", "8765", "7654", "6543", "5432", "4321", "3210"}


def pin_zayif_mi(pin):
    """Aynı rakamlar (0000) ya da ardışık dizi (1234, 4321): tahmin edilmesi kolay PIN."""
    return len(set(pin)) == 1 or pin in PIN_ARDISIK


def pin_degistir(token, eski, yeni):
    """Müşteri PIN'ini terminalde KENDİ değiştirir: açık oturum + ESKİ PIN yeniden doğrulanır.

    Oturum tek başına yetmez: açık bırakılmış bir terminalden PIN'in çalınmasını eski PIN şartı engeller. Eski PIN
    yanlışsa normal PIN sayacı işler (3'te bloke). Yeni PIN eskisinden farklı, 4 rakam ve kolay tahmin edilir olmamalı.
    Mühür ve onaylı kart özeti yenilenir (onaylı bir değişiklik sayılır); kart kayıtları zaten tutarsızsa değişiklik
    reddedilir, böylece onaysız bir müdahale bu yolla meşrulaştırılmaz. PIN hiçbir yere yazılmaz; müşterinin
    telefonuna "PIN'iniz değiştirildi" SMS'i gider (kendisi yapmadıysa fark etsin).
    """
    kart = _token_coz(token)
    eski, yeni = str(eski or ""), str(yeni or "")
    if not re.fullmatch(r"\d{4}", yeni):
        raise IslemHatasi("Yeni PIN 4 rakam olmalı", "pin_bicim")
    if yeni == eski:
        raise IslemHatasi("Yeni PIN eskisiyle aynı olamaz", "pin_ayni")
    if pin_zayif_mi(yeni):
        raise IslemHatasi("Yeni PIN çok kolay tahmin edilir", "pin_zayif")
    _pin_kontrol(kart, eski, kart.uid)   # yanlis eski PIN sayaci isletir; hata kilit blogu disinda firlatilir
    hata, musteri = None, None
    with transaction.atomic():
        kilit.zincir_kilidi()
        taze = Kart.objects.select_for_update().select_related("hesap__musteri").get(pk=kart.pk)
        baslangic_var = kart_butunlugu.son_ozet() is not None
        if taze.bloke:
            hata = ("Kart bloke edildi", "bloke")
        elif taze.pin_hash != kart.pin_hash:
            hata = ("PIN bu arada değişti; yeniden giriş yapın", "durum_degisti")
        elif baslangic_var and kart_butunlugu.tutarli_mi() is False:
            hata = ("Kart kayıtları doğrulanamadı; şubeye başvurun", "kart_butunluk")
        else:
            taze.pin_ayarla(yeni)   # hatali deneme sayacini da sifirlar
            taze.save(update_fields=["pin_hash", "hatali_deneme"])
            if baslangic_var:
                kart_butunlugu.muhurle(taze)
                kart_butunlugu.goruntu_kaydet("musteri_pin_degisti", kart_id=taze.id)
            kayit_ekle("pin_degistirildi", {"uid": taze.uid, "hesap_no": taze.hesap.hesap_no})
            musteri = taze.hesap.musteri
    if hata:
        raise IslemHatasi(*hata)
    bildirim.pin_degisti(musteri)
    return {"degisti": True}


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


def personel_butunlugunu_dogrula():
    """Yetkili kayıtları son onaylı sürümden farklıysa onaylar ve kasa açma KAPALI kalır."""
    if personel_butunlugu.tutarli_mi() is False:
        personel_butunlugu.ihlal_kaydet()
        raise IslemHatasi("Yetkili kayıtları doğrulanamadı; işlem yapılamaz", "butunluk")


def personel_dogrula(uid, pin, imza=None):
    uid = uid_duzelt(uid)
    personel = Personel.objects.filter(kart_uid=uid, aktif=True).first()
    if personel is None:
        kayit_ekle("yetkisiz_onay_denemesi", {"uid": uid})
        raise IslemHatasi("Yetkili kartı değil", "yetkisiz")
    _imza_kontrol(personel, uid, imza)
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
    butunluk_bozuk = politika_degisikligi.goruntu_tutarli_mi() is False
    if butunluk_bozuk:
        politika_degisikligi.ihlal_kaydet()
    if politika is None or butunluk_bozuk:
        # yapilandirma eksikse ya da politika tablosu ONAYSIZ degistirilmisse kapali kal
        gereken, en_az = 3, int(Rol.MUDUR)
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
    ).exclude(teslim=Islem.Teslim.GERI_ALINDI).aggregate(t=Sum(F("tutar") - F("iade_tutari")))["t"]
    return toplam or Decimal("0.00")


def _islem_sonucu(islem, tekrar=False):
    """Mevcut bir islemin durumunu, islem_baslat cevabi bicimine cevirir."""
    if islem.teslim == Islem.Teslim.GERI_ALINDI:
        raise IslemHatasi("İşlem ters kaydedildi", "ters_kayit")
    if islem.durum == Islem.Durum.ONAY_BEKLIYOR:
        talep_durumu(islem.talep.id)  # suresi dolduysa burada kapanir
        islem.refresh_from_db()
    if islem.durum in (Islem.Durum.UYGULANDI, Islem.Durum.BASARISIZ):
        hesap = Hesap.objects.get(id=islem.hesap_id)
        return {"durum": "uygulandi", "islem_id": islem.id, "bakiye": para(hesap.bakiye),
                "basarili": islem.durum == Islem.Durum.UYGULANDI, "tekrar": tekrar,
                "dagitim": islem.dagitim_plani}
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


def _islem_kontrolleri(hesap, tur, tutar):
    """Hesap satiri KILITLIYKEN: bakiye, nakit, limit, banknot stogu ve onay politikasi.

    (hata, gereken_onay, en_az_rol) dondurur. hata None ya da (mesaj, kod, ekstra). Red logu burada yazilir;
    cagiran, hatayi atomic bloktan CIKTIKTAN sonra firlatir ki log geri alinmasin.
    """
    gereken, en_az = 0, int(Rol.GOREVLI)
    if tur in (Islem.Tur.CEKME, Islem.Tur.HAVALE):
        if hesap.bakiye < tutar:
            kayit_ekle("islem_reddi_bakiye", {"hesap_no": hesap.hesap_no, "tur": tur, "tutar": para(tutar)})
            return ("Yetersiz bakiye", "bakiye", {}), gereken, en_az
        if tur == Islem.Tur.CEKME and _kasa_hesabi().bakiye < tutar:
            kayit_ekle("islem_reddi_nakit", {"hesap_no": hesap.hesap_no, "tutar": para(tutar)})
            return ("ATM'de yeterli nakit yok", "nakit", {}), gereken, en_az
        kullanilan = gunluk_toplam(hesap)
        if kullanilan + tutar > hesap.gunluk_limit:
            kalan = max(Decimal("0.00"), hesap.gunluk_limit - kullanilan)
            kayit_ekle("islem_reddi_limit", {"hesap_no": hesap.hesap_no, "tutar": para(tutar),
                                             "kalan_limit": para(kalan)})
            return ("Günlük limit aşıldı", "limit", {"kalan_limit": para(kalan)}), gereken, en_az
        if tur == Islem.Tur.CEKME:
            try:  # banknot stogu: tutar eldeki banknotlarla verilebilir mi? (rezerve etmez; asil rezerve _uygula'da)
                nakit.plan_uret(baglam.terminal(), tutar, rezerve=False)
            except nakit.NakitHatasi as e:
                kayit_ekle("islem_reddi_banknot", {"hesap_no": hesap.hesap_no, "tutar": para(tutar),
                                                   "neden": e.kod, "terminal": nakit.terminal_kodu(baglam.terminal())})
                return (e.mesaj, e.kod, {}), gereken, en_az
        try:
            gereken, en_az = gerekli_onay(tutar)
        except IslemHatasi as e:
            return (e.mesaj, e.kod, e.ekstra), 0, en_az
    elif tur == Islem.Tur.YATIRMA:
        _kasa_hesabi()
    return None, gereken, en_az


@kilit.yeniden_dene
def islem_baslat(token, tur, tutar, hedef_hesap_no=None, anahtar=None):
    """Musteri islemi baslatir.

    anahtar (idempotency): ayni (hesap, anahtar) ciftiyle gelen tekrar istek yeni islem
    olusturmaz, ilk islemin sonucunu dondurur. Ag kopup istek iki kez gelse bile para
    iki kez cekilmez.

    Es zamanlilik: bakiye, gunluk limit ve banknot stogu kontrolleri, hesap satiri ve zincir kilidi
    ALTINDA yapilir (kilit.py). Ayni hesaptan es zamanli iki istek, kontrolleri ic ice gecemez.
    """
    kart = _token_coz(token)
    tur = (tur or "").upper()
    if tur not in MUSTERI_ISLEMLERI:
        raise IslemHatasi("Geçersiz işlem türü", "tur")
    tutar = _tutar_coz(tutar)
    anahtar = _anahtar_coz(anahtar)

    if anahtar:  # hizli yol: ayni anahtar daha once islendiyse eski sonucu dondur
        onceki = Islem.objects.select_related("hedef_hesap").filter(hesap=kart.hesap, anahtar=anahtar).first()
        if onceki:
            return _tekrar_istegi(onceki, tur, tutar, hedef_hesap_no)

    hedef = None
    if tur == Islem.Tur.HAVALE:
        hedef = Hesap.objects.filter(hesap_no=(hedef_hesap_no or "").strip(), tur=Hesap.Tur.MUSTERI).first()
        if hedef is None:
            raise IslemHatasi("Hedef hesap bulunamadı", "hedef")
        if hedef.id == kart.hesap_id:
            raise IslemHatasi("Kendi hesabınıza havale olmaz", "hedef")

    hata, tekrar, talep, islem, hesap, risk_sonucu = None, None, None, None, None, None
    with transaction.atomic():
        kilit.zincir_kilidi()
        hesap = Hesap.objects.select_for_update().get(id=kart.hesap_id)
        if anahtar:  # kilit altinda tekrar bak: es zamanli ayni anahtarli istek az once tamamlanmis olabilir
            tekrar = Islem.objects.select_related("hedef_hesap").filter(hesap=hesap, anahtar=anahtar).first()
        if tekrar is None and (Kart.objects.filter(pk=kart.pk, bloke=True).exists()
                               or bloke_butunlugu.beklenen_bloke_mu("kart", kart.id, kart.uid)):
            # oturum acikken kart bloke edildi (risk motoru, 3. yanlis PIN) ya da bloke alani onaysiz sifirlandi (log
            # bloke diyor): token'la devam edilemez
            hata, gereken, en_az = ("Kart bloke edildi", "bloke", {}), 0, int(Rol.GOREVLI)
        elif tekrar is None:
            hata, gereken, en_az = _islem_kontrolleri(hesap, tur, tutar)
            if hata is None and tur in (Islem.Tur.CEKME, Islem.Tur.HAVALE):
                hata, gereken, en_az, risk_sonucu = risk.kontrol(kart, hesap, tur, tutar, gereken, en_az)
            if hata is None:
                try:
                    with transaction.atomic():
                        islem = Islem.objects.create(
                            hesap=hesap, hedef_hesap=hedef, tur=tur, tutar=tutar,
                            baslatan_uid=kart.uid, anahtar=anahtar, terminal=baglam.terminal(),
                            risk_skoru=(risk_sonucu or {}).get("skor", 0), risk_notu=(risk_sonucu or {}).get("not", ""),
                        )
                except IntegrityError:
                    tekrar = Islem.objects.select_related("hedef_hesap").filter(hesap=hesap, anahtar=anahtar).first()
                    if tekrar is None:
                        raise
                if tekrar is None:
                    if gereken == 0:
                        _uygula(islem)
                        hesap.refresh_from_db()
                    else:
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

    if tekrar is not None:
        return _tekrar_istegi(tekrar, tur, tutar, hedef_hesap_no)
    if hata:
        if risk_sonucu and risk_sonucu["karar"] in ("RED", "BLOKE"):
            bildirim.risk_reddi(kart.hesap.musteri, risk_sonucu["karar"] == "BLOKE")   # commit sonrasi
        raise IslemHatasi(hata[0], hata[1], **hata[2])
    if talep is None:
        return {"durum": "uygulandi", "islem_id": islem.id, "bakiye": para(hesap.bakiye),
                "basarili": islem.durum == Islem.Durum.UYGULANDI, "tekrar": False,
                "dagitim": islem.dagitim_plani}
    bildirim.talep_acildi(talep)  # commit sonrasi; hata verirse islemi bozmaz
    return {"durum": "onay_bekliyor", "talep_id": talep.id, "islem_id": islem.id,
            "gereken": talep.gereken_onay, "tekrar": False}


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

    plan = None
    if basarisizlik is None and islem.tur == Islem.Tur.CEKME:
        try:  # banknotlari REZERVE et (kilit altinda); verilemiyorsa para hic hareket etmez
            plan = nakit.plan_uret(islem.terminal, islem.tutar, rezerve=True)
        except nakit.NakitHatasi as e:
            basarisizlik = f"banknot: {e.mesaj}"

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
    if islem.tur == Islem.Tur.CEKME:
        islem.teslim = Islem.Teslim.BEKLIYOR  # paranin fiilen verilmesi terminalden bildirilecek
        islem.dagitim_plani = nakit.plan_yaz(plan or {})
    islem.save(update_fields=["durum", "tamamlanma", "teslim", "dagitim_plani"])
    kayit_ekle("islem_uygulandi", {
        "islem_id": islem.id, "tur": islem.tur, "tutar": para(tutar),
        "hesap_no": kaynak.hesap_no, "yeni_bakiye": para(kaynak.bakiye),
        "hedef_hesap_id": islem.hedef_hesap_id, "dagitim": islem.dagitim_plani,
    })
    return True


# ------------------------------------------------------------------ ters kayit / teslim

TESLIM_BILDIRIMLERI = {"ALINDI", "ALINMADI", "ARIZA", "SIKISTI", "EKSIK"}
TERS_NEDENI = {"ALINMADI": "MUSTERI_ALMADI", "ARIZA": "PARA_VERILMEDI", "SIKISTI": "DAGITICI_SIKISTI"}
HEDEF_TESLIM = {"ALINDI": Islem.Teslim.ALINDI, "ALINMADI": Islem.Teslim.GERI_ALINDI,
                "ARIZA": Islem.Teslim.GERI_ALINDI, "SIKISTI": Islem.Teslim.GERI_ALINDI,
                "EKSIK": Islem.Teslim.KISMI}


def _ters_kayit_uygula(orijinal, neden, tutar=None, teslim=None):
    """Nakit cekmeyi (tamamen ya da `tutar` kadar), orijinal satirlara DOKUNMADAN, ters borc/alacak
    satirlariyla geri alir. Eksik vermede yalniz verilmeyen fark iade edilir.

    transaction.atomic() ICINDE ve `orijinal` kilitliyken cagrilir. Cagiran, `orijinal.verilen_plani` ve
    `orijinal.iade_tutari` alanlarini gerekiyorsa once ayarlar; burada birlikte kaydedilir.
    """
    tutar = orijinal.tutar if tutar is None else tutar
    teslim = Islem.Teslim.GERI_ALINDI if teslim is None else teslim
    kasa = _kasa_hesabi()
    idler = sorted({orijinal.hesap_id, kasa.id})
    kilitli = {h.id: h for h in Hesap.objects.select_for_update().filter(id__in=idler).order_by("id")}
    musteri, kasa = kilitli[orijinal.hesap_id], kilitli[kasa.id]
    Y = HesapHareketi.Yon

    ters = Islem.objects.create(
        hesap=orijinal.hesap, tur=Islem.Tur.TERS_KAYIT, tutar=tutar,
        baslatan_uid=orijinal.baslatan_uid, ters_islem=orijinal, ters_neden=neden,
        terminal=baglam.terminal(),
    )
    ledger.hareket_ekle(ters, musteri, Y.ALACAK, tutar)
    ledger.hareket_ekle(ters, kasa, Y.BORC, tutar)
    kasa.save(update_fields=["bakiye"])
    musteri.save(update_fields=["bakiye"])

    ters.durum = Islem.Durum.UYGULANDI
    ters.tamamlanma = simdi()
    ters.save(update_fields=["durum", "tamamlanma"])
    orijinal.teslim = teslim
    orijinal.save(update_fields=["teslim", "verilen_plani", "iade_tutari"])
    kayit_ekle("ters_kayit", {
        "orijinal_islem_id": orijinal.id, "ters_islem_id": ters.id, "tutar": para(tutar),
        "orijinal_tutar": para(orijinal.tutar), "neden": neden, "hesap_no": musteri.hesap_no,
        "yeni_bakiye": para(musteri.bakiye),
    })
    return ters


def _stok_isle(islem, planlanan, verilen, kutuya, kasete_geri=None):
    """Dagitim sonucunu kaset stoguna isler. Tutarsizlik teslim sonucunu ENGELLEMEZ, loga yazilir."""
    try:
        uyarilar = nakit.sonuclandir(islem.terminal, planlanan, verilen, kutuya, kasete_geri or {})
    except nakit.NakitHatasi as e:
        uyarilar = [e.mesaj]
    if uyarilar:
        kayit_ekle("kaset_tutarsiz", {"islem_id": islem.id, "uyarilar": uyarilar[:5]})


def _teslim_alindi(islem, planlanan, olay="teslim_onayi"):
    islem.teslim = Islem.Teslim.ALINDI
    islem.verilen_plani = islem.dagitim_plani
    islem.save(update_fields=["teslim", "verilen_plani"])
    _stok_isle(islem, planlanan, verilen=planlanan, kutuya={})
    kayit_ekle(olay, {"islem_id": islem.id})


def belirsiz_mi(islem):
    """Teslimi BELIRSIZ olan ya da teslim penceresi dolmus ve sonucu gelmemis cekme mi? (yazmaz)"""
    T = Islem.Teslim
    return islem.teslim == T.BELIRSIZ or (
        islem.teslim == T.BEKLIYOR and bool(islem.tamamlanma)
        and simdi() - islem.tamamlanma > timedelta(seconds=settings.TESLIM_PENCERESI_SN))


def _belirsiz_isaretle(islem):
    """Teslim penceresi gecmis ve sonuc gelmemis cekmeyi BELIRSIZ yapar (islem kilitliyken)."""
    if islem.teslim == Islem.Teslim.BEKLIYOR and belirsiz_mi(islem):
        islem.teslim = Islem.Teslim.BELIRSIZ
        islem.save(update_fields=["teslim"])
        kayit_ekle("teslim_belirsiz_isaretlendi", {"islem_id": islem.id, "terminal": islem.terminal})


def _dagitici_kapat(islem, neden):
    kod = nakit.terminal_kodu(islem.terminal)
    Terminal.objects.filter(kod=kod).update(nakit_dagitimi=False)
    kayit_ekle("dagitici_kapandi", {"terminal": kod, "islem_id": islem.id, "neden": neden})


def _teslim_cevabi(islem):
    T = Islem.Teslim
    if islem.teslim == T.ALINDI:
        sonuc, iade = "ALINDI", Decimal("0.00")
    elif islem.teslim == T.GERI_ALINDI:
        sonuc, iade = "GERI", islem.tutar
    elif islem.teslim == T.KISMI:
        sonuc, iade = "KISMI", islem.iade_tutari
    else:
        sonuc, iade = islem.teslim, Decimal("0.00")
    return {"teslim": sonuc, "islem_id": islem.id, "iade": para(iade)}


def _teslim_uygula(islem, durum, verilen_plan):
    """Teslim bildirimini, islem KILITLIYKEN isler. (hata, ters_kayit, dagitici_kapatma_nedeni) dondurur."""
    T = Islem.Teslim
    planlanan = nakit.plan_coz(islem.dagitim_plani)
    _belirsiz_isaretle(islem)

    if islem.teslim == T.BELIRSIZ:
        if durum == "ALINDI":
            # Gec gelen "para alindi": paranin gittigini dogrular, hicbir parayi geri vermez; guvenle kabul edilir
            _teslim_alindi(islem, planlanan, olay="teslim_gec_onay")
            return None, None, None
        kayit_ekle("teslim_sure_asimi", {"islem_id": islem.id, "bildirilen": durum})
        return ("Teslim bildirimi için süre geçti", "sure"), None, None

    hedef = HEDEF_TESLIM[durum]
    if islem.teslim != T.BEKLIYOR:  # son durum zaten kayitli: ayni bildirim tekrari sorunsuz, celiskisi reddedilir
        ayni = islem.teslim == hedef and (hedef != T.KISMI or nakit.plan_yaz(verilen_plan or {}) == islem.verilen_plani)
        if ayni:
            return None, None, None
        kayit_ekle("teslim_celiskisi", {"islem_id": islem.id, "kayitli": islem.teslim, "bildirilen": durum})
        return ("Bu işlemin teslim durumu zaten kayıtlı", "celiski"), None, None

    if durum == "ALINDI":
        _teslim_alindi(islem, planlanan)
        return None, None, None

    if durum == "EKSIK":
        verilen_tutar = nakit.plan_tutari(verilen_plan or {})
        if (not verilen_plan or not nakit.plan_icinde_mi(verilen_plan, planlanan)
                or not (0 < verilen_tutar < islem.tutar)):
            kayit_ekle("teslim_gecersiz_eksik", {"islem_id": islem.id, "bildirilen": nakit.plan_yaz(verilen_plan or {})})
            return ("Geçersiz eksik verme bildirimi", "plan"), None, None
        iade = islem.tutar - Decimal(verilen_tutar)
        _stok_isle(islem, planlanan, verilen=verilen_plan, kutuya=nakit.plan_cikar(planlanan, verilen_plan))
        islem.verilen_plani = nakit.plan_yaz(verilen_plan)
        islem.iade_tutari = iade
        ters = _ters_kayit_uygula(islem, "EKSIK_VERILDI", tutar=iade, teslim=T.KISMI)
        return None, ters, "EKSIK"

    # ALINMADI (zaman asimi, para geri cekildi) / ARIZA (para verilmedi) / SIKISTI: tam iade, banknotlar iade kutusuna
    _stok_isle(islem, planlanan, verilen={}, kutuya=planlanan)
    ters = _ters_kayit_uygula(islem, TERS_NEDENI[durum])
    return None, ters, ("SIKISTI" if durum == "SIKISTI" else None)


@kilit.yeniden_dene
def teslim_bildir(islem_id, durum, verilen=None):
    """Terminal, nakit cekmede paranin fiilen teslim edilip edilmedigini bildirir (dagitici/sensor olayi).

    durum:
      ALINDI   para alindi
      ALINMADI zaman asimi: para geri cekildi        -> tam ters kayit, banknotlar iade kutusuna
      ARIZA    dagitici hatasi, para verilmedi        -> tam ters kayit
      SIKISTI  banknotlar sikisti, para verilmedi     -> tam ters kayit + dagitici KAPANIR
      EKSIK    eksik verildi; `verilen` = fiilen verilen banknotlar ("200x8+100x1") -> yalniz fark iade + dagitici KAPANIR

    Ayni bildirim tekrarlanirsa ayni sonuc doner (idempotent). Yalnizca islemi baslatan terminal,
    TESLIM_PENCERESI_SN icinde bildirebilir; sure gecerse teslim "belirsiz" olur: sadece "para alindi" gec de
    kabul edilir (hicbir parayi geri vermez), iade gerektiren bildirimler operator sayimina (nakit_coz) kalir.
    """
    durum = str(durum).upper()
    if durum not in TESLIM_BILDIRIMLERI:
        raise IslemHatasi("Geçersiz teslim durumu", "teslim")
    try:
        islem_id = int(islem_id)
    except (TypeError, ValueError):
        raise IslemHatasi("İşlem bulunamadı", "yok")
    verilen_plan = None
    if durum == "EKSIK":
        try:
            verilen_plan = nakit.plan_coz(verilen)
        except nakit.NakitHatasi as e:
            raise IslemHatasi(e.mesaj, e.kod)

    hata, ters, kapat, cevap = None, None, None, None
    with transaction.atomic():
        kilit.zincir_kilidi()
        islem = Islem.objects.select_for_update().select_related("hesap").filter(id=islem_id).first()
        if islem is None or islem.tur != Islem.Tur.CEKME or islem.durum != Islem.Durum.UYGULANDI:
            hata = ("İşlem bulunamadı ya da teslim bildirilemez", "yok")
        elif islem.terminal and islem.terminal != baglam.terminal():
            kayit_ekle("teslim_baska_terminal", {"islem_id": islem.id, "islem_terminali": islem.terminal})
            hata = ("Teslimi yalnızca işlemi başlatan terminal bildirebilir", "terminal")
        elif not islem.teslim:
            hata = ("Bu işlem için teslim takibi yok", "yok")
        else:
            hata, ters, kapat = _teslim_uygula(islem, durum, verilen_plan)
            if kapat:
                _dagitici_kapat(islem, kapat)
            if hata is None:
                cevap = _teslim_cevabi(islem)
    if hata:
        raise IslemHatasi(*hata)
    if ters:
        bildirim.ters_kayit(ters)  # commit sonrasi
    if kapat:
        bildirim.dagitici_arizasi(islem, kapat)
    return cevap


def belirsizleri_isaretle():
    """Teslim penceresi gecmis, sonucu gelmemis nakit cekmeleri BELIRSIZ yapar (terminal coktu / baglanti koptu).

    Para OTOMATIK iade edilmez: musteri parayi almis olabilir, iade cift odeme olurdu. Operator kasetleri
    sayar ve nakit_coz ile sonuclandirir.
    """
    esik = simdi() - timedelta(seconds=settings.TESLIM_PENCERESI_SN)
    idler = list(Islem.objects.filter(
        tur=Islem.Tur.CEKME, durum=Islem.Durum.UYGULANDI, teslim=Islem.Teslim.BEKLIYOR, tamamlanma__lt=esik,
    ).values_list("id", flat=True))
    for islem_id in idler:
        with transaction.atomic():
            kilit.zincir_kilidi()
            islem = Islem.objects.select_for_update().filter(id=islem_id).first()
            if islem is not None:
                _belirsiz_isaretle(islem)
    return len(idler)


NAKIT_COZUM_SONUCLARI = {"VERILDI", "VERILMEDI", "KISMI"}


def _nakit_coz_islemi(islem_id, sonuc, yapan, neden, verilen_plan, ek=None):
    """Belirsiz teslimi sonuclandirir. transaction.atomic() icinde ve zincir kilidi alinmis olarak cagrilir.

    (hata, ters, cevap) doner; hata (mesaj, kod) ise hicbir sonuc uygulanmamistir (yalnizca BELIRSIZ isareti
    yazilmis olabilir). `ek`, 'nakit_cozuldu' log kaydina eklenir (dort goz talebi ve onaylayanlar gibi).
    """
    T = Islem.Teslim
    hata, ters, cevap = None, None, None
    islem = Islem.objects.select_for_update().select_related("hesap").filter(id=islem_id).first()
    if islem is None or islem.tur != Islem.Tur.CEKME or islem.durum != Islem.Durum.UYGULANDI or not islem.teslim:
        return ("İşlem bulunamadı ya da nakit çekme değil", "yok"), None, None
    _belirsiz_isaretle(islem)
    if islem.teslim == T.BEKLIYOR:
        return ("Teslim hâlâ bekleniyor; süre dolunca çözülebilir", "bekliyor"), None, None
    if islem.teslim != T.BELIRSIZ:
        return ("Bu işlem zaten sonuçlanmış", "sonuclanmis"), None, None
    planlanan = nakit.plan_coz(islem.dagitim_plani)
    if sonuc == "VERILDI":
        _teslim_alindi(islem, planlanan, olay="teslim_operator_onayi")
    elif sonuc == "VERILMEDI":
        _stok_isle(islem, planlanan, verilen={}, kutuya={}, kasete_geri=planlanan)
        ters = _ters_kayit_uygula(islem, "OPERATOR_VERILMEDI")
    else:
        verilen_tutar = nakit.plan_tutari(verilen_plan or {})
        if (not verilen_plan or not nakit.plan_icinde_mi(verilen_plan, planlanan)
                or not (0 < verilen_tutar < islem.tutar)):
            return ("Geçersiz eksik verme bildirimi", "plan"), None, None
        iade = islem.tutar - Decimal(verilen_tutar)
        _stok_isle(islem, planlanan, verilen=verilen_plan, kutuya=nakit.plan_cikar(planlanan, verilen_plan))
        islem.verilen_plani = nakit.plan_yaz(verilen_plan)
        islem.iade_tutari = iade
        ters = _ters_kayit_uygula(islem, "OPERATOR_KISMI", tutar=iade, teslim=T.KISMI)
    kayit_ekle("nakit_cozuldu", {"islem_id": islem.id, "sonuc": sonuc, "yapan": yapan[:60],
                                 "neden": neden[:200], "verilen": islem.verilen_plani, **(ek or {})})
    cevap = _teslim_cevabi(islem)
    return hata, ters, cevap


def nakit_coz_girdisi(islem_id, sonuc, yapan, neden, verilen=None):
    """nakit_coz girdilerini dogrular; (islem_id, sonuc, yapan, neden, verilen_plan) doner."""
    sonuc = str(sonuc).upper()
    if sonuc not in NAKIT_COZUM_SONUCLARI:
        raise IslemHatasi("Geçersiz sonuç", "sonuc")
    yapan, neden = (yapan or "").strip(), (neden or "").strip()
    if len(yapan) < 2 or len(neden) < 5:
        raise IslemHatasi("Yapan ve neden (en az 5 karakter) gerekli", "gerekce")
    try:
        islem_id = int(islem_id)
    except (TypeError, ValueError):
        raise IslemHatasi("İşlem bulunamadı", "yok")
    verilen_plan = None
    if sonuc == "KISMI":
        try:
            verilen_plan = nakit.plan_coz(verilen)
        except nakit.NakitHatasi as e:
            raise IslemHatasi(e.mesaj, e.kod)
    return islem_id, sonuc, yapan, neden, verilen_plan


@kilit.yeniden_dene
def nakit_coz(islem_id, sonuc, yapan, neden, verilen=None):
    """Teslimi BELIRSIZ kalan nakit cekmeyi, operator kasetleri/kasayi SAYDIKTAN sonra TEK KISI sonuclandirir.

    sonuc: VERILDI (para musteriye gitti) | VERILMEDI (hic dagitilmadi; banknotlar kasette) |
           KISMI (verilen = fiilen cikan banknotlar, fark iade edilir; kalanlar iade kutusuna).
    yapan ve neden (en az 5 karakter) zorunludur ve hash zincirine yazilir.

    DIKKAT: tek kisilik yoldur (simulator ve acil durum icin). Uretimde yol, NAKIT_COZ yonetim talebidir
    (yonetim_talepleri): talep eden + baska onaylayan(lar). Ayni cekirdegi (_nakit_coz_islemi) kullanir.
    """
    islem_id, sonuc, yapan, neden, verilen_plan = nakit_coz_girdisi(islem_id, sonuc, yapan, neden, verilen)
    with transaction.atomic():
        kilit.zincir_kilidi()
        hata, ters, cevap = _nakit_coz_islemi(islem_id, sonuc, yapan, neden, verilen_plan)
    if hata:
        raise IslemHatasi(*hata)
    if ters:
        bildirim.ters_kayit(ters)
    return cevap


@kilit.yeniden_dene
def dagitici_ac(terminal_kodu, yapan, neden):
    """Teknik servis para dagiticiyi kontrol ettikten sonra terminalin nakit dagitimini yeniden acar."""
    yapan, neden = (yapan or "").strip(), (neden or "").strip()
    if len(yapan) < 2 or len(neden) < 5:
        raise IslemHatasi("Yapan ve neden (en az 5 karakter) gerekli", "gerekce")
    hata = None
    with transaction.atomic():
        kilit.zincir_kilidi()
        t = Terminal.objects.select_for_update().filter(kod=nakit.terminal_kodu(terminal_kodu)).first()
        if t is None:
            hata = ("Terminal bulunamadı", "terminal")
        else:
            t.nakit_dagitimi = True
            t.save(update_fields=["nakit_dagitimi"])
            kayit_ekle("dagitici_acildi", {"terminal": t.kod, "yapan": yapan[:60], "neden": neden[:200]})
    if hata:
        raise IslemHatasi(*hata)
    return {"terminal": t.kod, "nakit_dagitimi": True}


@kilit.yeniden_dene
def kaset_doldur(terminal_kodu, nominal, adet, yapan, iade_kutusunu_bosalt=False):
    """Fiziksel sayim/dolum sonrasi kasetteki banknot sayisini ayarlar (hash zincirine yazilir)."""
    yapan = (yapan or "").strip()
    if len(yapan) < 2:
        raise IslemHatasi("Yapan gerekli", "gerekce")
    hata, sonuc = None, None
    with transaction.atomic():
        kilit.zincir_kilidi()
        try:
            eski, yeni = nakit.kaset_ayarla(terminal_kodu, nominal, adet, iade_kutusunu_bosalt)
        except nakit.NakitHatasi as e:
            hata = (e.mesaj, e.kod)
        else:
            kod = nakit.terminal_kodu(terminal_kodu)
            kayit_ekle("kaset_ayarlandi", {"terminal": kod, "nominal": int(nominal), "yapan": yapan[:60],
                                           "eski_adet": eski[0], "yeni_adet": yeni[0],
                                           "iade_kutusu_once": eski[1], "iade_kutusu_sonra": yeni[1]})
            sonuc = {"terminal": kod, "nominal": int(nominal), "eski_adet": eski[0], "yeni_adet": yeni[0]}
    if hata:
        raise IslemHatasi(*hata)
    return sonuc


# ------------------------------------------------------------------ kasa dairesi

@kilit.yeniden_dene
def kasa_acma_baslat(uid, pin, imza=None):
    """Yetkili, kasa dairesini acmak icin onay talebi baslatir.

    Kural ayarlardan gelir (KASA_ACMA_GEREKEN_ONAY, KASA_ACMA_EN_AZ_ROL). Talebi baslatan
    kendi talebini onaylayamaz; bu yuzden onaylayabilecek yeterli baska yetkili de olmali.
    """
    personel_butunlugunu_dogrula()
    personel = personel_dogrula(uid, pin, imza)
    if not rbac.yetki_var_mi(personel, rbac.Yetki.KASA_AC_BASLAT):
        kayit_ekle("rbac_reddi", {"personel_id": personel.id, "yetki": rbac.Yetki.KASA_AC_BASLAT})
        raise IslemHatasi("Bu rol kasa açma talebi başlatamaz", "rol_yetersiz")
    kasa = _kasa_hesabi()
    gereken = int(settings.KASA_ACMA_GEREKEN_ONAY)
    en_az = int(settings.KASA_ACMA_EN_AZ_ROL)
    adaylar = Personel.objects.filter(aktif=True, bloke=False).exclude(id=personel.id)
    if gereken > adaylar.count() or not adaylar.filter(rol__gte=en_az).exists():
        kayit_ekle("kasa_acma_reddi", {"personel_id": personel.id, "sebep": "yeterli onaylayici yok"})
        raise IslemHatasi("Yeterli yetkili yok", "yetkili_yok")

    with transaction.atomic():
        kilit.zincir_kilidi()
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
        "dagitim": talep.islem.dagitim_plani,
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


@kilit.yeniden_dene
def talep_durumu(talep_id):
    """Talebin durumu. Okuma KILITSIZDIR (terminal saniyede bir sorar); yalnizca suresi dolmus bir talebi
    kapatmak icin zincir ve satir kilidi alinir."""
    talep_id = _talep_no(talep_id)
    talep = OnayTalebi.objects.select_related("islem").filter(id=talep_id).first()
    if talep is None:
        raise IslemHatasi("Talep bulunamadı", "yok")
    if talep.durum == OnayTalebi.Durum.BEKLIYOR and simdi() > talep.son_tarih:
        with transaction.atomic():
            kilit.zincir_kilidi()
            talep = OnayTalebi.objects.select_for_update().select_related("islem").get(id=talep_id)
            _suresi_dolduysa_kapat(talep)
    return _ozet(talep)


def suresi_dolanlari_kapat():
    for talep_id in OnayTalebi.objects.filter(
        durum=OnayTalebi.Durum.BEKLIYOR, son_tarih__lt=simdi()
    ).values_list("id", flat=True):
        talep_durumu(talep_id)
    belirsizleri_isaretle()


def bekleyen_talep():
    """En eski, suresi dolmamis talebi dondurur; yoksa None."""
    suresi_dolanlari_kapat()
    talep = (OnayTalebi.objects.select_related("islem")
             .filter(durum=OnayTalebi.Durum.BEKLIYOR).order_by("id").first())
    return _ozet(talep) if talep else None


@kilit.yeniden_dene
def onay_karar(talep_id, uid, pin, karar, imza=None):
    """karar: 'ONAY' veya 'RED'. Sonuc ozet sozlugu; kural ihlali IslemHatasi."""
    karar = Onay.Karar.RED if str(karar).upper() in ("RED", "H") else Onay.Karar.ONAY
    talep_id = _talep_no(talep_id)
    personel_butunlugunu_dogrula()
    personel = personel_dogrula(uid, pin, imza)  # atomic disinda: hatali PIN sayaci kalici olsun
    hata = None
    kasa_acildi = None
    with transaction.atomic():
        kilit.zincir_kilidi()
        talep = (OnayTalebi.objects.select_for_update().select_related("islem")
                 .filter(id=talep_id).first())
        if talep is None:
            raise IslemHatasi("Talep bulunamadı", "yok")

        if not _suresi_dolduysa_kapat(talep) and talep.durum == OnayTalebi.Durum.BEKLIYOR:
            islem = talep.islem
            gerekli_yetki = (rbac.Yetki.KASA_AC_ONAYLA if islem.tur == Islem.Tur.KASA_ACMA
                             else rbac.Yetki.ISLEM_ONAYLA)
            if not rbac.yetki_var_mi(personel, gerekli_yetki):
                kayit_ekle("rbac_reddi", {"talep_id": talep.id, "personel_id": personel.id, "yetki": gerekli_yetki})
                hata = ("Bu rolün bu işlemi onaylama yetkisi yok", "rol_yetersiz")
            elif Onay.objects.filter(talep=talep, personel=personel).exists():
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
