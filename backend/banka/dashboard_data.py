"""Operasyon paneli için salt-okunur özetler.

ATM kayıtlarının `aktif` alanı idari açık/kapalı durumudur. Bağlantı durumu ayrıdır: izleme.py köprüden gelen heartbeat'e
dayanır ("köprü terminali duyuyor"); röle, okuyucu ya da LCD sağlığı değildir. Cihaz telemetrisi ayrı bir fazdır.
"""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from . import izleme
from .models import ArizaVakasi, Hesap, Islem, Kaset, OnayTalebi, Terminal


def panel_ozeti(anomaliler):
    bugun = timezone.localdate()
    baslangic = bugun - timedelta(days=6)

    musteri_hesaplari = Hesap.objects.filter(tur=Hesap.Tur.MUSTERI)
    bakiye = musteri_hesaplari.aggregate(toplam=Sum("bakiye"))["toplam"] or Decimal("0.00")
    toplam_atm = Terminal.objects.count()
    aktif_atm = Terminal.objects.filter(aktif=True).count()
    bekleyen = OnayTalebi.objects.filter(durum=OnayTalebi.Durum.BEKLIYOR).count()
    belirsiz = Islem.objects.filter(teslim=Islem.Teslim.BELIRSIZ).count()

    gunluk = Islem.objects.filter(olusturma__date__gte=baslangic).annotate(
        gun=TruncDate("olusturma")
    ).values("gun").annotate(adet=Count("id")).order_by("gun")
    gun_sayilari = {satir["gun"]: satir["adet"] for satir in gunluk}
    tepe = max([gun_sayilari.get(baslangic + timedelta(days=i), 0) for i in range(7)] + [1])
    grafik = []
    for i in range(7):
        gun = baslangic + timedelta(days=i)
        adet = gun_sayilari.get(gun, 0)
        grafik.append({
            "etiket": gun.strftime("%d.%m"),
            "adet": adet,
            "yukseklik": round((adet / tepe) * 100),
        })

    terminal_listesi = []
    for terminal in Terminal.objects.all():
        kasetler = list(Kaset.objects.filter(terminal=terminal))
        nakit = sum((Decimal(k.nominal) * k.adet for k in kasetler), Decimal("0"))
        son = Islem.objects.filter(terminal=terminal.kod).order_by("-olusturma").first()
        terminal_listesi.append({
            "kod": terminal.kod,
            "ad": terminal.ad,
            "aktif": terminal.aktif,
            "dagitici_acik": terminal.nakit_dagitimi,
            "baglanti": izleme.baglanti_durumu(terminal),
            "nakit": nakit if kasetler else None,
            "son_islem": son.olusturma if son else None,
            "islem_sayisi": Islem.objects.filter(terminal=terminal.kod, olusturma__date=bugun).count(),
        })

    acik_vakalar = ArizaVakasi.objects.select_related("terminal", "atanan").filter(durum=ArizaVakasi.Durum.ACIK)
    son_cozulenler = ArizaVakasi.objects.select_related("terminal").filter(durum=ArizaVakasi.Durum.COZULDU)[:3]
    son_islemler = Islem.objects.select_related("hesap").order_by("-id")[:10]
    son_belirsizler = Islem.objects.select_related("hesap").filter(
        teslim=Islem.Teslim.BELIRSIZ
    ).order_by("-id")[:5]

    return {
        "istatistik": {
            "bakiye": bakiye,
            "musteri_hesap": musteri_hesaplari.count(),
            "atm_toplam": toplam_atm,
            "atm_aktif": aktif_atm,
            "bekleyen_onay": bekleyen,
            "belirsiz_cekim": belirsiz,
            "anomali": len(anomaliler),
            "acik_ariza": acik_vakalar.count(),
            "cevrimdisi_atm": sum(1 for t in terminal_listesi if t["baglanti"]["durum"] == izleme.CEVRIMDISI),
            "risk_esigi": settings.RISK_EK_ONAY_ESIGI,
        },
        "grafik": grafik,
        "terminal_ozetleri": terminal_listesi,
        "ariza_vakalari": acik_vakalar[:5],
        "cozulen_vakalar": son_cozulenler,
        "son_islemler_ozet": son_islemler,
        "belirsiz_islemler_ozet": son_belirsizler,
        "son_guncelleme": timezone.now(),
        "simulator_erisimi": settings.SIMULATOR_AKTIF,
    }
