"""Onay politikasında maker-checker (dört göz) ve politika bütünlüğü.

Üç katman:
  1. Talep/onay: Politika tablosu doğrudan değiştirilemez. Bir kullanıcı talep açar (maker),
     en az POLITIKA_DEGISIKLIK_ONAY başka kullanıcı onaylar (checker). Talep sahibi kendi talebini
     onaylayamaz, aynı kullanıcı iki kez sayılmaz, süresi dolan talep düşer.
  2. Korkuluklar: Onaylansa bile bazı kurallar ihlal edilemez (kume_dogrula): onaysız aralık en
     fazla POLITIKA_ONAYSIZ_AZAMI_TUTAR olabilir, aralıklar boşluksuz/çakışmasız olmalı, tutar
     arttıkça gereken onay/rol azalamaz. Bu sınırlar KOD/AYAR düzeyindedir, panelden değişmez.
  3. Bütünlük: Her onaylı değişiklikte politika tablosunun görüntüsü (özet) hash zincirine yazılır.
     Tablo bu görüntüden farklıysa (ör. veritabanına doğrudan müdahale), sistem KAPALI kalır:
     her çekme/havale en sıkı kuralla (3 onay, müdür) istenir ve mutabakat hata verir.
"""
import hashlib
import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import bildirim
from .hashlog import kanonik_json, kayit_ekle
from .models import LogKaydi, Politika, PolitikaDegisikligi, PolitikaDegisiklikOyu

GECERLI_ROLLER = (1, 2, 3)


class PolitikaHatasi(Exception):
    def __init__(self, mesaj, kod="hata"):
        super().__init__(mesaj)
        self.mesaj = mesaj
        self.kod = kod


def _para(d):
    return f"{Decimal(d):.2f}"


# ------------------------------------------------------------------ kume (politika aralıkları)

def mevcut_kume():
    """Politika tablosunun anlık hali: [{id, alt, ust, onay, rol, aciklama}]"""
    return [
        {"id": p.id, "alt": _para(p.alt_limit),
         "ust": None if p.ust_limit is None else _para(p.ust_limit),
         "onay": p.gereken_onay, "rol": int(p.en_az_rol), "aciklama": p.aciklama}
        for p in Politika.objects.order_by("alt_limit", "id")
    ]


def _sirala(kume):
    return sorted(kume, key=lambda p: (Decimal(p["alt"]), p["ust"] is None, Decimal(p["ust"] or "0")))


def _satir(p):
    return [p["alt"], p["ust"], p["onay"], p["rol"]]


def kume_ozeti(kume):
    """Kuralları belirleyen alanların (açıklama ve numara hariç) SHA-256 özeti."""
    return hashlib.sha256(kanonik_json([_satir(p) for p in _sirala(kume)]).encode("utf-8")).hexdigest()


def _bagla(kume):
    """Her aralığın üst limitini bir sonraki aralığın alt limitinden türetir (boşluk/çakışma olamaz)."""
    sirali = _sirala(kume)
    return [{**p, "ust": sirali[i + 1]["alt"] if i + 1 < len(sirali) else None} for i, p in enumerate(sirali)]


def _normalle(yeni):
    """Yeni aralık değerleri. Üst limit verilmez: bir sonraki aralığın alt limiti belirler."""
    try:
        alt = Decimal(str(yeni.get("alt"))).quantize(Decimal("0.01"))
        onay = int(yeni.get("onay"))
        rol = int(yeni.get("rol"))
    except (InvalidOperation, TypeError, ValueError):
        raise PolitikaHatasi("Alt limit, gereken onay ve rol zorunlu ve geçerli olmalı", "eksik")
    return {"alt": _para(alt), "ust": None, "onay": onay, "rol": rol,
            "aciklama": str(yeni.get("aciklama") or "")[:120]}


def uygulanmis_kume(islem, hedef_id, yeni, mevcut):
    """Değişiklik uygulansaydı ortaya çıkacak aralık kümesi (üst limitler yeniden bağlanmış)."""
    kume = [dict(p) for p in mevcut]
    if islem == PolitikaDegisikligi.Islem.EKLE:
        kume.append({"id": None, **yeni})
        return _bagla(kume)
    indeks = next((i for i, p in enumerate(kume) if p["id"] == hedef_id), None)
    if indeks is None:
        raise PolitikaHatasi("Hedef politika bulunamadı", "hedef")
    if islem == PolitikaDegisikligi.Islem.SIL:
        kume.pop(indeks)
    else:
        kume[indeks] = {"id": hedef_id, **yeni}
    return _bagla(kume)


