"""Panelde "Kural ve ML risk skoru" bölümü: üç durum (kapalı, model yok, hazır) ve tablo içeriği."""
import json
from pathlib import Path

from django.test import override_settings

from . import ml_golge
from .test_ml_golge import kucuk_model
from .test_ml_panel import _MlTabani


@override_settings(MESAI_DISI_KURALI=False, RISK_MOTORU_AKTIF=True)
class MlPanelGorunumTestleri(_MlTabani):
    """Paneldeki "Kural ve ML risk skoru" bölümü: üç durum (kapalı, model yok, hazır) ve tablo içeriği."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model
        kullanici = get_user_model().objects.create_user(username="ml_panel", password="test-only-password", is_staff=True)
        self.client.force_login(kullanici)

    def panel(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        return r

    def test_kapaliyken_nasil_acilacagi_yazar_ve_tablo_yoktur(self):
        r = self.panel()
        self.assertContains(r, 'id="ml-risk"')
        self.assertContains(r, 'id="ml-kapali"')
        self.assertContains(r, "ML_RISK_MODU=golge")
        self.assertNotContains(r, 'id="ml-ozet"')

    def test_model_yoksa_uyari_sebebiyle_gosterilir(self):
        self.golge(model=False)
        r = self.panel()
        self.assertContains(r, 'id="ml-model-yok"')
        self.assertContains(r, "model dosyası yok")
        self.assertNotContains(r, 'id="ml-ozet"')

    def test_hazirken_sayimlar_ve_yalniz_ml_listesi(self):
        self.golge()
        yalniz = self.islem(0, 100, ml_gerekce="tutarın olağan tutara oranı (log): 4.49")
        self.islem(50, 100)
        self.islem(0, 5)
        r = self.panel()
        self.assertContains(r, 'id="ml-ozet"')
        self.assertContains(r, "Yalnız ML işaretledi")
        self.assertContains(r, "tutarın olağan tutara oranı (log): 4.49")
        yalniz_satir = f'data-sonuc="yalniz_ml"'
        self.assertContains(r, yalniz_satir, count=2)               # hem "yalnız ML" listesinde hem son işlemlerde
        self.assertContains(r, f"#{yalniz.id}")
        self.assertContains(r, ml_golge.modeli_yukle()[0].muhur()[:16])
        self.assertNotContains(r, 'id="ml-kural-kapali"')

    def test_hazirken_bos_durum_mesajlari(self):
        self.golge()
        r = self.panel()
        self.assertContains(r, "Henüz skorlanan işlem yok")
        self.assertContains(r, "kuralın geçirip ML'nin işaretlediği işlem yok")

    def test_kural_motoru_kapaliysa_uyari_cikar(self):
        self.golge()
        with override_settings(RISK_MOTORU_AKTIF=False):
            self.assertContains(self.panel(), 'id="ml-kural-kapali"')

    def test_nav_baglantisi_var(self):
        self.assertContains(self.panel(), 'href="#ml-risk"')

    # ---- Güvenlik durumu: model mührü satırı
    def test_guvenlik_durumunda_model_muhru_kapaliyken_kapali_yazar(self):
        r = self.panel()
        self.assertContains(r, 'id="ml-muhur"')
        self.assertContains(r, "ML model mührü")
        self.assertNotContains(r, "Zincirle eşleşiyor")

    def test_guvenlik_durumunda_gecerli_model_icin_yesil_satir(self):
        self.golge()
        r = self.panel()
        self.assertContains(r, "Zincirle eşleşiyor (" + ml_golge.modeli_yukle()[0].muhur()[:8] + ")")

    def test_guvenlik_durumunda_bozuk_model_uyari_verir(self):
        self.golge()
        m = kucuk_model(tohum=9)                                     # zincire yazılmamış başka bir model, kendi mührüyle
        Path(self.yol).write_text(json.dumps({"muhur": m.muhur(), "model": m.sozluge()}), encoding="utf-8")
        ml_golge._onbellek.clear()
        r = self.panel()
        self.assertContains(r, 'class="danger-text"')
        self.assertContains(r, "zincirdeki son eğitim kaydıyla eşleşmiyor")
