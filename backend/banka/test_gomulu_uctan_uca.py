"""Gömülü yazılımın (C++ firmware çekirdeği) uçtan uca testi: donanım olmadan, gerçek parçalarla.

    firmware simülatörü (arduino/test/sim_calistir: GERÇEK terminal çekirdeği, sahte kart/tuş/LCD/röle)
        ⇄  CRC'li seri hat  ⇄  gerçek Python köprüsü (bridge/serial_bridge.py Kopru + cerceve.Oturum)
        ⇄  gerçek Django API'si + veritabanı (onay kuralları, defter, hash zinciri)

Doğrulananlar: para çekme defteri, gürültülü hatta paranın TAM BİR KEZ çekilmesi (idempotency + köprü tekrar önbelleği),
dört göz onayı, kasa kilidi rölesinin yalnızca gerçek onaydan sonra ve tam 5 sn açılması.
g++/make yoksa atlanır.
"""
import json
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from django.test import Client, TestCase, override_settings

from . import ledger
from .hashlog import zinciri_dogrula
from .models import Hesap, HesapHareketi, Islem, LogKaydi, OnayTalebi
from .tests import Yardimci

KOK = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(KOK / "bridge"))
sys.path.insert(0, str(KOK / "arduino" / "test"))

try:
    import cerceve
    import serial_bridge
    import yazilim_terminal as yt
    SIM_HATASI = None if yt.simulatoru_derle() else "g++/make yok"
except Exception as hata:  # pragma: no cover
    SIM_HATASI = f"simulator hazirlanamadi: {hata}"

import unittest

ANAHTAR = "gelistirme-kopru-anahtari"


class DjangoKopru(serial_bridge.Kopru):
    """Gerçek köprü komut eşlemesi; yalnızca HTTP katmanı Django test istemcisine yönlendirilir."""

    def __init__(self, istemci, kuyruk_yolu):
        super().__init__("http://127.0.0.1:1", ANAHTAR, terminal="ATM-01", kuyruk_yolu=kuyruk_yolu)
        self.istemci = istemci

    def _post(self, yol, veri):
        r = self.istemci.post(yol, data=json.dumps(veri), content_type="application/json",
                              headers={"X-Kopru-Anahtari": ANAHTAR, "X-Terminal-Id": "ATM-01"})
        return r.json()