def kume_dogrula(kume):
    """Korkuluklar. İhlal varsa PolitikaHatasi (tüm ihlaller tek mesajda)."""
    if not kume:
        raise PolitikaHatasi("En az bir politika aralığı olmalı", "gecersiz_politika")
    hatalar = []
    sirali = _sirala(kume)
    cap = Decimal(str(settings.POLITIKA_ONAYSIZ_AZAMI_TUTAR))

    for p in sirali:
        alt = Decimal(p["alt"])
        ust = None if p["ust"] is None else Decimal(p["ust"])
        etiket = f"{alt:,.2f}–{'∞' if ust is None else f'{ust:,.2f}'}"
        if alt < 0:
            hatalar.append(f"{etiket}: alt limit negatif olamaz")
        if ust is not None and ust <= alt:
            hatalar.append(f"{etiket}: üst limit alt limitten büyük olmalı")
        if not 0 <= p["onay"] <= settings.POLITIKA_MAKS_ONAY:
            hatalar.append(f"{etiket}: gereken onay 0–{settings.POLITIKA_MAKS_ONAY} arası olmalı")
        if p["rol"] not in GECERLI_ROLLER:
            hatalar.append(f"{etiket}: geçersiz rol")
        if p["onay"] == 0 and (ust is None or ust > cap):
            hatalar.append(f"{etiket}: onaysız aralık en fazla {cap:,.2f} TL olabilir")

    alt_limitler = [p["alt"] for p in sirali]
    if len(set(alt_limitler)) != len(alt_limitler):
        hatalar.append("Aynı alt limit birden fazla aralıkta kullanılamaz")
    if Decimal(sirali[0]["alt"]) != 0:
        hatalar.append("İlk aralık 0'dan başlamalı")
    for a, b in zip(sirali, sirali[1:]):
        if a["ust"] is None or Decimal(a["ust"]) != Decimal(b["alt"]):
            hatalar.append(f"Aralıklar bitişik olmalı (boşluk ya da çakışma var: {a['alt']} / {b['alt']})")
        if b["onay"] < a["onay"] or b["rol"] < a["rol"]:
            hatalar.append("Tutar arttıkça gereken onay sayısı ve rol azalamaz")
    if sirali[-1]["ust"] is not None:
        hatalar.append("Son aralığın üst limiti boş (sınırsız) olmalı")
    if sirali[-1]["onay"] < settings.POLITIKA_EN_UST_ARALIK_MIN_ONAY:
        hatalar.append(f"En üst aralık en az {settings.POLITIKA_EN_UST_ARALIK_MIN_ONAY} onay istemeli")

    if hatalar:
        raise PolitikaHatasi("; ".join(dict.fromkeys(hatalar)), "gecersiz_politika")


# ------------------------------------------------------------------ butunluk (onayli goruntu)

def goruntu_kaydet(neden, **ek):
    """Şu anki politika tablosunu 'onaylı sürüm' olarak hash zincirine yazar."""
    kume = _sirala(mevcut_kume())
    return kayit_ekle("politika_goruntusu", {
        "ozet": kume_ozeti(kume), "politikalar": [_satir(p) for p in kume], "neden": neden, **ek,
    })


def son_onayli_ozet():
    kayit = LogKaydi.objects.filter(olay_turu="politika_goruntusu").order_by("-sira").first()
    if kayit is None:
        return None
    try:
        return json.loads(kayit.veri)["ozet"]
    except (ValueError, KeyError):
        return None


def goruntu_tutarli_mi():
    """True: tablo son onaylı sürümle aynı. False: farklı (onaysız değişiklik). None: onaylı sürüm yok."""
    ozet = son_onayli_ozet()
    if ozet is None:
        return None
    return ozet == kume_ozeti(mevcut_kume())


def butunluk_durumu():
    return {None: "baslangic_yok", True: "tutarli", False: "tutarsiz"}[goruntu_tutarli_mi()]


def ihlal_kaydet():
    """Onaysız değişiklik tespit edildi: loga yaz (saatte bir, log şişmesin)."""
    sinir = timezone.now() - timedelta(hours=1)
    if LogKaydi.objects.filter(olay_turu="politika_butunluk_ihlali", zaman__gte=sinir).exists():
        return
    kayit_ekle("politika_butunluk_ihlali", {
        "beklenen_ozet": son_onayli_ozet(), "mevcut_ozet": kume_ozeti(mevcut_kume()),
    })


# ------------------------------------------------------------------ talep / onay

