"""Rol tabanlı erişim kontrolü (RBAC): iki düzlem.

1. Terminal (kart) düzlemi: Yetkililerin (Personel) rolleri açık bir yetki matrisine bağlıdır. Eskiden yalnızca
   "rol seviyesi" vardı; şimdi hangi rolün hangi eylemi yapabildiği burada tek yerde yazılıdır.
2. Yönetim (admin) düzlemi: Django kullanıcıları gruplarla yetkilendirilir. Grup tanımları KODDADIR
   (`rbac_kur` komutu admin'deki gruplara uygular) ve görev ayrımı (SoD) kuralları `denetim()` ile sınanır:
   aynı kişi hem talep eden (maker) hem onaylayan (checker) olamaz; denetçi hiçbir şeyi değiştiremez.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import models

from .models import Rol


# ------------------------------------------------------------------ 1. terminal (kart) duzlemi

class Yetki(models.TextChoices):
    ISLEM_ONAYLA = "ISLEM_ONAYLA", "Çekme/havale talebini onaylayabilir"
    KASA_AC_ONAYLA = "KASA_AC_ONAYLA", "Kasa açma talebini onaylayabilir"
    KASA_AC_BASLAT = "KASA_AC_BASLAT", "Kasa açma talebi başlatabilir"


ROL_YETKILERI = {
    Rol.GOREVLI: {Yetki.ISLEM_ONAYLA, Yetki.KASA_AC_ONAYLA},
    Rol.MUDUR_YRD: {Yetki.ISLEM_ONAYLA, Yetki.KASA_AC_ONAYLA, Yetki.KASA_AC_BASLAT},
    Rol.MUDUR: {Yetki.ISLEM_ONAYLA, Yetki.KASA_AC_ONAYLA, Yetki.KASA_AC_BASLAT},
}


def yetkiler(personel):
    return ROL_YETKILERI.get(Rol(personel.rol), set())


def yetki_var_mi(personel, yetki):
    return yetki in yetkiler(personel)


# ------------------------------------------------------------------ 2. yonetim (admin) duzlemi

TALEP_YONETIM_GOZLEM = ["view_yonetimtalebi", "view_personel", "view_hesap", "view_terminal", "view_kart"]

YONETIM_GRUPLARI = {
    "Politika Talep Eden": ["politika_talep_et", "view_politikadegisikligi", "view_politika"],
    "Politika Onaylayan": ["politika_onayla", "view_politikadegisikligi", "view_politika"],
    "Yönetim Talep Eden": ["yonetim_talep_et"] + TALEP_YONETIM_GOZLEM,
    "Yönetim Onaylayan": ["yonetim_onayla"] + TALEP_YONETIM_GOZLEM,
    "Denetçi": [
        "view_logkaydi", "view_hesaphareketi", "view_islem", "view_onaytalebi", "view_onay", "view_bildirim",
        "view_terminalgunlukkaydi", "view_politikadegisikligi", "view_politikadegisiklikoyu", "view_yonetimtalebi",
        "view_yonetimoyu", "view_politika", "view_personel", "view_hesap", "view_terminal",
    ],
}

# Aynı kişide bulunmaması gereken izin çiftleri (görev ayrımı)
GOREV_AYRIMI = [
    ("politika_talep_et", "politika_onayla"),
    ("yonetim_talep_et", "yonetim_onayla"),
]
DENETCI_GRUBU = "Denetçi"


def kur():
    """Grup tanımlarını veritabanına uygular (idempotent). Eksik izin kodu varsa hata verir."""
    for grup_adi, kodlar in YONETIM_GRUPLARI.items():
        izinler = Permission.objects.filter(content_type__app_label="banka", codename__in=kodlar)
        eksik = set(kodlar) - set(izinler.values_list("codename", flat=True))
        if eksik:
            raise ValueError(f"'{grup_adi}' grubu için bilinmeyen izin kodları: {sorted(eksik)}")
        grup, _ = Group.objects.get_or_create(name=grup_adi)
        grup.permissions.set(izinler)
    return list(YONETIM_GRUPLARI)


def _kodlar(kullanici):
    return {p.split(".", 1)[1] for p in kullanici.get_all_permissions() if p.startswith("banka.")}


def denetim():
    """Yönetim kullanıcılarının yetkilerini görev ayrımı kurallarına göre denetler."""
    kullanicilar = []
    ihlaller, denetci_yazanlar, grupsuz = [], [], []
    superuserlar = []
    for u in get_user_model().objects.filter(is_active=True, is_staff=True).order_by("username"):
        gruplar = sorted(u.groups.values_list("name", flat=True))
        kodlar = _kodlar(u)
        kullanicilar.append({"kullanici": u.get_username(), "superuser": u.is_superuser, "gruplar": gruplar})
        if u.is_superuser:
            superuserlar.append(u.get_username())
            continue  # superuser her izne sahiptir; ayri uyari olarak raporlanir
        if not gruplar:
            grupsuz.append(u.get_username())
        for a, b in GOREV_AYRIMI:
            if a in kodlar and b in kodlar:
                ihlaller.append({"kullanici": u.get_username(), "izinler": [a, b]})
        if DENETCI_GRUBU in gruplar:
            yazma = sorted(k for k in kodlar if not k.startswith("view_"))
            if yazma:
                denetci_yazanlar.append({"kullanici": u.get_username(), "izinler": yazma})
    eksik_gruplar = sorted(set(YONETIM_GRUPLARI) - set(Group.objects.values_list("name", flat=True)))
    return {
        "saglam": not (ihlaller or denetci_yazanlar or eksik_gruplar),
        "kullanicilar": kullanicilar,
        "gorev_ayrimi_ihlalleri": ihlaller,
        "denetci_yazma_izinleri": denetci_yazanlar,
        "eksik_gruplar": eksik_gruplar,
        "grupsuz_yonetim_kullanicilari": grupsuz,
        "superuserlar": superuserlar,
    }
