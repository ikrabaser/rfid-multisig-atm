"""Operasyon paneli için salt-okunur özetler.

ATM kayıtlarının `aktif` alanı idari açık/kapalı durumudur. Bağlantı durumu ayrıdır: izleme.py köprüden gelen heartbeat'e
dayanır ("köprü terminali duyuyor"); röle, okuyucu ya da LCD sağlığı değildir. Cihaz telemetrisi ayrı bir fazdır.
"""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from . import izleme, ml_golge
from .models import ArizaVakasi, Hesap, Islem, Kaset, OnayTalebi, Terminal


ML_PENCERE_GUN = 7
ML_LISTE_SAYISI = 8


def _ml_etiket(kural, ml):
    if kural and ml:
        return "ikisi"
    if ml:
        return "yalniz_ml"
    if kural:
        return "yalniz_kural"
    return "hicbiri"


def ml_karsilastirma():
    """Gölge modda kaydedilen ML puanını kural skoruyla yan yana koyar. Karar vermez, yalnızca okur.

    "İşaretledi": kural için `RISK_EK_ONAY_ESIGI` ve üstü (işlem ek onaya düşer), ML için `ML_RISK_ISARET_PUANI` ve üstü (eğitimdeki
    normal işlemlerin en uç yüzdesi). Reddedilen işlemlerin satırı olmadığı için karşılaştırma uygulanan ve onaya giden işlemler üzerindedir.
    """
    kural_esik = settings.RISK_EK_ONAY_ESIGI
    ml_esik = settings.ML_RISK_ISARET_PUANI
    sonuc = {"mod": settings.ML_RISK_MODU, "durum": "kapali", "sebep": "", "kural_esik": kural_esik, "ml_esik": ml_esik,
             "kural_aktif": settings.RISK_MOTORU_AKTIF, "pencere_gun": ML_PENCERE_GUN,
             "sayim": None, "yalniz_ml": [], "son": []}
    if not ml_golge.aktif():
        return sonuc
    try:
        model, sebep = ml_golge.modeli_yukle()
    except Exception as hata:                     # panel hiçbir koşulda ML yüzünden açılmaz olmasın
        model, sebep = None, f"model denetlenemedi: {hata}"
    if model is None:
        sonuc.update(durum="model_yok", sebep=sebep)
        return sonuc
    sonuc.update(durum="hazir", muhur=model.muhur()[:16])

    pencere = timezone.now() - timedelta(days=ML_PENCERE_GUN)
    uygun = Islem.objects.filter(olusturma__gte=pencere, tur__in=[Islem.Tur.CEKME, Islem.Tur.HAVALE])
    skorlu = uygun.filter(ml_puan__isnull=False)
    k, m = Q(risk_skoru__gte=kural_esik), Q(ml_puan__gte=ml_esik)
    sayim = skorlu.aggregate(
        toplam=Count("id"), ikisi=Count("id", filter=k & m),
        yalniz_ml=Count("id", filter=m & ~k), yalniz_kural=Count("id", filter=k & ~m))
    sayim["hicbiri"] = sayim["toplam"] - sayim["ikisi"] - sayim["yalniz_ml"] - sayim["yalniz_kural"]
    sayim["uygun"] = uygun.count()
    sonuc["sayim"] = sayim

    secim = skorlu.select_related("hesap").order_by("-id")
    sonuc["yalniz_ml"] = [{"islem": i, "etiket": "yalniz_ml"} for i in secim.filter(m & ~k)[:ML_LISTE_SAYISI]]
    sonuc["son"] = [{"islem": i, "etiket": _ml_etiket(i.risk_skoru >= kural_esik, i.ml_puan >= ml_esik)}
                    for i in secim[:ML_LISTE_SAYISI]]
    return sonuc


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
        "ml": ml_karsilastirma(),
    }
