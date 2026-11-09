import json
from functools import wraps

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from . import anomali, kart_butunlugu, personel_butunlugu, politika_degisikligi, services, simulator
from .hashlog import zinciri_dogrula
from .models import (Bildirim, Hesap, HesapHareketi, Islem, LogKaydi, OnayTalebi, PolitikaDegisikligi,
                     Terminal, YonetimTalebi)


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
        "personel_durumu": {
            "tutarli": ("ok", "Yetkili kayıtları son onaylı sürümle aynı."),
            "tutarsiz": ("kotu", "YETKİLİ KAYITLARI ONAYLI SÜRÜMDEN FARKLI: onaylar ve kasa açma kapalı."),
            "baslangic_yok": ("kotu", "Yetkili kayıtları için onaylı başlangıç görüntüsü yok (python manage.py personel_goruntu_al)."),
        }[personel_butunlugu.durum()],
        "yonetim_talepleri": YonetimTalebi.objects.select_related("talep_eden")[:8],
        "kart_durumu": {
            "tutarli": ("ok", "Müşteri kartı kayıtları son onaylı sürümle aynı."),
            "tutarsiz": ("kotu", "MÜŞTERİ KARTI KAYITLARI ONAYLI SÜRÜMDEN FARKLI: onaysız eklenen/değişen kartlar kullanılamaz."),
            "baslangic_yok": ("kotu", "Müşteri kartları için onaylı başlangıç görüntüsü yok (python manage.py kart_goruntu_al)."),
        }[kart_butunlugu.durum()],
        "terminaller": Terminal.objects.all(),
    }
    return render(request, "banka/panel.html", baglam)


# ---------------------------------------------------------------- web ATM simulatoru

def simulator_yetkisi(view):
    """Simülatör gerçek işlemler (para hareketi) yaptırır: yalnızca superuser. Salt okunur roller (denetçi) giremez."""
    @wraps(view)
    def sarmal(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)
        if not (request.user.is_active and request.user.is_superuser):
            raise PermissionDenied("Simülatör yalnızca superuser içindir")
        return view(request, *args, **kwargs)
    return sarmal


def simulator_kapisi(view):
    """SIMULATOR_AKTIF kapaliysa (varsayilan: DEBUG disinda) hicbir simulator ucu yoktur (404)."""
    @wraps(view)
    def sarmal(request, *args, **kwargs):
        if not simulator.aktif_mi():
            raise Http404("Simülatör kapalı")
        return view(request, *args, **kwargs)
    return sarmal


@simulator_kapisi
@simulator_yetkisi
@ensure_csrf_cookie
def simulator_sayfa(request):
    return render(request, "banka/simulator.html", {"veri": {
        **simulator.sayfa_verisi(),
        "komut_url": "/simulator/komut/", "serit_url": "/simulator/serit/", "telefon_url": "/simulator/telefon/",
        "operator_url": "/simulator/operator/",
    }})


@simulator_kapisi
@simulator_yetkisi
@require_POST
def simulator_komut(request):
    try:
        govde = json.loads(request.body or b"{}")
        satir, terminal = govde["satir"], govde.get("terminal", "")
        if not isinstance(satir, str) or len(satir) > 300 or not isinstance(terminal, str) or len(terminal) > 20:
            raise ValueError
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"cevap": "HATA|Gecersiz istek"}, status=400)
    return JsonResponse({"cevap": simulator.isle(satir, terminal)})


@simulator_kapisi
@simulator_yetkisi
@require_GET
def simulator_serit(request):
    return JsonResponse(simulator.serit_verisi(zincir=request.GET.get("zincir") == "1"))


@simulator_kapisi
@simulator_yetkisi
@require_POST
def simulator_operator(request):
    try:
        govde = json.loads(request.body or b"{}")
        if not isinstance(govde, dict):
            raise ValueError
    except ValueError:
        return JsonResponse({"ok": False, "hata": "Geçersiz istek"}, status=400)
    return JsonResponse(simulator.operator_eylemi(request.user, govde))


@simulator_kapisi
@simulator_yetkisi
@require_GET
def simulator_telefon(request):
    return JsonResponse({"mesajlar": simulator.telefon_mesajlari()})
