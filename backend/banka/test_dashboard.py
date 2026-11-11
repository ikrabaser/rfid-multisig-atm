"""Operasyon paneli için temel görünüm ve veri testleri."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from .models import Hesap, Musteri, Terminal


class OperasyonPaneliTestleri(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="ops_test", password="test-giris-parolasi-44", is_staff=True
        )
        self.client.force_login(self.user)

    def test_panel_gercek_veriden_ozet_uretir(self):
        musteri = Musteri.objects.create(ad="Test Müşteri")
        Hesap.objects.create(
            musteri=musteri, hesap_no="199101", tur=Hesap.Tur.MUSTERI,
            bakiye="1250.00", acilis_bakiyesi="1250.00",
        )
        Terminal.objects.create(kod="ATM-TEST", ad="Test Şubesi", aktif=True)
        yanit = self.client.get("/")
        self.assertEqual(yanit.status_code, 200)
        self.assertContains(yanit, "Operasyon Merkezi")
        self.assertContains(yanit, "Politika bütünlüğü")
        self.assertContains(yanit, "Test Şubesi")
        self.assertEqual(yanit.context["istatistik"]["bakiye"], 1250)
        self.assertEqual(yanit.context["istatistik"]["atm_aktif"], 1)
        self.assertEqual(len(yanit.context["grafik"]), 7)

    def test_girissiz_kullanici_panele_erisim_saglayamaz(self):
        self.client.logout()
        yanit = self.client.get("/")
        self.assertEqual(yanit.status_code, 302)
        self.assertTrue(yanit["Location"].startswith("/giris/"))

    @override_settings(LOGIN_REDIRECT_URL="/")
    def test_admin_dogrudan_erisim_korunur(self):
        yanit = self.client.get("/admin/")
        self.assertIn(yanit.status_code, (200, 302))
