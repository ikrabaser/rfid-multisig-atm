"""ML risk katmanı: GÖLGE MOD. Skor hesaplanır ve işlemle birlikte kaydedilir; karar ASLA değişmez.

  ML_RISK_MODU=kapali   (varsayılan) hiçbir şey yapmaz
  ML_RISK_MODU=golge    para çekme/havalede `Islem.ml_puan` ve `ml_gerekce` dolar, hash zincirine `ml_risk_golge` yazılır

Güvenlik ve dayanıklılık kuralları:
  * `skorla` hiçbir koşulda istisna fırlatmaz; model yok/bozuk/uyumsuzsa ya da hesap patlarsa None döner ve işlem eskisi gibi sürer.
  * Model dosyası SHA-256 mührü taşır ve eğitim anında mühür hash zincirine (`ml_model_egitildi`) yazılır. Yüklenirken hem dosyanın
    kendi mührü hem de zincirdeki SON eğitim kaydıyla eşleşmesi aranır: dosya (mührüyle birlikte) değiştirilse bile zincir yakalar.
  * Veritabanı sorguları savepoint içindedir; bir hata dıştaki işlem transaction'ını bozmaz.
Dürüst sınır: şimdilik model sentetik veriyle eğitilir (`ml_risk_egit`); gerçek müşteri davranışıyla yeniden eğitim ayrı bir adımdır.
"""
import json
import logging
import os
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import baglam
from .hashlog import kayit_ekle
from .ml_risk import aciklama, ozellikler
from .ml_risk.orman import IsolationForest
from .models import Islem, LogKaydi

log = logging.getLogger(__name__)

EGITIM_OLAYI = "ml_model_egitildi"
GOLGE_OLAYI = "ml_risk_golge"
GECMIS_GUN = 90                  # risk.py TUTAR_PENCERE_GUN ile aynı
GECMIS_EN_FAZLA = 300
PIN_PENCERE_DK = 30              # risk.py PIN_PENCERE_DK ile aynı

_onbellek = {}                   # yol -> (mtime_ns, model)
_son_uyari = [None]


def aktif():
    return settings.ML_RISK_MODU == "golge"


def _uyar(sebep):
    if _son_uyari[0] != sebep:      # aynı sebebi her işlemde tekrar yazma
        _son_uyari[0] = sebep
        log.warning("ML gölge modu devre dışı kaldı: %s", sebep)


def modeli_yaz(model, yol=None):
    """Mühürlü modeli dosyaya yazar ve mührü hash zincirine kaydeder. Dönüş: yol."""
    yol = str(yol or settings.ML_RISK_MODEL_YOLU)
    veri = {"muhur": model.muhur(), "model": model.sozluge()}
    gecici = yol + ".tmp"
    with open(gecici, "w", encoding="utf-8") as f:
        json.dump(veri, f, separators=(",", ":"))
    os.replace(gecici, yol)
    kayit_ekle(EGITIM_OLAYI, {"muhur": model.muhur(), "ozellikler": list(model.ozellik_kodlari),
                              "agac": len(model.agaclar), "tohum": model.tohum})
    _onbellek.pop(yol, None)
    return yol


def modeli_yukle(yol=None):
    """(model, None) ya da (None, sebep). Dosya mührü ve zincirdeki son eğitim kaydı doğrulanır."""
    yol = str(yol or settings.ML_RISK_MODEL_YOLU)
    try:
        damga = os.stat(yol).st_mtime_ns
    except OSError:
        return None, f"model dosyası yok ({yol})"
    onceki = _onbellek.get(yol)
    if onceki and onceki[0] == damga:
        model = onceki[1]
    else:
        try:
            with open(yol, encoding="utf-8") as f:
                veri = json.load(f)
            model = IsolationForest.sozlukten(veri["model"])
            if model.muhur() != veri["muhur"]:
                return None, "model mührü tutmuyor (dosya değiştirilmiş)"
        except (OSError, ValueError, KeyError, TypeError) as hata:
            return None, f"model okunamadı: {hata}"
        if tuple(model.ozellik_kodlari) != ozellikler.OZELLIK_KODLARI:
            return None, "model özellik listesi koddakiyle uyumsuz; yeniden eğit"
        _onbellek[yol] = (damga, model)
    son = LogKaydi.objects.filter(olay_turu=EGITIM_OLAYI).order_by("-sira").first()
    try:
        zincirdeki = json.loads(son.veri).get("muhur") if son else None
    except ValueError:
        zincirdeki = None
    if zincirdeki != model.muhur():
        return None, "model mührü zincirdeki son eğitim kaydıyla eşleşmiyor"
    return model, None


