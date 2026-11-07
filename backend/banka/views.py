from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render

from . import anomali, politika_degisikligi, services
from .hashlog import zinciri_dogrula
from .models import (Bildirim, Hesap, HesapHareketi, Islem, LogKaydi, OnayTalebi, PolitikaDegisikligi,
                     Terminal)


@staff_member_required
def panel(request):
    services.suresi_dolanlari_kapat()
    baglam = {
        "zincir": zinciri_dogrula(),
        "bekleyenler": OnayTalebi.objects.select_related("islem__hesap")
        .filter(durum=OnayTalebi.Durum.BEKLIYOR),
        "talepler": OnayTalebi.objects.select_related("islem__hesap").prefetch_related("onaylar__personel")[:10],
        "islemler": Islem.objects.select_related("hesap", "hedef_hesap")[:15],
        "hesaplar": Hesap.objects.select_related("musteri"),
        "loglar": LogKaydi.objects.order_by("-sira")[:15],
        "hareketler": HesapHareketi.objects.select_related("hesap")[:12],
        "anomaliler": anomali.tara(),
        "bildirimler": Bildirim.objects.all()[:10],
        "politika_durumu": {
            "tutarli": ("ok", "Politika tablosu son onaylı sürümle aynı."),
            "tutarsiz": ("kotu", "POLİTİKA TABLOSU ONAYLI SÜRÜMDEN FARKLI: sistem kapalı modda, her çekme/havale en sıkı kuralı ister."),
            "baslangic_yok": ("kotu", "Politika için onaylı başlangıç görüntüsü yok (python manage.py politika_goruntu_al)."),
        }[politika_degisikligi.butunluk_durumu()],
        "politika_talepleri": PolitikaDegisikligi.objects.select_related("talep_eden")[:8],
        "terminaller": Terminal.objects.all(),
    }
    return render(request, "banka/panel.html", baglam)