def talep_dogrula(islem, hedef_id, yeni, gerekce):
    """Talebin kendisini doğrular; normalleştirilmiş yeni değeri döndürür (SIL için None)."""
    if islem not in PolitikaDegisikligi.Islem.values:
        raise PolitikaHatasi("Geçersiz değişiklik türü", "islem")
    if len((gerekce or "").strip()) < 10:
        raise PolitikaHatasi("Gerekçe en az 10 karakter olmalı", "gerekce")
    if islem != PolitikaDegisikligi.Islem.EKLE and hedef_id is None:
        raise PolitikaHatasi("Güncelleme ve silme için hedef politika no gerekli", "hedef")
    if (islem != PolitikaDegisikligi.Islem.EKLE and PolitikaDegisikligi.objects.filter(
            durum=PolitikaDegisikligi.Durum.BEKLIYOR, hedef_id=hedef_id).exists()):
        raise PolitikaHatasi("Bu politika için zaten bekleyen bir talep var", "cakisan")
    yeni_n = None if islem == PolitikaDegisikligi.Islem.SIL else _normalle(yeni or {})
    kume_dogrula(uygulanmis_kume(islem, hedef_id, yeni_n, mevcut_kume()))
    return yeni_n


def _yeni_degerler(talep):
    return {"alt": talep.yeni_alt_limit, "onay": talep.yeni_gereken_onay,
            "rol": talep.yeni_en_az_rol, "aciklama": talep.yeni_aciklama}


def talep_kaydet(talep, kullanici):
    """Kaydedilmemiş talebi doğrular, sahibini ve süresini atar, kaydeder ve loglar."""
    if not kullanici.has_perm("banka.politika_talep_et"):
        raise PolitikaHatasi("Politika değişikliği talep etme yetkiniz yok", "yetki")
    yeni_n = talep_dogrula(talep.islem, talep.hedef_id, _yeni_degerler(talep), talep.gerekce)

    talep.talep_eden = kullanici
    talep.gereken_onay = settings.POLITIKA_DEGISIKLIK_ONAY
    talep.durum = PolitikaDegisikligi.Durum.BEKLIYOR
    talep.son_tarih = timezone.now() + timedelta(hours=settings.POLITIKA_DEGISIKLIK_SURE_SAAT)
    with transaction.atomic():
        talep.save()
        kayit_ekle("politika_degisikligi_talebi", {
            "talep_id": talep.id, "islem": talep.islem, "hedef_id": talep.hedef_id, "yeni": yeni_n,
            "talep_eden": kullanici.get_username(), "gereken_onay": talep.gereken_onay,
            "gerekce": talep.gerekce[:300],
        })
    bildirim.politika_degisikligi_acildi(talep)
    return talep


def talep_olustur(kullanici, islem, hedef_id=None, yeni=None, gerekce=""):
    yeni = yeni or {}
    talep = PolitikaDegisikligi(
        islem=islem, hedef_id=hedef_id, yeni_alt_limit=yeni.get("alt"),
        yeni_gereken_onay=yeni.get("onay"), yeni_en_az_rol=yeni.get("rol"),
        yeni_aciklama=yeni.get("aciklama", ""), gerekce=gerekce,
    )
    return talep_kaydet(talep, kullanici)


def _kapat(talep, durum):
    talep.durum = durum
    talep.kapanma = timezone.now()
    talep.save(update_fields=["durum", "kapanma"])


def _suresi_dolduysa_kapat(talep):
    if talep.durum == PolitikaDegisikligi.Durum.BEKLIYOR and timezone.now() > talep.son_tarih:
        _kapat(talep, PolitikaDegisikligi.Durum.SURESI_DOLDU)
        kayit_ekle("politika_degisikligi_suresi_doldu", {"talep_id": talep.id})
        return True
    return False


def suresi_dolanlari_kapat():
    for talep_id in PolitikaDegisikligi.objects.filter(
        durum=PolitikaDegisikligi.Durum.BEKLIYOR, son_tarih__lt=timezone.now()
    ).values_list("id", flat=True):
        with transaction.atomic():
            talep = PolitikaDegisikligi.objects.select_for_update().get(id=talep_id)
            _suresi_dolduysa_kapat(talep)


def _uygula(talep):
    """Değişikliği uygular ve yeni görüntüyü zincire yazar. transaction.atomic() içinde çağrılır."""
    if goruntu_tutarli_mi() is False:
        raise PolitikaHatasi(
            "Politika tablosu son onaylı sürümden farklı (onaysız değişiklik). Önce incelenmeli.", "butunluk")
    islem = PolitikaDegisikligi.Islem
    yeni = None if talep.islem == islem.SIL else _normalle(_yeni_degerler(talep))
    kume_dogrula(uygulanmis_kume(talep.islem, talep.hedef_id, yeni, mevcut_kume()))  # durum degismis olabilir

    onceki = kume_ozeti(mevcut_kume())
    if talep.islem == islem.EKLE:
        Politika.objects.create(
            alt_limit=Decimal(yeni["alt"]), gereken_onay=yeni["onay"], en_az_rol=yeni["rol"],
            aciklama=yeni["aciklama"])
    elif talep.islem == islem.GUNCELLE:
        p = Politika.objects.select_for_update().get(id=talep.hedef_id)
        p.alt_limit = Decimal(yeni["alt"])
        p.gereken_onay, p.en_az_rol, p.aciklama = yeni["onay"], yeni["rol"], yeni["aciklama"]
        p.save()
    else:
        Politika.objects.filter(id=talep.hedef_id).delete()
    _tabloyu_bagla()
    goruntu_kaydet(f"politika_degisikligi #{talep.id}", talep_id=talep.id, onceki_ozet=onceki)


