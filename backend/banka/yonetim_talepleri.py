"""Hassas yönetim işlemlerinde maker-checker (dört göz).

Neden: N-of-M onayın güvencesi, onaylayanların kim olduğuna bağlıdır. Yönetici tek başına sahte bir yetkili
ekleyebilir, PIN'ini bilip onay verebilir, bloke kartı açabilir ya da günlük limiti yükseltebilirse bütün düzen
boşa gider. Bu yüzden aşağıdaki işlemler doğrudan değiştirilemez; bir kullanıcı talep açar (maker), işlemin
ağırlığına göre 1-3 BAŞKA kullanıcı onaylar (checker), son onayda uygulanır:

    PERSONEL_EKLE, PERSONEL_ROL, PERSONEL_DURUM, PERSONEL_PIN_SIFIRLA, BLOKE_KALDIR, GUNLUK_LIMIT, TERMINAL_DURUM

Kurallar (politika değişikliğindekilerle aynı): talep eden kendi talebini onaylayamaz, aynı kullanıcı iki kez
sayılmaz, herhangi bir onaylayan reddedebilir, süresi dolan talep düşer, aynı hedef için tek bekleyen talep olur.
Korkuluklar: en az YONETIM_MIN_AKTIF_YETKILI aktif yetkili ve en az bir aktif müdür kalmalı, günlük limit
HESAP_GUNLUK_LIMIT_AZAMI'yi aşamaz. Koruyucu yönler (rol düşürme, pasif yapma, limit azaltma, terminal kapatma)
daha az onay ister; genişletici yönler daha çok.

PIN'ler asla talebe yazılmaz ve hiçbir kullanıcıya gösterilmez: uygulama anında rastgele üretilir ve yetkilinin
KENDİ telefonuna SMS ile iletilir (simülasyon).
"""
import json
import re
import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from . import bildirim, personel_butunlugu
from .hashlog import kayit_ekle
from .models import Hesap, Kart, Personel, Rol, Terminal, YonetimOyu, YonetimTalebi, uid_duzelt

T = YonetimTalebi.Tur
UID_DESENI = re.compile(r"[0-9a-f]{8,20}")
TELEFON_DESENI = re.compile(r"\+?\d{10,15}")
PERSONEL_TURLERI = {T.PERSONEL_EKLE, T.PERSONEL_ROL, T.PERSONEL_DURUM, T.PERSONEL_PIN_SIFIRLA}


class YonetimHatasi(Exception):
    def __init__(self, mesaj, kod="hata"):
        super().__init__(mesaj)
        self.mesaj = mesaj
        self.kod = kod


# ------------------------------------------------------------------ yardimcilar

def _int(deger, ad):
    try:
        return int(str(deger).strip())
    except (TypeError, ValueError):
        raise YonetimHatasi(f"{ad} sayı olmalı", "gecersiz")


def _bool(deger, ad):
    d = str(deger).strip().lower()
    if d in ("1", "true", "evet", "aktif", "e"):
        return True
    if d in ("0", "false", "hayir", "hayır", "pasif", "h"):
        return False
    raise YonetimHatasi(f"{ad}: aktif/pasif (true/false) olmalı", "gecersiz")


def _rol(deger):
    r = _int(deger, "Rol")
    if r not in (1, 2, 3):
        raise YonetimHatasi("Rol 1 (görevli), 2 (müdür yrd.) ya da 3 (müdür) olmalı", "gecersiz")
    return r


def _personel(personel_id):
    p = Personel.objects.filter(id=_int(personel_id, "Personel no")).first()
    if p is None:
        raise YonetimHatasi("Personel bulunamadı", "hedef")
    return p


def _onay(anahtar):
    return int(settings.YONETIM_ONAY_SAYILARI[anahtar])


def _rastgele_pin():
    """Tahmin edilmesi kolay PIN'lerden (aynı rakam, ardışık) kaçınır."""
    ardisik = {"0123", "1234", "2345", "3456", "4567", "5678", "6789", "9876", "8765", "7654", "6543", "5432", "4321", "3210"}
    while True:
        pin = f"{secrets.randbelow(10000):04d}"
        if len(set(pin)) > 1 and pin not in ardisik:
            return pin


