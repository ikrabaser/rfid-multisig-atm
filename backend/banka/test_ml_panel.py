"""Panelde kural vs ML karşılaştırması: veri katmanı (dashboard_data.ml_karsilastirma). Görünüm testleri test_ml_panel_gorunum.py'dadır."""
import tempfile
from datetime import timedelta
from pathlib import Path

from django.test import TestCase, override_settings
from django.utils import timezone

from . import dashboard_data, ml_golge
from .models import Islem
from .test_ml_golge import kucuk_model
from .tests import Yardimci


@override_settings(MESAI_DISI_KURALI=False, RISK_MOTORU_AKTIF=True)
class _MlTabani(Yardimci, TestCase):
    """Ortak hazırlık: seed_demo, geçici model dosyası, gölge modu açma, skorlu işlem üretme."""
    def setUp(self):
        self.hazirla()
        self.dizin = tempfile.TemporaryDirectory()
        self.addCleanup(self.dizin.cleanup)
        self.yol = str(Path(self.dizin.name) / "model.json")
        ayar = override_settings(ML_RISK_MODEL_YOLU=self.yol)
        ayar.enable()
        self.addCleanup(ayar.disable)
        ml_golge._onbellek.clear()

    def golge(self, model=True):
        ayar = override_settings(ML_RISK_MODU="golge")
        ayar.enable()
        self.addCleanup(ayar.disable)
        if model:
            ml_golge.modeli_yaz(kucuk_model(), self.yol)

    def islem(self, kural, ml, tur="CEKME", **ek):
        return Islem.objects.create(hesap=self.ali, tur=tur, tutar=100, baslatan_uid="a0a0a001",
                                    risk_skoru=kural, ml_puan=ml, **ek)


@override_settings(MESAI_DISI_KURALI=False, RISK_MOTORU_AKTIF=True)
class MlKarsilastirmaVeriTestleri(_MlTabani):
    def test_kapali_modda_hicbir_sey_hesaplanmaz(self):
        self.islem(0, 100)
        veri = dashboard_data.ml_karsilastirma()
        self.assertEqual((veri["durum"], veri["sayim"], veri["son"], veri["yalniz_ml"]), ("kapali", None, [], []))
        self.assertEqual(veri["mod"], "kapali")

    def test_model_dosyasi_yoksa_sebep_gosterilir(self):
        self.golge(model=False)
        veri = dashboard_data.ml_karsilastirma()
        self.assertEqual(veri["durum"], "model_yok")
        self.assertIn("model dosyası yok", veri["sebep"])
        self.assertIsNone(veri["sayim"])

    def test_zincirle_eslesmeyen_model_hazir_sayilmaz(self):
        self.golge()
        Path(self.yol).write_text(Path(self.yol).read_text(encoding="utf-8").replace('"tohum":1', '"tohum":2'), encoding="utf-8")
        ml_golge._onbellek.clear()
        self.assertEqual(dashboard_data.ml_karsilastirma()["durum"], "model_yok")

    def test_hazir_ama_skorlanan_islem_yok(self):
        self.golge()
        veri = dashboard_data.ml_karsilastirma()
        self.assertEqual(veri["durum"], "hazir")
        self.assertEqual(veri["sayim"]["toplam"], 0)
        self.assertEqual((veri["son"], veri["yalniz_ml"]), ([], []))
        self.assertEqual(len(veri["muhur"]), 16)

    def test_iki_by_iki_sayim_ve_etiketler(self):
        self.golge()
        yalniz_ml = self.islem(0, 100)
        self.islem(50, 100)
        self.islem(50, 10)
        self.islem(0, 10)
        self.islem(30, 98)                                       # kural eşiğin (40) altında, ML eşiğin (99) altında
        veri = dashboard_data.ml_karsilastirma()
        self.assertEqual({k: veri["sayim"][k] for k in ("toplam", "ikisi", "yalniz_ml", "yalniz_kural", "hicbiri")},
                         {"toplam": 5, "ikisi": 1, "yalniz_ml": 1, "yalniz_kural": 1, "hicbiri": 2})
        self.assertEqual([k["islem"].id for k in veri["yalniz_ml"]], [yalniz_ml.id])
        etiketler = [k["etiket"] for k in veri["son"]]
        self.assertEqual(etiketler, ["hicbiri", "hicbiri", "yalniz_kural", "ikisi", "yalniz_ml"])    # en yeni başta

    def test_esikler_ayardan_gelir(self):
        self.golge()
        self.islem(0, 90)
        with override_settings(ML_RISK_ISARET_PUANI=90, RISK_EK_ONAY_ESIGI=40):
            veri = dashboard_data.ml_karsilastirma()
        self.assertEqual((veri["ml_esik"], veri["kural_esik"], veri["sayim"]["yalniz_ml"]), (90, 40, 1))

    def test_yalnizca_cekme_ve_havale_ve_son_7_gun(self):
        self.golge()
        eski = self.islem(0, 100)
        Islem.objects.filter(id=eski.id).update(olusturma=timezone.now() - timedelta(days=8))
        self.islem(0, 100, tur="YATIRMA")
        self.islem(0, 100, tur="HAVALE")
        self.islem(0, None)                                      # skorlanmamış (gölge öncesi) işlem
        veri = dashboard_data.ml_karsilastirma()
        self.assertEqual((veri["sayim"]["toplam"], veri["sayim"]["uygun"]), (1, 2))      # uygun: HAVALE + skorsuz CEKME

    def test_kural_motoru_kapaliyken_bayrak_dusuk(self):
        with override_settings(RISK_MOTORU_AKTIF=False):
            self.assertFalse(dashboard_data.ml_karsilastirma()["kural_aktif"])
        self.assertTrue(dashboard_data.ml_karsilastirma()["kural_aktif"])

    def test_panel_ozeti_ml_anahtarini_tasir(self):
        self.assertEqual(dashboard_data.panel_ozeti([])["ml"]["durum"], "kapali")

    def test_liste_uzunlugu_sinirlidir(self):
        self.golge()
        for _ in range(dashboard_data.ML_LISTE_SAYISI + 4):
            self.islem(0, 100)
        veri = dashboard_data.ml_karsilastirma()
        self.assertEqual((len(veri["yalniz_ml"]), len(veri["son"])), (dashboard_data.ML_LISTE_SAYISI,) * 2)
        self.assertEqual(veri["sayim"]["yalniz_ml"], dashboard_data.ML_LISTE_SAYISI + 4)
