"""Hat cercevelemesi testleri (donanim ve ag gerekmez):  python -m unittest discover -s bridge -p "test_*.py"."""
import random
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cerceve  # noqa: E402
from cerceve import CerceveHatasi, Oturum, coz, crc16, kodla  # noqa: E402

KOK = Path(__file__).resolve().parents[1]
VEKTOR = KOK / "arduino" / "test" / "vektorler.txt"


class CrcVeCerceveTestleri(unittest.TestCase):
    def test_standart_kontrol_degeri(self):
        self.assertEqual(crc16(b"123456789"), 0x29B1)       # CRC-16/CCITT-FALSE
        self.assertEqual(crc16(b""), 0xFFFF)

    def test_gidis_donus(self):
        for sira in (0, 1, 10, 255):
            for yuk in ("PING", "KART|a0a0a001", "A*B|*", "", "x" * cerceve.YUK_EN_COK):
                self.assertEqual(coz(kodla(sira, yuk)), (sira, yuk))

    def test_cok_uzun_ve_yazdirilamaz_yuk_reddedilir(self):
        with self.assertRaises(CerceveHatasi):
            kodla(1, "x" * (cerceve.YUK_EN_COK + 1))
        with self.assertRaises(CerceveHatasi):
            kodla(1, "a\nb")
        with self.assertRaises(CerceveHatasi):
            kodla(1, "café")

    def test_bozuklar_reddedilir(self):
        iyi = kodla(10, "KART|a0a0a001")
        for kotu in (iyi[:-1], iyi.replace("KART", "KARD"), "KART|a0a0a001", "", "0A:x*ZZZZ", "0A:" + "KART*0000"):
            with self.assertRaises(CerceveHatasi, msg=kotu):
                coz(kotu)

    def test_en_fazla_uc_bit_hatasi_her_zaman_yakalanir(self):
        """CRC-16/CCITT bu uzunlukta 1-3 bit hatasini kesin yakalar (Hamming mesafesi 4)."""
        rng = random.Random(1)
        for _ in range(3000):
            sira = rng.randrange(256)
            yuk = "".join(rng.choice("ABCabc012|*. ") for _ in range(rng.randrange(0, 60)))
            orijinal = kodla(sira, yuk)
            f = bytearray(orijinal.encode())
            for _ in range(rng.randrange(1, 4)):
                f[rng.randrange(len(f))] ^= 1 << rng.randrange(8)
            if bytes(f) == orijinal.encode():
                continue
            try:
                sonuc = coz(f.decode("latin-1"))
            except CerceveHatasi:
                continue
            # CRC'deki 'A'->'a' gibi anlami degismeyen bicim farki zararsiz; YANLIS icerik kabul edilmemeli
            self.assertEqual(sonuc, (sira, yuk), f"bozuk cerceve yanlis icerikle kabul edildi: {f!r}")

    def test_ortak_vektorler_guncel_ve_dogru(self):
        r = subprocess.run([sys.executable, str(KOK / "arduino" / "test" / "vektor_uret.py"), "--kontrol"])
        self.assertEqual(r.returncode, 0, "arduino/test/vektorler.txt eski: python arduino/test/vektor_uret.py")
        for satir in VEKTOR.read_text(encoding="utf-8").splitlines():
            if satir.startswith("#") or not satir:
                continue
            alan = satir.split("\t")
            if alan[0] == "CRC":
                self.assertEqual(crc16(bytes.fromhex(alan[1])), int(alan[2], 16), satir)
            elif alan[0] == "OK":
                self.assertEqual(kodla(int(alan[1], 16), alan[2]), alan[3])
                self.assertEqual(coz(alan[3]), (int(alan[1], 16), alan[2]))
            elif alan[0] == "BAD":
                with self.assertRaises(CerceveHatasi, msg=satir):
                    coz(alan[1] if len(alan) > 1 else "")