def _telefon_gerekli(telefon, neden):
    telefon = str(telefon or "").strip()
    if not TELEFON_DESENI.fullmatch(telefon):
        raise YonetimHatasi(f"{neden}: geçerli bir telefon gerekli (ör. +905551112233)", "telefon")
    return telefon


def _yetkili_kumesi_dogrula(satirlar):
    """satirlar: [(id, rol, aktif)]. Değişiklik sonrası küme yeterli ve yönetilebilir kalmalı."""
    aktif = [s for s in satirlar if s[2]]
    if len(aktif) < settings.YONETIM_MIN_AKTIF_YETKILI:
        raise YonetimHatasi(f"En az {settings.YONETIM_MIN_AKTIF_YETKILI} aktif yetkili kalmalı", "gecersiz_degisiklik")
    if not any(s[1] == int(Rol.MUDUR) for s in aktif):
        raise YonetimHatasi("En az bir aktif müdür kalmalı", "gecersiz_degisiklik")


def _mevcut_satirlar():
    return [(p.id, int(p.rol), bool(p.aktif)) for p in Personel.objects.all()]


def _degistirilmis(satirlar, personel_id, rol=None, aktif=None):
    return [
        (i, rol if (i == personel_id and rol is not None) else r, aktif if (i == personel_id and aktif is not None) else a)
        for i, r, a in satirlar
    ]


# ------------------------------------------------------------------ islem turleri: hazirla (dogrula) ve uygula

def _hazirla_personel_ekle(ham):
    ad = str(ham.get("ad") or "").strip()
    if not 2 <= len(ad) <= 80:
        raise YonetimHatasi("Ad 2-80 karakter olmalı", "gecersiz")
    uid = uid_duzelt(ham.get("kart_uid"))
    if not UID_DESENI.fullmatch(uid):
        raise YonetimHatasi("Kart UID 8-20 karakterlik onaltılık (hex) olmalı", "gecersiz")
    if Personel.objects.filter(kart_uid=uid).exists() or Kart.objects.filter(uid=uid).exists():
        raise YonetimHatasi("Bu kart UID zaten tanımlı", "cakisma")
    rol = _rol(ham.get("rol"))
    telefon = _telefon_gerekli(ham.get("telefon"), "İlk PIN SMS ile iletilir")
    eposta = str(ham.get("eposta") or "").strip()
    if eposta:
        try:
            validate_email(eposta)
        except ValidationError:
            raise YonetimHatasi("Geçersiz e-posta", "gecersiz")
    return {
        "veri": {"ad": ad, "kart_uid": uid, "rol": rol, "eposta": eposta, "telefon": telefon},
        "ozet": f"Yetkili ekle: {ad} ({Rol(rol).label}), kart {uid}",
        "gereken": _onay("PERSONEL_EKLE_MUDUR" if rol == int(Rol.MUDUR) else "PERSONEL_EKLE"),
        "hedef": f"personel_ekle:{uid}",
    }


def _uygula_personel_ekle(veri):
    if Personel.objects.filter(kart_uid=veri["kart_uid"]).exists() or Kart.objects.filter(uid=veri["kart_uid"]).exists():
        raise YonetimHatasi("Bu kart UID artık kullanımda", "cakisma")
    pin = _rastgele_pin()
    p = Personel(ad=veri["ad"], kart_uid=veri["kart_uid"], rol=veri["rol"], eposta=veri["eposta"],
                 telefon=veri["telefon"], aktif=True)
    p.pin_ayarla(pin)
    p.save()
    return {"personel_id": p.id, "pin_ilet": (p, pin, "Yetkili kaydınız oluşturuldu")}


