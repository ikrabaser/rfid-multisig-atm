"""Terminal kaydı ve terminalin çevrimdışıyken biriktirdiği günlüğün (store-and-forward) yüklenmesi.

Bilinçli sınır: Çevrimdışı terminal PARA İŞLEMİ YAPAMAZ. PIN doğrulayıcıları ve bakiyeler yalnızca
sunucuda durur; terminalde saklamak "PIN'i Arduino'da hash'lememe" kararıyla çelişirdi. Çevrimdışıyken
olanlar (kart okutma denemeleri, çevrimdışı kalma süresi) denetim için günlüğe yazılır ve bağlantı
gelince buraya yüklenir.
"""
import re
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .hashlog import kayit_ekle
from .models import Terminal, TerminalGunlukKaydi
from .services import IslemHatasi

IZINLI_OLAYLAR = {"cevrimdisi_istek", "cevrimdisi_basladi", "cevrimdisi_bitti"}
MAKS_GIRDI = 200
KAYIT_ID_DESENI = re.compile(r"[0-9a-f]{32}")


def terminal_coz(kod):
    """Başlıktaki terminal kodunu doğrular. Başlık yoksa varsayılan terminal."""
    kod = (kod or settings.VARSAYILAN_TERMINAL).strip()
    terminal = Terminal.objects.filter(kod=kod, aktif=True).first()
    if terminal is None:
        raise IslemHatasi("Terminal tanımsız ya da kapalı", "terminal")
    return terminal


def gunluk_yukle(girdiler, terminal_kodu):
    """Terminalden gelen günlük satırlarını kaydeder. Aynı kayıt_id ikinci kez eklenmez."""
    if not isinstance(girdiler, list) or len(girdiler) > MAKS_GIRDI:
        raise IslemHatasi("Geçersiz günlük", "gunluk")

    simdi = timezone.now()
    kabul = tekrar = red = 0
    for g in girdiler:
        if not isinstance(g, dict):
            red += 1
            continue
        kayit_id = str(g.get("id", ""))
        olay = g.get("olay")
        veri = g.get("veri", {})
        try:
            zaman = parse_datetime(str(g.get("zaman", "")))
        except ValueError:
            zaman = None
        if (not KAYIT_ID_DESENI.fullmatch(kayit_id) or olay not in IZINLI_OLAYLAR
                or zaman is None or not isinstance(veri, dict)):
            red += 1
            continue
        if timezone.is_naive(zaman):
            zaman = timezone.make_aware(zaman)

        temiz = {str(k)[:20]: str(v)[:40] for k, v in list(veri.items())[:6]}
        guvenilir = (simdi - timedelta(days=7)) <= zaman <= (simdi + timedelta(minutes=5))
        _, yeni = TerminalGunlukKaydi.objects.get_or_create(
            kayit_id=kayit_id,
            defaults={
                "terminal": terminal_kodu, "terminal_zamani": zaman, "olay": olay,
                "veri": str(temiz), "guvenilir_zaman": guvenilir,
            },
        )
        if not yeni:
            tekrar += 1
            continue
        kayit_ekle("terminal_gunlugu", {
            "terminal_zamani": zaman.isoformat(), "olay": olay, "veri": temiz,
            "zaman_guvenilir": guvenilir,
        })
        kabul += 1
    return {"kabul": kabul, "tekrar": tekrar, "red": red}
