import json
from functools import wraps

from django.conf import settings
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from . import dashboard_data, anomali, kart_butunlugu, personel_butunlugu, politika_degisikligi, services, simulator, webgiris
from .hashlog import zinciri_dogrula
from .models import (Bildirim, Hesap, HesapHareketi, Islem, LogKaydi, OnayTalebi, PolitikaDegisikligi,
                     Terminal, YonetimTalebi)


def personel_gerekli(view):
    """Giriş yapmış ve is_staff olan kullanıcı. Girişsiz: kendi giriş sayfamız; yetkisiz: 403 (döngü olmasın)."""
    @wraps(view)
    def sarmal(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)
        if not (request.user.is_active and request.user.is_staff):
            raise PermissionDenied("Bu sayfa yalnızca yönetim kullanıcıları içindir")
        return view(request, *args, **kwargs)
    return sarmal


def _guvenli_adres(request, adres):
    ok = url_has_allowed_host_and_scheme(adres, allowed_hosts={request.get_host()},
                                         require_https=request.is_secure())
    return adres if ok else settings.LOGIN_REDIRECT_URL


def giris_sayfa(request):
    sonraki = request.POST.get("next") or request.GET.get("next") or ""
    # Girişli kullanıcı otomatik yönlendirilmez: yetkisi olmayan sayfaya yönlendirip tekrar buraya dönmek döngü olurdu
    hata = None
    if request.method == "POST":
        user, hata = webgiris.giris_yap(request, request.POST.get("username"), request.POST.get("password"))
        if user is not None:
            return redirect(_guvenli_adres(request, sonraki))
    cevap = render(request, "banka/giris.html", {"hata": hata, "sonraki": sonraki, "oturum": request.user.is_authenticated,
                                                  "kullanici": request.POST.get("username", "")[:150]},
                   status=429 if hata == webgiris.KILIT_HATASI else 200)
    cevap["Cache-Control"] = "no-store"
    return cevap


def admin_giris_yonlendir(request):
    """/admin/login/ kilidi atlayan yan kapı olmasın: aynı giriş sayfasına yönlendirilir."""
    adres = settings.LOGIN_URL
    if request.GET.get("next"):
        from urllib.parse import urlencode
        adres += "?" + urlencode({"next": request.GET["next"]})
    return redirect(adres)


@require_POST
def cikis(request):
    webgiris.cikis_yap(request)
    return redirect(settings.LOGIN_URL)


@login_required
def sifre_degistir(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    basarili = False
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)   # bu oturum açık kalır, diğerleri düşer
        webgiris.sifre_degisti_kaydet(request, user)
        form = PasswordChangeForm(request.user)
        basarili = True
    return render(request, "banka/sifre_degistir.html", {"form": form, "basarili": basarili})


@personel_gerekli
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
    baglam.update(dashboard_data.panel_ozeti(baglam["anomaliler"]))
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
        "operator_url": "/simulator/operator/", "kart_dokun_url": "/simulator/kart-dokun/",
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
@require_POST
def simulator_kart_dokun(request):
    try:
        govde = json.loads(request.body or b"{}")
        if not isinstance(govde, dict):
            raise ValueError
    except ValueError:
        return JsonResponse({"hata": "Geçersiz istek"}, status=400)
    return JsonResponse(simulator.kart_dokun(govde.get("uid"), govde.get("mod", "gercek")))


@simulator_kapisi
@simulator_yetkisi
@require_GET
def simulator_telefon(request):
    return JsonResponse({"mesajlar": simulator.telefon_mesajlari()})