def _tabloyu_bagla():
    """Tablodaki üst limitleri bir sonraki aralığın alt limitine eşitler."""
    satirlar = list(Politika.objects.select_for_update().order_by("alt_limit", "id"))
    for i, p in enumerate(satirlar):
        ust = satirlar[i + 1].alt_limit if i + 1 < len(satirlar) else None
        if p.ust_limit != ust:
            p.ust_limit = ust
            p.save(update_fields=["ust_limit"])


def oy_ver(talep_id, kullanici, karar):
    """karar: 'ONAY' ya da 'RED'. Kural ihlali PolitikaHatasi; sonuçta güncel talep döner."""
    if not kullanici.has_perm("banka.politika_onayla"):
        raise PolitikaHatasi("Politika değişikliğini onaylama yetkiniz yok", "yetki")
    karar = PolitikaDegisiklikOyu.Karar.RED if str(karar).upper() == "RED" else PolitikaDegisiklikOyu.Karar.ONAY
    Durum = PolitikaDegisikligi.Durum
    hata = None
    with transaction.atomic():
        talep = PolitikaDegisikligi.objects.select_for_update().filter(id=talep_id).first()
        if talep is None:
            raise PolitikaHatasi("Talep bulunamadı", "yok")
        if not _suresi_dolduysa_kapat(talep) and talep.durum == Durum.BEKLIYOR:
            if talep.talep_eden_id == kullanici.id:
                kayit_ekle("politika_onay_reddi_kendi_talebi",
                           {"talep_id": talep.id, "kullanici": kullanici.get_username()})
                hata = ("Kendi talebinizi onaylayamazsınız", "kendi_talebi")
            elif PolitikaDegisiklikOyu.objects.filter(talep=talep, kullanici=kullanici).exists():
                kayit_ekle("politika_onay_reddi_tekrar_oy",
                           {"talep_id": talep.id, "kullanici": kullanici.get_username()})
                hata = ("Bu talep için zaten oy kullandınız", "tekrar")
            elif karar == PolitikaDegisiklikOyu.Karar.RED:
                PolitikaDegisiklikOyu.objects.create(talep=talep, kullanici=kullanici, karar=karar)
                _kapat(talep, Durum.REDDEDILDI)
                kayit_ekle("politika_degisikligi_reddedildi",
                           {"talep_id": talep.id, "kullanici": kullanici.get_username()})
            else:
                PolitikaDegisiklikOyu.objects.create(talep=talep, kullanici=kullanici, karar=karar)
                oy_sayisi = talep.oylar.filter(karar=PolitikaDegisiklikOyu.Karar.ONAY).count()
                kayit_ekle("politika_degisikligi_onayi", {
                    "talep_id": talep.id, "kullanici": kullanici.get_username(),
                    "onay": oy_sayisi, "gereken": talep.gereken_onay})
                if oy_sayisi >= talep.gereken_onay:
                    try:
                        with transaction.atomic():
                            _uygula(talep)
                        _kapat(talep, Durum.UYGULANDI)
                        kayit_ekle("politika_degisikligi_uygulandi", {"talep_id": talep.id})
                    except PolitikaHatasi as e:
                        _kapat(talep, Durum.BASARISIZ)
                        kayit_ekle("politika_degisikligi_uygulanamadi", {"talep_id": talep.id, "sebep": e.mesaj})
    if hata:
        raise PolitikaHatasi(*hata)
    talep.refresh_from_db()
    return talep


def iptal(talep_id, kullanici):
    """Yalnızca talep sahibi, bekleyen talebini geri çekebilir."""
    with transaction.atomic():
        talep = PolitikaDegisikligi.objects.select_for_update().filter(id=talep_id).first()
        if talep is None:
            raise PolitikaHatasi("Talep bulunamadı", "yok")
        if talep.talep_eden_id != kullanici.id:
            raise PolitikaHatasi("Yalnızca talep sahibi iptal edebilir", "yetki")
        if talep.durum != PolitikaDegisikligi.Durum.BEKLIYOR:
            raise PolitikaHatasi("Talep artık beklemiyor", "durum")
        _kapat(talep, PolitikaDegisikligi.Durum.IPTAL)
        kayit_ekle("politika_degisikligi_iptal", {"talep_id": talep.id, "kullanici": kullanici.get_username()})
    return talep
