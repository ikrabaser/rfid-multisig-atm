"""Koprunun (bridge) konustugu JSON API. Hepsi POST, hepsi X-Kopru-Anahtari ister."""
import hmac
import json
from functools import wraps

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import services
from .hashlog import zinciri_dogrula
from .services import IslemHatasi


def _hata(mesaj, kod="hata", http=400, **ekstra):
    return JsonResponse({"ok": False, "hata": mesaj, "kod": kod, **ekstra}, status=http)


def kopru_endpoint(fn):
    @csrf_exempt
    @require_http_methods(["POST"])
    @wraps(fn)
    def sarmal(request, *args, **kwargs):
        if settings.API_SADECE_HTTPS and not request.is_secure():
            return _hata("HTTPS gerekli", "https", 403)
        anahtar = request.headers.get("X-Kopru-Anahtari", "")
        if not hmac.compare_digest(anahtar, settings.KOPRU_API_ANAHTARI):
            return _hata("Yetkisiz", "yetkisiz", 401)
        try:
            govde = json.loads(request.body or b"{}")
            if not isinstance(govde, dict):
                raise ValueError
        except ValueError:
            return _hata("Geçersiz JSON", "json")
        try:
            sonuc = fn(govde, *args, **kwargs)
        except IslemHatasi as e:
            return _hata(e.mesaj, e.kod, **e.ekstra)
        return JsonResponse({"ok": True, **sonuc})

    return sarmal


@kopru_endpoint
def kart(g):
    return {"tip": services.kart_tipi(g.get("uid"))}


@kopru_endpoint
def giris(g):
    return services.musteri_giris(g.get("uid"), g.get("pin"))


@kopru_endpoint
def bakiye(g):
    return services.bakiye_sorgula(g.get("token"))


@kopru_endpoint
def islem(g):
    return services.islem_baslat(g.get("token"), g.get("tur"), g.get("tutar"), g.get("hedef"), g.get("anahtar"))


@kopru_endpoint
def kasa_ac(g):
    return services.kasa_acma_baslat(g.get("uid"), g.get("pin"))


@kopru_endpoint
def onay(g):
    return services.onay_karar(g.get("talep_id"), g.get("uid"), g.get("pin"), g.get("karar", "ONAY"))


@kopru_endpoint
def talep(g):
    return services.talep_durumu(g.get("talep_id"))


@kopru_endpoint
def bekleyen(g):
    return {"talep": services.bekleyen_talep()}


@kopru_endpoint
def log_dogrula(g):
    return zinciri_dogrula()
