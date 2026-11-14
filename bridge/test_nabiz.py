"""Gerçek köprünün (serial_bridge.Kopru) heartbeat'i: arka planda gider, hata ve yavaşlık PONG'u geciktirmez."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import requests

import cerceve
import serial_bridge


class KopruNabizTestleri(unittest.TestCase):
    def setUp(self):
        self.gecici = tempfile.TemporaryDirectory()
        self.addCleanup(self.gecici.cleanup)
        self.kopru = serial_bridge.Kopru("http://sunucu.test", "anahtar", terminal="ATM-07",
                                         kuyruk_yolu=Path(self.gecici.name) / "k.jsonl")

    def bekle(self, kosul, sn=2.0):
        bitis = time.time() + sn
        while time.time() < bitis:
            if kosul():
                return True
            time.sleep(0.01)
        return kosul()

    def test_ping_heartbeat_endpointine_arka_planda_gider(self):
        cagrilar = []
        with mock.patch.object(self.kopru.oturum, "post", side_effect=lambda *a, **k: cagrilar.append((a, k))):
            o = cerceve.Oturum(self.kopru.isle, kati=True, nabiz=self.kopru.nabiz)
            cevap = o.satir_isle(cerceve.kodla(1, "PING"))
            self.assertEqual(cerceve.coz(cevap), (1, "PONG"))
            self.assertTrue(self.bekle(lambda: len(cagrilar) == 1))
        self.assertEqual(cagrilar[0][0][0], "http://sunucu.test/api/heartbeat/")
        self.assertEqual(self.kopru.oturum.headers["X-Terminal-Id"], "ATM-07")

    def test_yavas_sunucu_pong_u_geciktirmez(self):
        serbest = threading.Event()
        with mock.patch.object(self.kopru.oturum, "post", side_effect=lambda *a, **k: serbest.wait(5)):
            o = cerceve.Oturum(self.kopru.isle, kati=True, nabiz=self.kopru.nabiz)
            t0 = time.monotonic()
            cevap = o.satir_isle(cerceve.kodla(2, "PING"))
            self.assertLess(time.monotonic() - t0, 0.5)               # heartbeat 5 sn sürse de cevap hemen döner
            self.assertEqual(cerceve.coz(cevap), (2, "PONG"))
            serbest.set()

    def test_sunucu_yoksa_hata_yutulur_ve_cevrimdisi_kuyruguna_yazilmaz(self):
        with mock.patch.object(self.kopru.oturum, "post", side_effect=requests.exceptions.ConnectionError("yok")):
            o = cerceve.Oturum(self.kopru.isle, kati=True, nabiz=self.kopru.nabiz)
            cevap = o.satir_isle(cerceve.kodla(3, "PING"))
            self.assertEqual(cerceve.coz(cevap), (3, "PONG"))
            self.assertTrue(self.bekle(lambda: not self.kopru._nabiz_calisiyor.locked()))
        self.assertFalse(self.kopru._cevrimdisi)                       # heartbeat kuyruk/çevrimdışı durumuna dokunmaz
        self.assertTrue(self.kopru.kuyruk.bos_mu())

    def test_devam_eden_heartbeat_varken_yenisi_baslamaz(self):
        serbest = threading.Event()
        sayac = []
        def post(*a, **k):
            sayac.append(1)
            serbest.wait(5)
        with mock.patch.object(self.kopru.oturum, "post", side_effect=post):
            self.kopru.nabiz()
            self.assertTrue(self.bekle(lambda: len(sayac) == 1))
            self.kopru.nabiz()
            self.kopru.nabiz()
            time.sleep(0.1)
            self.assertEqual(len(sayac), 1)
            serbest.set()


if __name__ == "__main__":
    unittest.main()
