from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render

from . import anomali, services
from .hashlog import zinciri_dogrula
from .models import Hesap, HesapHareketi, Islem, LogKaydi, OnayTalebi


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
    }
    return render(request, "banka/panel.html", baglam)