def _hazirla_personel_rol(ham):
    p = _personel(ham.get("personel_id"))
    yeni = _rol(ham.get("yeni_rol"))
    if yeni == int(p.rol):
        raise YonetimHatasi("Yetkili zaten bu rolde", "gecersiz")
    _yetkili_kumesi_dogrula(_degistirilmis(_mevcut_satirlar(), p.id, rol=yeni))
    yukselis = yeni > int(p.rol)
    anahtar = ("PERSONEL_ROL_YUKSELT_MUDUR" if yeni == int(Rol.MUDUR) else "PERSONEL_ROL_YUKSELT") if yukselis else "PERSONEL_ROL_DUSUR"
    return {
        "veri": {"personel_id": p.id, "yeni_rol": yeni, "eski_rol": int(p.rol)},
        "ozet": f"Rol değiştir: {p.ad}, {Rol(p.rol).label} → {Rol(yeni).label}",
        "gereken": _onay(anahtar),
        "hedef": f"personel:{p.id}",
    }


def _uygula_personel_rol(veri):
    p = Personel.objects.select_for_update().get(id=veri["personel_id"])
    if int(p.rol) != veri["eski_rol"]:
        raise YonetimHatasi("Yetkilinin rolü talepten sonra değişmiş", "durum_degisti")
    p.rol = veri["yeni_rol"]
    p.save(update_fields=["rol"])
    return {}


def _hazirla_personel_durum(ham):
    p = _personel(ham.get("personel_id"))
    aktif = _bool(ham.get("aktif"), "Durum")
    if aktif == bool(p.aktif):
        raise YonetimHatasi("Yetkili zaten bu durumda", "gecersiz")
    _yetkili_kumesi_dogrula(_degistirilmis(_mevcut_satirlar(), p.id, aktif=aktif))
    return {
        "veri": {"personel_id": p.id, "aktif": aktif},
        "ozet": f"Yetkiliyi {'aktif' if aktif else 'pasif'} yap: {p.ad}",
        "gereken": _onay("PERSONEL_AKTIF_ET" if aktif else "PERSONEL_PASIF_ET"),
        "hedef": f"personel:{p.id}",
    }


def _uygula_personel_durum(veri):
    p = Personel.objects.select_for_update().get(id=veri["personel_id"])
    p.aktif = veri["aktif"]
    p.save(update_fields=["aktif"])
    return {}


def _hazirla_pin_sifirla(ham):
    p = _personel(ham.get("personel_id"))
    _telefon_gerekli(p.telefon, "PIN yetkilinin kendi telefonuna iletilir; yetkilinin telefonu kayıtlı değil")
    return {
        "veri": {"personel_id": p.id},
        "ozet": f"PIN sıfırla: {p.ad}",
        "gereken": _onay("PERSONEL_PIN_SIFIRLA"),
        "hedef": f"personel:{p.id}",
    }


def _uygula_pin_sifirla(veri):
    p = Personel.objects.select_for_update().get(id=veri["personel_id"])
    pin = _rastgele_pin()
    p.pin_ayarla(pin)  # hatali deneme sayacini da sifirlar
    p.save(update_fields=["pin_hash", "hatali_deneme"])
    return {"pin_ilet": (p, pin, "PIN'iniz sıfırlandı")}


def _hazirla_bloke_kaldir(ham):
    hedef_turu = str(ham.get("hedef") or "").strip().lower()
    if hedef_turu == "personel":
        nesne = _personel(ham.get("id"))
        ad, anahtar = f"yetkili {nesne.ad}", "BLOKE_KALDIR_PERSONEL"
    elif hedef_turu == "kart":
        nesne = Kart.objects.filter(id=_int(ham.get("id"), "Kart no")).first()
        if nesne is None:
            raise YonetimHatasi("Kart bulunamadı", "hedef")
        ad, anahtar = f"kart {nesne.uid}", "BLOKE_KALDIR_KART"
    else:
        raise YonetimHatasi("Hedef türü 'personel' ya da 'kart' olmalı", "gecersiz")
    if not nesne.bloke:
        raise YonetimHatasi("Hedef zaten bloke değil", "gecersiz")
    return {
        "veri": {"hedef": hedef_turu, "id": nesne.id},
        "ozet": f"Bloke kaldır: {ad}",
        "gereken": _onay(anahtar),
        "hedef": f"bloke:{hedef_turu}:{nesne.id}",
    }