@unittest.skipIf(SIM_HATASI, SIM_HATASI)
@override_settings(MESAI_DISI_KURALI=False)
class GomuluUctanUcaTestleri(Yardimci, TestCase):
    def setUp(self):
        self.hazirla()
        self.gecici = tempfile.TemporaryDirectory()
        self.addCleanup(self.gecici.cleanup)
        self.kopru = DjangoKopru(Client(), Path(self.gecici.name) / "kuyruk.jsonl")

    def terminal(self, **kw):
        oturum = cerceve.Oturum(self.kopru.isle, kati=True)
        self.oturum = oturum
        t = yt.YazilimTerminali(oturum.satir_isle, **kw)
        self.addCleanup(t.kapat)
        t.ilerle(2500)
        return t

    # ------------------------------------------------------------ yardımcılar

    def musteri_girisi(self, t):
        t.kart_okut("a0a0a001")
        self.assertTrue(t.durum_bekle("PIN_AL", 8000), t.lcd)
        t.yaz("1234")
        self.assertTrue(t.durum_bekle("MENU", 20000), t.lcd)

    def cekme_baslat(self, t, tutar):
        t.tikla("B")
        t.yaz(tutar)
        t.tikla("#")

    def yetkili_onayi(self, t, uid, pin, karar="A"):
        t.kart_okut(uid)
        self.assertTrue(t.durum_bekle("KARAR_AL", 12000), t.lcd)
        t.tikla(karar)
        self.assertTrue(t.durum_bekle("PIN_AL", 4000), t.lcd)
        t.yaz(pin)
        self.assertTrue(t.ekran_bekle("Oyunuz alindi", 12000), t.lcd)
        t.ilerle(1300)

    def kasa_acma_baslat(self, t):
        t.kart_okut("b0b0b003")                                   # Cem: müdür yardımcısı
        self.assertTrue(t.durum_bekle("TALEP_YOK", 12000), t.lcd)
        t.tikla("A")
        t.yaz("3333")
        self.assertTrue(t.durum_bekle("ONAY_IZLE", 12000), t.lcd)

    # ------------------------------------------------------------ testler

    def test_para_cekme_defter_ve_zincir(self):
        t = self.terminal()
        self.assertEqual(t.lcd, ["Karti okutun", "Mini ATM / Kasa"])
        self.musteri_girisi(t)
        t.tikla("A")
        self.assertTrue(t.ekran_bekle("Bakiye:", 8000, "1000000.00 TL"), t.lcd)
        self.assertTrue(t.durum_bekle("MENU", 8000))
        self.cekme_baslat(t, "1000")
        self.assertTrue(t.durum_bekle("PARA_BEKLE", 12000), t.lcd)
        self.assertTrue(t.led)
        t.tikla("A")
        self.assertTrue(t.ekran_bekle("Islem tamam", 12000), t.lcd)
        self.assertEqual(t.lcd[1], "Bak: 999000.00")
        self.assertFalse(t.led)
        self.assertEqual(self.yenile(self.ali), Decimal("999000.00"))
        self.assertEqual(HesapHareketi.objects.count(), 2)          # çift taraflı kayıt
        self.assertEqual(Islem.objects.count(), 1)
        self.assertTrue(ledger.mutabakat()["saglam"])
        self.assertTrue(zinciri_dogrula()["saglam"])
        self.assertEqual(t.role_acilislari(), [])
        self.assertEqual(t.istatistik_al()["ihlal"], 0)

    def test_gurultulu_hatta_para_tam_bir_kez_cekilir(self):
        """Her çalıştırmada hat bozulur (≈%10 çerçeve), cevaplar kaybolur; Ali'den TAM 1000 TL çıkmalı."""
        toplam_tekrar = 0
        for k, tohum in enumerate(range(1, 7), start=1):
            with self.subTest(tohum=tohum):
                t = self.terminal(gurultu=(0.001, 0.0005, 0.0005), tohum=tohum)
                self.musteri_girisi(t)
                self.cekme_baslat(t, "1000")
                self.assertTrue(t.durum_bekle("PARA_BEKLE", 60000), t.lcd)
                t.tikla("A")
                self.assertTrue(t.ekran_bekle("Islem tamam", 30000), t.lcd)
                self.assertEqual(self.yenile(self.ali), Decimal("1000000.00") - 1000 * k)
                self.assertEqual(HesapHareketi.objects.count(), 2 * k)
                self.assertEqual(Islem.objects.count(), k)
                self.assertEqual(t.role_acilislari(), [])
                toplam_tekrar += self.oturum.istatistik["tekrar"] + self.oturum.istatistik["bozuk"]
                t.kapat()
        self.assertGreater(toplam_tekrar, 0, "hat hiç bozulmadı: gürültü devreye girmemiş olabilir")
        self.assertTrue(ledger.mutabakat()["saglam"])
        self.assertTrue(zinciri_dogrula()["saglam"])

    def test_dort_goz_onayi_sonra_para_teslimi(self):
        t = self.terminal()
        self.musteri_girisi(t)
        self.cekme_baslat(t, "10000")                             # 5.000-50.000: 1 yetkili onayı
        self.assertTrue(t.durum_bekle("ONAY_IZLE", 12000), t.lcd)
        t.ilerle(3000)
        self.assertTrue(t.lcd[0].startswith("Onay 0/1"), t.lcd)
        self.assertEqual(self.yenile(self.ali), Decimal("1000000.00"))   # onaydan önce para çıkmaz
        self.yetkili_onayi(t, "b0b0b001", "1111")                 # Ayşe
        self.assertTrue(t.durum_bekle("PARA_BEKLE", 12000), t.lcd)
        t.tikla("A")
        self.assertTrue(t.ekran_bekle("Islem onaylandi", 12000), t.lcd)
        self.assertEqual(self.yenile(self.ali), Decimal("990000.00"))
        self.assertEqual(OnayTalebi.objects.get().durum, "UYGULANDI")
        self.assertTrue(t.durum_bekle("MENU", 6000))
        self.assertEqual(t.role_acilislari(), [])

    def test_yetkili_yanlis_pin_onay_sayilmaz(self):
        t = self.terminal()
        self.musteri_girisi(t)
        self.cekme_baslat(t, "10000")
        self.assertTrue(t.durum_bekle("ONAY_IZLE", 12000))
        t.ilerle(2000)
        t.kart_okut("b0b0b001")
        self.assertTrue(t.durum_bekle("KARAR_AL", 12000))
        t.tikla("A")
        t.yaz("9999")
        self.assertTrue(t.durum_bekle("MESAJ", 12000), t.lcd)
        self.assertTrue(t.durum_bekle("ONAY_IZLE", 8000))
        self.assertEqual(OnayTalebi.objects.get().durum, "BEKLIYOR")
        self.assertEqual(self.yenile(self.ali), Decimal("1000000.00"))

    def test_kasa_kilidi_yalniz_dort_yetkili_onayindan_sonra_ve_tam_5sn(self):
        t = self.terminal()
        self.kasa_acma_baslat(t)
        t.ilerle(2500)
        self.assertTrue(t.lcd[0].startswith("Onay 0/3"), t.lcd)
        self.assertEqual(t.role_acilislari(), [])
        for uid, pin in (("b0b0b001", "1111"), ("b0b0b002", "2222"), ("b0b0b004", "4444")):
            self.yetkili_onayi(t, uid, pin)                       # 3 onay ama müdür yok
            self.assertEqual(t.role_acilislari(), [], "müdürsüz 3 onayla kilit açılmamalı")
        t.ilerle(2500)
        self.assertEqual(t.lcd[1], "Ust rol gerekli")
        self.yetkili_onayi(t, "b0b0b005", "5555")                 # Elif: müdür
        self.assertTrue(t.durum_bekle("KILIT", 12000), t.lcd)
        self.assertEqual(t.lcd[0], "Kasa acildi")
        self.assertTrue(t.role)
        self.assertTrue(t.durum_bekle("MESAJ", 8000))
        self.assertFalse(t.role)
        acilis = t.role_acilislari()
        self.assertEqual(len(acilis), 1)
        kapanis = [o for o in t.olaylar if o.tur == "ROLE" and o.veri == "0" and o.t > acilis[0].t][0]
        self.assertTrue(5000 <= kapanis.t - acilis[0].t <= 5020, kapanis.t - acilis[0].t)
        self.assertEqual(OnayTalebi.objects.get().durum, "UYGULANDI")
        self.assertTrue(LogKaydi.objects.filter(olay_turu="kasa_acildi").exists())
        self.assertEqual(HesapHareketi.objects.count(), 0)         # kasa açmak para hareketi değildir
        self.assertTrue(zinciri_dogrula()["saglam"])
        self.assertEqual(t.istatistik_al()["ihlal"], 0)

    def test_yetersiz_onayla_rol_hic_acilmaz(self):
        t = self.terminal(gurultu=(0.0005, 0.0002, 0.0002), tohum=3)
        self.kasa_acma_baslat(t)
        self.yetkili_onayi(t, "b0b0b001", "1111")
        self.yetkili_onayi(t, "b0b0b002", "2222")
        t.kart_okut("a0a0a001")                                   # müşteri kartı yetkili sayılmaz
        t.ilerle(60000)                                           # bir dakika bekle, süre dolmadan
        self.assertEqual(t.role_acilislari(), [])
        self.assertNotEqual(OnayTalebi.objects.get().durum, "UYGULANDI")
        self.assertFalse(LogKaydi.objects.filter(olay_turu="kasa_acildi").exists())
        self.assertEqual(t.istatistik_al()["ihlal"], 0)

    def test_musteri_cikis_yapinca_oturum_jetonu_sunucuda_gecerli_ama_terminalde_yok(self):
        t = self.terminal()
        self.musteri_girisi(t)
        t.tikla("*")
        self.assertTrue(t.durum_bekle("BOSTA", 8000))
        t.ilerle(500)
        self.assertEqual(t.lcd[0], "Karti okutun")
        self.assertEqual(self.oturum.istatistik["bozuk"], 0)
