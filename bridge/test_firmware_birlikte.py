"""GERCEK C++ firmware cekirdegi (arduino/test/sim_calistir) <-> GERCEK Python kopru cerceveleme (cerceve.Oturum).

Iki bagimsiz gerceklemenin (C++ ve Python) ayni hat protokolunu konustugunu dogrular: CRC, sira numarasi, HELLO/PING,
tekrar onbellegi, NAK, gurultu. Sunucu yerine kucuk bir sahte `isle` kullanilir (Django ile uctan uca olani:
backend/banka/test_gomulu_uctan_uca.py). g++/make yoksa atlanir.

    python -m unittest discover -s bridge -p "test_*.py"
"""
import sys
import unittest
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(KOK / "arduino" / "test"))

import cerceve  # noqa: E402

try:
    import yazilim_terminal as yt  # noqa: E402
    SIM_VAR = yt.simulatoru_derle() is not None
except Exception as e:  # pragma: no cover - derleme hatasi testlerde gorunsun
    SIM_VAR = False
    SIM_HATA = str(e)


class SahteSunucu:
    def __init__(self):
        self.cagrilar = []
        self.token = "7:1vXyZa:" + "k" * 43

    def isle(self, yuk):
        self.cagrilar.append(yuk)
        a = yuk.split("|")
        if a[0] == "KART":
            return "TIP|MUSTERI" if a[1] == "a0a0a001" else "TIP|BILINMIYOR"
        if a[0] == "PIN":
            return f"GIRIS|{self.token}|Ali" if a[2] == "1234" else "HATA|PIN yanlis"
        if a[0] == "BAKIYE":
            return "BAKIYE|1500.75"
        return "HATA|Bilinmeyen komut"


@unittest.skipUnless(SIM_VAR, "g++/make yok ya da simulator derlenemedi")
class FirmwareKopruTestleri(unittest.TestCase):
    def kur(self, **kw):
        self.sunucu = SahteSunucu()
        self.oturum = cerceve.Oturum(self.sunucu.isle, kati=kw.pop("kati", True))
        self.term = yt.YazilimTerminali(self.oturum.satir_isle, **kw)
        self.addCleanup(self.term.kapat)
        return self.term

    def test_hello_el_sikisma_ve_bosta_ekrani(self):
        t = self.kur()
        t.ilerle(3500)
        self.assertEqual(t.lcd, ["Karti okutun", "Mini ATM / Kasa"])
        self.assertTrue(t.tx[0].startswith(("0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "A", "B", "C", "D", "E", "F")))
        sira, yuk = cerceve.coz(t.tx[0])          # firmware'in CRC'si Python tarafinca dogrulanir
        self.assertEqual(yuk, "HELLO|2")
        self.assertGreaterEqual(self.oturum.istatistik["cerceve"], 1)
        self.assertEqual(self.oturum.istatistik["bozuk"], 0)
        self.assertEqual(self.sunucu.cagrilar, [])  # HELLO/PING sunucuya gitmez

    def test_musteri_girisi_ve_bakiye_gurultusuz(self):
        t = self.kur()
        t.ilerle(2500)
        t.kart_okut("a0a0a001")
        self.assertTrue(t.durum_bekle("PIN_AL", 5000))
        t.yaz("1234")
        self.assertTrue(t.durum_bekle("MENU", 8000))
        t.tikla("A")
        self.assertTrue(t.ekran_bekle("Bakiye:", 5000, "1500.75 TL"))
        self.assertEqual(self.sunucu.cagrilar[0], "KART|a0a0a001")
        self.assertTrue(self.sunucu.cagrilar[1].startswith("PIN|a0a0a001|1234"))
        self.assertEqual(self.sunucu.cagrilar[2][:7], "BAKIYE|")
        self.assertEqual(len(self.sunucu.cagrilar), 3)
        self.assertEqual(t.role_acilislari(), [])
        self.assertEqual(self.oturum.istatistik["bozuk"], 0)

    def test_gurultulu_hatta_sunucu_her_komutu_en_fazla_bir_kez_gorur(self):
        for tohum in range(1, 9):
            with self.subTest(tohum=tohum):
                t = self.kur(gurultu=(0.001, 0.0005, 0.0005), tohum=tohum, kati=False)
                t.ilerle(2500)
                t.kart_okut("a0a0a001")
                t.durum_bekle("PIN_AL", 20000)
                t.yaz("1234")
                self.assertTrue(t.durum_bekle("MENU", 30000))
                t.tikla("A")
                self.assertTrue(t.ekran_bekle("Bakiye:", 20000, "1500.75 TL"))
                # ayni (komut) sunucuda iki kez calismadi: tekrar eden cerceveler onbellekten cevaplandi
                self.assertEqual(len([c for c in self.sunucu.cagrilar if c.startswith("PIN|")]), 1)
                self.assertEqual(len([c for c in self.sunucu.cagrilar if c.startswith("BAKIYE|")]), 1)
                self.assertEqual(t.role_acilislari(), [])
                self.assertEqual(t.istatistik_al()["ihlal"], 0)

    def test_cevap_kaybolunca_terminal_ayni_cerceveyi_yeniden_yollar(self):
        sunucu = SahteSunucu()
        oturum = cerceve.Oturum(sunucu.isle, kati=True)
        dusen = {"n": 0}

        def kayipli(satir):
            cevap = oturum.satir_isle(satir)
            if cevap and "TIP|" in cevap and dusen["n"] == 0:   # ilk KART cevabi yolda kaybolur
                dusen["n"] = 1
                return None
            return cevap

        t = yt.YazilimTerminali(kayipli)
        self.addCleanup(t.kapat)
        t.ilerle(2500)
        t.kart_okut("a0a0a001")
        self.assertTrue(t.durum_bekle("PIN_AL", 8000))      # 2 sn sonra yeniden denedi, onbellekten cevap geldi
        self.assertEqual(sunucu.cagrilar.count("KART|a0a0a001"), 1)
        self.assertEqual(oturum.istatistik["tekrar"], 1)
        kart_cerceveleri = [x for x in t.tx if "KART|a0a0a001" in x]
        self.assertEqual(len(kart_cerceveleri), 2)
        self.assertEqual(kart_cerceveleri[0], kart_cerceveleri[1])   # birebir ayni cerceve (ayni sira no)

    def test_kopru_cevapsizsa_firmware_sunucu_yanitsiz_der_ve_toparlanir(self):
        t = yt.YazilimTerminali(lambda satir: None)          # kablo cikik
        self.addCleanup(t.kapat)
        t.ilerle(2500)
        t.kart_okut("a0a0a001")
        self.assertTrue(t.ekran_bekle("Sunucu yanitsiz", 9000))
        self.assertTrue(t.durum_bekle("BOSTA", 6000))
        self.assertEqual(t.istatistik_al()["zaman_asimi"], 1)
        self.assertEqual(t.role_acilislari(), [])

    def test_eski_firmware_cerceve_gormeden_once_calisir_kati_modda_degil(self):
        sunucu = SahteSunucu()
        self.assertEqual(cerceve.Oturum(sunucu.isle).satir_isle("KART|a0a0a001"), "TIP|MUSTERI")
        self.assertIsNone(cerceve.Oturum(sunucu.isle, kati=True).satir_isle("KART|a0a0a001"))


if __name__ == "__main__":
    unittest.main()