def _uygula_bloke_kaldir(veri):
    model = Personel if veri["hedef"] == "personel" else Kart
    nesne = model.objects.select_for_update().get(id=veri["id"])
    if not nesne.bloke:
        raise YonetimHatasi("Hedef artık bloke değil", "durum_degisti")
    nesne.bloke = False
    nesne.hatali_deneme = 0
    nesne.save(update_fields=["bloke", "hatali_deneme"])
    return {}


def _hazirla_gunluk_limit(ham):
    hesap = Hesap.objects.filter(hesap_no=str(ham.get("hesap_no") or "").strip(), tur=Hesap.Tur.MUSTERI).first()
    if hesap is None:
        raise YonetimHatasi("Müşteri hesabı bulunamadı", "hedef")
    try:
        yeni = Decimal(str(ham.get("yeni_limit")).strip().replace(",", ".")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise YonetimHatasi("Geçersiz limit", "gecersiz")
    azami = Decimal(str(settings.HESAP_GUNLUK_LIMIT_AZAMI))
    if not yeni.is_finite() or yeni <= 0 or yeni > azami:
        raise YonetimHatasi(f"Limit 0 ile {azami:,.2f} TL arasında olmalı", "gecersiz")
    if yeni == hesap.gunluk_limit:
        raise YonetimHatasi("Limit zaten bu değerde", "gecersiz")
    artis = yeni > hesap.gunluk_limit
    return {
        "veri": {"hesap_no": hesap.hesap_no, "yeni_limit": f"{yeni:.2f}", "eski_limit": f"{hesap.gunluk_limit:.2f}"},
        "ozet": f"Günlük limit: hesap {hesap.hesap_no}, {hesap.gunluk_limit:,.2f} → {yeni:,.2f} TL",
        "gereken": _onay("GUNLUK_LIMIT_ARTIR" if artis else "GUNLUK_LIMIT_AZALT"),
        "hedef": f"limit:{hesap.hesap_no}",
    }


def _uygula_gunluk_limit(veri):
    hesap = Hesap.objects.select_for_update().get(hesap_no=veri["hesap_no"])
    if f"{hesap.gunluk_limit:.2f}" != veri["eski_limit"]:
        raise YonetimHatasi("Limit talepten sonra değişmiş", "durum_degisti")
    hesap.gunluk_limit = Decimal(veri["yeni_limit"])
    hesap.save(update_fields=["gunluk_limit"])
    return {}


def _hazirla_terminal_durum(ham):
    t = Terminal.objects.filter(kod=str(ham.get("kod") or "").strip()).first()
    if t is None:
        raise YonetimHatasi("Terminal bulunamadı", "hedef")
    aktif = _bool(ham.get("aktif"), "Durum")
    if aktif == bool(t.aktif):
        raise YonetimHatasi("Terminal zaten bu durumda", "gecersiz")
    return {
        "veri": {"kod": t.kod, "aktif": aktif},
        "ozet": f"Terminali {'aç' if aktif else 'kapat'}: {t.kod}",
        "gereken": _onay("TERMINAL_AKTIF_ET" if aktif else "TERMINAL_PASIF_ET"),
        "hedef": f"terminal:{t.kod}",
    }


def _uygula_terminal_durum(veri):
    t = Terminal.objects.select_for_update().get(kod=veri["kod"])
    t.aktif = veri["aktif"]
    t.save(update_fields=["aktif"])
    return {}


ISLEYICILER = {
    T.PERSONEL_EKLE: (_hazirla_personel_ekle, _uygula_personel_ekle),
    T.PERSONEL_ROL: (_hazirla_personel_rol, _uygula_personel_rol),
    T.PERSONEL_DURUM: (_hazirla_personel_durum, _uygula_personel_durum),
    T.PERSONEL_PIN_SIFIRLA: (_hazirla_pin_sifirla, _uygula_pin_sifirla),
    T.BLOKE_KALDIR: (_hazirla_bloke_kaldir, _uygula_bloke_kaldir),
    T.GUNLUK_LIMIT: (_hazirla_gunluk_limit, _uygula_gunluk_limit),
    T.TERMINAL_DURUM: (_hazirla_terminal_durum, _uygula_terminal_durum),
}


# ------------------------------------------------------------------ talep / onay

def talep_dogrula(tur, ham, gerekce):
    """Talebi doğrular; {veri, ozet, gereken, hedef} döndürür."""
    if tur not in ISLEYICILER:
        raise YonetimHatasi("Geçersiz işlem türü", "tur")
    if len((gerekce or "").strip()) < 10:
        raise YonetimHatasi("Gerekçe en az 10 karakter olmalı", "gerekce")
    hazir = ISLEYICILER[tur][0](ham or {})
    if YonetimTalebi.objects.filter(durum=YonetimTalebi.Durum.BEKLIYOR, hedef=hazir["hedef"]).exists():
        raise YonetimHatasi("Bu hedef için zaten bekleyen bir talep var", "cakisan")
    return hazir


def talep_kaydet(talep, kullanici, ham):
    """Kaydedilmemiş talebi doğrular, sahibini/süresini atar, kaydeder ve loglar."""
    if not kullanici.has_perm("banka.yonetim_talep_et"):
        raise YonetimHatasi("Yönetim işlemi talep etme yetkiniz yok", "yetki")
    hazir = talep_dogrula(talep.tur, ham, talep.gerekce)
    talep.veri = json.dumps(hazir["veri"], ensure_ascii=False, sort_keys=True)
    talep.ozet = hazir["ozet"][:200]
    talep.hedef = hazir["hedef"]
    talep.gereken_onay = hazir["gereken"]
    talep.talep_eden = kullanici
    talep.durum = YonetimTalebi.Durum.BEKLIYOR
    talep.son_tarih = timezone.now() + timedelta(hours=settings.YONETIM_TALEP_SURE_SAAT)
    with transaction.atomic():
        talep.save()
        kayit_ekle("yonetim_talebi", {
            "talep_id": talep.id, "tur": talep.tur, "ozet": talep.ozet, "hedef": talep.hedef,
            "gereken_onay": talep.gereken_onay, "talep_eden": kullanici.get_username(),
            "gerekce": talep.gerekce[:300],
        })
    bildirim.yonetim_talebi_acildi(talep)
    return talep


def talep_olustur(kullanici, tur, ham, gerekce):
    return talep_kaydet(YonetimTalebi(tur=tur, gerekce=gerekce), kullanici, ham)


def _kapat(talep, durum):
    talep.durum = durum
    talep.kapanma = timezone.now()
    talep.save(update_fields=["durum", "kapanma"])


def _suresi_dolduysa_kapat(talep):
    if talep.durum == YonetimTalebi.Durum.BEKLIYOR and timezone.now() > talep.son_tarih:
        _kapat(talep, YonetimTalebi.Durum.SURESI_DOLDU)
        kayit_ekle("yonetim_talebi_suresi_doldu", {"talep_id": talep.id})
        return True
    return False


def suresi_dolanlari_kapat():
    for talep_id in YonetimTalebi.objects.filter(
        durum=YonetimTalebi.Durum.BEKLIYOR, son_tarih__lt=timezone.now()
    ).values_list("id", flat=True):
        with transaction.atomic():
            _suresi_dolduysa_kapat(YonetimTalebi.objects.select_for_update().get(id=talep_id))


def _uygula(talep):
    """İşlemi uygular (transaction.atomic() içinde). PIN SMS'leri için bekleyen işleri döndürür."""
    personel_ilgili = talep.tur in PERSONEL_TURLERI
    if personel_ilgili and personel_butunlugu.tutarli_mi() is False:
        raise YonetimHatasi(
            "Yetkili kayıtları son onaylı sürümden farklı (onaysız değişiklik). Önce incelenmeli.", "butunluk")
    veri = json.loads(talep.veri)
    hazirla, uygula = ISLEYICILER[talep.tur]
    hazirla(veri)  # durum talepten sonra degismis olabilir: yeniden dogrula
    sonuc = uygula(veri)
    if personel_ilgili:
        personel_butunlugu.goruntu_kaydet(f"yonetim_talebi #{talep.id}", talep_id=talep.id)
    return sonuc


def oy_ver(talep_id, kullanici, karar):
    """karar: 'ONAY' ya da 'RED'. Kural ihlali YonetimHatasi; sonuçta güncel talep döner."""
    if not kullanici.has_perm("banka.yonetim_onayla"):
        raise YonetimHatasi("Yönetim işlemini onaylama yetkiniz yok", "yetki")
    karar = YonetimOyu.Karar.RED if str(karar).upper() == "RED" else YonetimOyu.Karar.ONAY
    Durum = YonetimTalebi.Durum
    hata, sonradan = None, []
    with transaction.atomic():
        talep = YonetimTalebi.objects.select_for_update().filter(id=talep_id).first()
        if talep is None:
            raise YonetimHatasi("Talep bulunamadı", "yok")
        if not _suresi_dolduysa_kapat(talep) and talep.durum == Durum.BEKLIYOR:
            if talep.talep_eden_id == kullanici.id:
                kayit_ekle("yonetim_onay_reddi_kendi_talebi", {"talep_id": talep.id, "kullanici": kullanici.get_username()})
                hata = ("Kendi talebinizi onaylayamazsınız", "kendi_talebi")
            elif YonetimOyu.objects.filter(talep=talep, kullanici=kullanici).exists():
                kayit_ekle("yonetim_onay_reddi_tekrar_oy", {"talep_id": talep.id, "kullanici": kullanici.get_username()})
                hata = ("Bu talep için zaten oy kullandınız", "tekrar")
            elif karar == YonetimOyu.Karar.RED:
                YonetimOyu.objects.create(talep=talep, kullanici=kullanici, karar=karar)
                _kapat(talep, Durum.REDDEDILDI)
                kayit_ekle("yonetim_talebi_reddedildi", {"talep_id": talep.id, "kullanici": kullanici.get_username()})
            else:
                YonetimOyu.objects.create(talep=talep, kullanici=kullanici, karar=karar)
                oy_sayisi = talep.oylar.filter(karar=YonetimOyu.Karar.ONAY).count()
                kayit_ekle("yonetim_onayi", {"talep_id": talep.id, "kullanici": kullanici.get_username(),
                                              "onay": oy_sayisi, "gereken": talep.gereken_onay})
                if oy_sayisi >= talep.gereken_onay:
                    try:
                        with transaction.atomic():
                            sonuc = _uygula(talep)
                        _kapat(talep, Durum.UYGULANDI)
                        kayit_ekle("yonetim_talebi_uygulandi", {"talep_id": talep.id, "tur": talep.tur, "ozet": talep.ozet})
                        if sonuc.get("pin_ilet"):
                            sonradan.append(sonuc["pin_ilet"])
                    except YonetimHatasi as e:
                        _kapat(talep, Durum.BASARISIZ)
                        kayit_ekle("yonetim_talebi_uygulanamadi", {"talep_id": talep.id, "sebep": e.mesaj})
    for personel, pin, neden in sonradan:
        bildirim.pin_ilet(personel, pin, neden)  # commit sonrasi: PIN yalnizca yetkilinin kendi telefonuna
    if hata:
        raise YonetimHatasi(*hata)
    talep.refresh_from_db()
    return talep


def iptal(talep_id, kullanici):
    """Yalnızca talep sahibi, bekleyen talebini geri çekebilir."""
    with transaction.atomic():
        talep = YonetimTalebi.objects.select_for_update().filter(id=talep_id).first()
        if talep is None:
            raise YonetimHatasi("Talep bulunamadı", "yok")
        if talep.talep_eden_id != kullanici.id:
            raise YonetimHatasi("Yalnızca talep sahibi iptal edebilir", "yetki")
        if talep.durum != YonetimTalebi.Durum.BEKLIYOR:
            raise YonetimHatasi("Talep artık beklemiyor", "durum")
        _kapat(talep, YonetimTalebi.Durum.IPTAL)
        kayit_ekle("yonetim_talebi_iptal", {"talep_id": talep.id, "kullanici": kullanici.get_username()})
    return talep
