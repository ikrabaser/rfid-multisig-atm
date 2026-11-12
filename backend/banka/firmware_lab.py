"""Firmware Laboratuvarı'nın sunucu tarafı.

Tarayıcıda WebAssembly olarak çalışan GERÇEK firmware çekirdeği (arduino/atm_terminal), seri hattın karşı ucundaki köprüye
CRC'li çerçeveler yollar. Burada o çerçeveler gerçek köprü kodundan (bridge/cerceve.py `Oturum`: CRC, NAK, tekrar önbelleği)
geçirilir ve komutlar gerçek servis katmanına (`simulator.isle`: defter, onay kuralları, log, hash zinciri) iletilir.

Yalnızca geliştirme içindir ve simülatörle aynı kapıyı kullanır: SIMULATOR_AKTIF kapalıysa 404, superuser dışında 403.
"""
import json
import sys
import threading
from collections import OrderedDict
from pathlib import Path

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from . import simulator
from .views import simulator_kapisi, simulator_yetkisi

_KOPRU_DIZINI = Path(__file__).resolve().parents[2] / "bridge"
if str(_KOPRU_DIZINI) not in sys.path:
    sys.path.insert(0, str(_KOPRU_DIZINI))
import cerceve  # noqa: E402  (bridge/cerceve.py: protokol v2 çerçeveleme ve köprü oturumu)

EN_COK_OTURUM = 64
_oturumlar = OrderedDict()   # (kullanici_id, lab_id, terminal) -> (Oturum, kilit)
_kilit = threading.Lock()


def _oturum(kullanici_id, lab_id, terminal):
    """Laboratuvar sekmesi başına bir köprü oturumu (tekrar önbelleği sekmeye aittir). Bellekte tutulur; en eski atılır."""
    anahtar = (kullanici_id, lab_id, terminal)
    with _kilit:
        if anahtar in _oturumlar:
            _oturumlar.move_to_end(anahtar)
            return _oturumlar[anahtar]
        oturum = cerceve.Oturum(lambda yuk: simulator.isle(yuk, terminal), kati=True)
        _oturumlar[anahtar] = (oturum, threading.Lock())
        while len(_oturumlar) > EN_COK_OTURUM:
            _oturumlar.popitem(last=False)
        return _oturumlar[anahtar]


@simulator_kapisi
@simulator_yetkisi
@ensure_csrf_cookie
def sayfa(request):
    return render(request, "banka/firmware_lab.html", {"veri": {
        **simulator.sayfa_verisi(),
        "hat_url": "/firmware-lab/hat/",
        "serit_url": "/simulator/serit/",
    }})


@simulator_kapisi
@simulator_yetkisi
@require_POST
def hat(request):
    """Firmware'in hatta yazdığı bir satırı (çerçeve) köprüye verir; köprünün terminale yazacağı satırı döndürür."""
    try:
        govde = json.loads(request.body or b"{}")
        satir, lab_id, terminal = govde["cerceve"], govde["lab_id"], govde.get("terminal", "ATM-01")
        if not (isinstance(satir, str) and len(satir) <= 200 and isinstance(lab_id, str) and 0 < len(lab_id) <= 40
                and isinstance(terminal, str) and len(terminal) <= 20):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"hata": "Geçersiz istek"}, status=400)
    oturum, kilit = _oturum(request.user.pk, lab_id, terminal)
    with kilit:
        onceki = dict(oturum.istatistik)
        cevap = oturum.satir_isle(satir)
        sonra = dict(oturum.istatistik)
    return JsonResponse({
        "cevap": cevap,                                        # None: köprü cevap yazmaz (bozuk/NAK/yoksayılan satır)
        "onbellekten": sonra["tekrar"] > onceki["tekrar"],     # aynı sıra no + aynı yük: isteği YENİDEN ÇALIŞTIRMADAN cevap
        "istatistik": sonra,
    })