def _olay_ve_gecmis(kart, hesap, tur, tutar, hedef, an):
    yerel = timezone.localtime
    kayitlar = list(Islem.objects.filter(
        hesap=hesap, tur__in=[Islem.Tur.CEKME, Islem.Tur.HAVALE], durum=Islem.Durum.UYGULANDI,
        olusturma__gte=an - timedelta(days=GECMIS_GUN), olusturma__lt=an,
    ).select_related("hedef_hesap").order_by("-olusturma")[:GECMIS_EN_FAZLA])
    gecmis = [ozellikler.Olay(yerel(i.olusturma), float(i.tutar), i.tur, i.terminal,
                              i.hedef_hesap.hesap_no if i.hedef_hesap_id else None) for i in reversed(kayitlar)]
    pin = LogKaydi.objects.filter(
        olay_turu="pin_hatali", veri__contains=f'"kimlik":"{kart.uid}"',
        zaman__gte=an - timedelta(minutes=PIN_PENCERE_DK), zaman__lte=an).count()
    olay = ozellikler.Olay(yerel(an), float(tutar), str(tur), baglam.terminal(),
                           hedef.hesap_no if hedef is not None else None, float(hesap.bakiye), pin)
    return olay, gecmis


def skorla(kart, hesap, tur, tutar, hedef=None, an=None):
    """{"puan": 0-100, "skor": ham, "gerekce": [...], "muhur": ...} ya da None. Veritabanını yalnızca okur, istisna fırlatmaz."""
    if not aktif():
        return None
    try:
        with transaction.atomic():
            model, sebep = modeli_yukle()
            if model is None:
                _uyar(sebep)
                return None
            from .services import simdi  # testlerde simdi() oynatilabilsin
            olay, gecmis = _olay_ve_gecmis(kart, hesap, tur, tutar, hedef, an or simdi())
            x = ozellikler.ozellik_vektoru(olay, gecmis)
            _son_uyari[0] = None
            return {"puan": int(round(model.puan(x))), "skor": round(model.skor(x), 4),
                    "gerekce": aciklama.gerekce(model, x), "muhur": model.muhur()[:16]}
    except Exception:  # gölge mod işlemi asla bozmamalı
        log.exception("ML gölge skoru hesaplanamadı")
        return None


def gerekce_metni(ml):
    return "; ".join(ml["gerekce"])[:200] if ml else ""


def kayit_yaz(islem, ml, risk_sonucu):
    """Gölge skorunu kural kararıyla birlikte hash zincirine yazar. Hata işlemi bozmaz."""
    try:
        with transaction.atomic():
            kayit_ekle(GOLGE_OLAYI, {
                "islem_id": islem.id, "hesap_no": islem.hesap.hesap_no, "tur": islem.tur, "tutar": f"{islem.tutar:.2f}",
                "ml_puan": ml["puan"], "ml_skor": ml["skor"], "ml_gerekce": ml["gerekce"], "model": ml["muhur"],
                "kural_skoru": (risk_sonucu or {}).get("skor", 0), "kural_karari": (risk_sonucu or {}).get("karar", "GEC"),
            })
    except Exception:
        log.exception("ML gölge kaydı yazılamadı")