class OturumTestleri(unittest.TestCase):
    def setUp(self):
        self.cagrilar = []
        self.oturum = Oturum(self._isle)

    def _isle(self, yuk):
        self.cagrilar.append(yuk)
        return "TIP|MUSTERI" if yuk.startswith("KART") else f"HATA|{yuk}"

    def test_cerceveli_istek_cerceveli_cevap(self):
        cevap = self.oturum.satir_isle(kodla(5, "KART|a0a0a001"))
        self.assertEqual(coz(cevap), (5, "TIP|MUSTERI"))
        self.assertEqual(self.cagrilar, ["KART|a0a0a001"])

    def test_tekrar_gelen_istek_yeniden_islenmez(self):
        satir = kodla(5, "KART|a0a0a001")
        ilk = self.oturum.satir_isle(satir)
        for _ in range(3):
            self.assertEqual(self.oturum.satir_isle(satir), ilk)
        self.assertEqual(len(self.cagrilar), 1)
        self.assertEqual(self.oturum.istatistik["tekrar"], 3)

    def test_ayni_sira_farkli_yuk_yeni_istektir(self):
        self.oturum.satir_isle(kodla(5, "KART|a"))
        self.oturum.satir_isle(kodla(5, "KART|b"))
        self.assertEqual(self.cagrilar, ["KART|a", "KART|b"])

    def test_bozuk_cerceve_islenmez_nak_doner(self):
        bozuk = kodla(5, "KART|a0a0a001").replace("a0a0", "b0b0")
        cevap = self.oturum.satir_isle(bozuk)
        self.assertEqual(coz(cevap), ("--", "NAK|CRC"))
        self.assertEqual(self.cagrilar, [])
        self.assertEqual(self.oturum.istatistik["bozuk"], 1)

    def test_hello_onbellegi_temizler_surumu_dogrular(self):
        satir = kodla(1, "KART|a")
        self.oturum.satir_isle(satir)
        self.assertEqual(coz(self.oturum.satir_isle(kodla(0, "HELLO|2"))), (0, "HELLO|OK|2"))
        self.oturum.satir_isle(satir)                 # terminal resetlendi, sira yeniden 1: yeni istek sayilir
        self.assertEqual(len(self.cagrilar), 2)
        self.assertEqual(coz(self.oturum.satir_isle(kodla(0, "HELLO|9")))[1], "HELLO|UYUMSUZ|2")

    def test_ping_sunucuya_gitmez(self):
        self.assertEqual(coz(self.oturum.satir_isle(kodla(3, "PING"))), (3, "PONG"))
        self.assertEqual(self.cagrilar, [])

    def test_eski_satirlar_cerceve_gorulene_kadar_kabul(self):
        self.assertEqual(self.oturum.satir_isle("KART|a0a0a001"), "TIP|MUSTERI")
        self.oturum.satir_isle(kodla(1, "PING"))
        self.assertIsNone(self.oturum.satir_isle("KART|a0a0a001"))       # artik reddedilir
        self.assertEqual(self.oturum.istatistik["reddedilen"], 1)
        self.assertEqual(len(self.cagrilar), 1)

    def test_kati_modda_eski_satir_hic_kabul_edilmez(self):
        o = Oturum(self._isle, kati=True)
        self.assertIsNone(o.satir_isle("KART|a0a0a001"))
        self.assertEqual(self.cagrilar, [])

    def test_cerceveye_sigmayan_cevap_kopru_cokertmez(self):
        o = Oturum(lambda yuk: "HATA|" + "x" * 200)
        self.assertEqual(coz(o.satir_isle(kodla(1, "KART|a"))), (1, "HATA|Yanit gecersiz"))
        o2 = Oturum(lambda yuk: "HATA|\u015fifre")
        self.assertEqual(coz(o2.satir_isle(kodla(2, "KART|a")))[1], "HATA|Yanit gecersiz")
        self.assertEqual(o2.istatistik["yanit_hatasi"], 1)

    def test_bozulmus_cerceve_eski_komut_sayilmaz(self):
        """Ilk gecerli cerceveden ONCE de: '04:BAKIYE|x*65zD' komut degildir, NAK doner."""
        cevap = self.oturum.satir_isle("04:BAKIYE|tok4*65zD")
        self.assertEqual(coz(cevap), ("--", "NAK|CRC"))
        self.assertIsNone(self.oturum.satir_isle("debug: sensor ok"))
        self.assertEqual(self.cagrilar, [])

    def test_onbellek_sinirli(self):
        for i in range(cerceve.ONBELLEK_BOYUTU + 5):
            self.oturum.satir_isle(kodla(i, f"KART|{i}"))
        self.assertEqual(len(self.oturum._onbellek), cerceve.ONBELLEK_BOYUTU)

    def test_gurultulu_hatta_komut_ya_dogru_islenir_ya_hic(self):
        """Rastgele bozulmus cerceveler: islenen her komut orijinaliyle AYNI olmali (yanlis komut asla calismaz)."""
        rng = random.Random(3)
        izinli = set()
        for i in range(2000):
            yuk = f"BAKIYE|tok{i}"
            izinli.add(yuk)
            f = bytearray(kodla(i % 256, yuk).encode())
            for _ in range(rng.randrange(0, 4)):
                f[rng.randrange(len(f))] ^= 1 << rng.randrange(7)
            self.oturum.satir_isle(f.decode("ascii", "ignore"))
        self.assertTrue(set(self.cagrilar) <= izinli)


if __name__ == "__main__":
    unittest.main()
