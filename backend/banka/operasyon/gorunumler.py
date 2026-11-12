"""Banka Operasyon Simülasyonu sayfaları (yönetim paneli).

Yetki: görmek için yönetim kullanıcısı (is_staff); başlatmak/iptal için superuser. `OPERASYON_SIM_AKTIF` kapalıysa (varsayılan:
DEBUG dışında) hiçbir uç yoktur (404). Başlatma yalnızca POST + CSRF; tarayıcı Centrifugo API anahtarını hiç görmez.
"""
from functools import wraps

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from ..models import SimulasyonKosusu
from ..views import personel_gerekli
from . import centrifugo, yurutucu
from .parametreler import SINIRLAR, ParametreHatasi, Parametreler

AKTIF = yurutucu.AKTIF_DURUMLAR

ALANLAR = [   # (ad, etiket, tür, ipucu)
    ("musteri_sayisi", "Sanal müşteri", "number", "1–500"),
    ("sure_sn", "Süre (sanal sn)", "number", "işlemler bu aralığa dağıtılır"),
    ("zaman_olcegi", "Zaman ölçeği", "number", "1 = gerçek zamanlı, 0 = beklemeden en hızlı"),
    ("isci_sayisi", "Eşzamanlı oturum", "number", "ATM oturumu (iş parçacığı)"),
    ("terminal_sayisi", "ATM sayısı", "number", "1–10"),
    ("ortalama_islem", "Müşteri başına işlem", "number", "ortalama"),
    ("tohum", "Tohum", "number", "aynı tohum = aynı plan"),
    ("hatali_pin_orani", "Hatalı PIN oranı", "number", "0–0,5"),
    ("onay_red_orani", "Onay red oranı", "number", "0–1"),
    ("onay_gecikme_sn", "Yetkili tepki süresi (sn)", "number", "ortalama, sanal sn"),
    ("teslim_sorun_orani", "Para alınmadı oranı", "number", "0–1"),
    ("sikisma_orani", "Dağıtıcı sıkışma oranı", "number", "0–0,2"),
]


def operasyon_kapisi(view):
    @wraps(view)
    def sarmal(request, *args, **kwargs):
        if not settings.OPERASYON_SIM_AKTIF:
            raise Http404("Operasyon simülasyonu kapalı")
        return view(request, *args, **kwargs)
    return sarmal


def superuser_gerekli(view):
    @wraps(view)
    def sarmal(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)
        if not (request.user.is_active and request.user.is_superuser):
            raise PermissionDenied("Simülasyonu yalnızca superuser başlatabilir")
        return view(request, *args, **kwargs)
    return sarmal


def _satir(k):
    """Listede gösterilecek işlem sayıları: bitmişse rapordan, çalışıyorsa son canlı ölçümden."""
    kaynak = k.ozet.get("toplam") or {"islem": k.son_durum.get("islem"), "basarisiz": k.son_durum.get("basarisiz")}
    k.islem_sayisi, k.basarisiz_sayisi = kaynak.get("islem"), kaynak.get("basarisiz")
    return k


def _liste_baglami(request, hata=None, girilen=None):
    varsayilan = Parametreler().sozluk()
    return {
        "kosular": [_satir(k) for k in SimulasyonKosusu.objects.all()[:15]],
        "alanlar": [{"ad": a, "etiket": e, "tur": t, "ipucu": i, "sinir": SINIRLAR.get(a),
                     "deger": (girilen or {}).get(a, varsayilan[a])} for a, e, t, i in ALANLAR],
        "gercek_hash": bool((girilen or {}).get("gercek_hash", False)),
        "aktif_kosu": SimulasyonKosusu.objects.filter(durum__in=AKTIF).first(),
        "baslatabilir": request.user.is_superuser, "hata": hata,
        "centrifugo": settings.CENTRIFUGO_AKTIF, "kuyruk_bellekte": settings.PROCRASTINATE_BAGLANTI == "memory",
    }


@operasyon_kapisi
@personel_gerekli
@ensure_csrf_cookie
def liste(request):
    return render(request, "banka/operasyon.html", _liste_baglami(request))


@operasyon_kapisi
@superuser_gerekli
@require_POST
def baslat(request):
    ham = {ad: request.POST[ad].strip().replace(",", ".") for ad, *_ in ALANLAR if request.POST.get(ad, "").strip() != ""}
    ham["gercek_hash"] = request.POST.get("gercek_hash") == "on"
    try:
        kosu = yurutucu.yeni_kosu(request.user.get_username(), ham)
    except (ParametreHatasi, ValueError) as e:
        return render(request, "banka/operasyon.html", _liste_baglami(request, hata=str(e), girilen=ham), status=400)
    from . import gorevler          # procrastinate yalnızca burada gerekir (içe aktarılamazsa site açılmaya devam eder)
    gorevler.kuyruga_at(kosu)
    return redirect("operasyon_kosu", kosu_id=kosu.id)


@operasyon_kapisi
@personel_gerekli
@ensure_csrf_cookie
def kosu(request, kosu_id):
    k = get_object_or_404(SimulasyonKosusu, id=kosu_id)
    return render(request, "banka/operasyon_kosu.html", {
        "kosu": k, "baslatabilir": request.user.is_superuser, "centrifugo": settings.CENTRIFUGO_AKTIF,
        "parametreler": sorted(k.parametreler.items()),
    })


@operasyon_kapisi
@personel_gerekli
@require_GET
def durum(request, kosu_id):
    k = get_object_or_404(SimulasyonKosusu, id=kosu_id)
    bitti = k.durum not in AKTIF
    cevap = JsonResponse({
        "id": k.id, "durum": k.durum, "durum_adi": k.get_durum_display(), "asama": k.asama, "hata": k.hata,
        "iptal_istendi": k.iptal_istendi, "son_durum": k.son_durum, "ozet": k.ozet if bitti else None,
    })
    cevap["Cache-Control"] = "no-store"
    return cevap


@operasyon_kapisi
@personel_gerekli
@require_GET
def jeton(request, kosu_id):
    """Bu koşunun kanalına abone olabilen kısa ömürlü Centrifugo bağlantı jetonu."""
    get_object_or_404(SimulasyonKosusu, id=kosu_id)
    if not settings.CENTRIFUGO_AKTIF:
        raise Http404("Centrifugo kapalı")
    cevap = JsonResponse({"jeton": centrifugo.baglanti_jetonu(request.user.get_username(), kosu_id),
                          "adres": settings.CENTRIFUGO_WS_URL, "kanal": centrifugo.kanal(kosu_id)})
    cevap["Cache-Control"] = "no-store"
    return cevap


@operasyon_kapisi
@superuser_gerekli
@require_POST
def iptal(request, kosu_id):
    k = get_object_or_404(SimulasyonKosusu, id=kosu_id)
    # Kuyrukta bekliyorsa (henüz başlamadı) doğrudan iptal; çalışıyorsa yürütücü bir sonraki ölçümde süreci durdurur.
    if SimulasyonKosusu.objects.filter(id=k.id, durum=SimulasyonKosusu.Durum.BEKLIYOR).update(
            durum=SimulasyonKosusu.Durum.IPTAL, bitis=timezone.now(), iptal_istendi=True):
        pass
    else:
        SimulasyonKosusu.objects.filter(id=k.id, durum=SimulasyonKosusu.Durum.CALISIYOR).update(iptal_istendi=True)
    return redirect("operasyon_kosu", kosu_id=k.id)
