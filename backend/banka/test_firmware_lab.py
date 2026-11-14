"""Firmware Laboratuvarı sunucu tarafı: erişim kuralları ve CRC'li hat (gerçek köprü Oturumu + gerçek servisler)."""
import json
import sys
import uuid
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from .models import HesapHareketi
from .tests import Yardimci

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bridge"))
import cerceve  # noqa: E402

HAT = "/firmware-lab/hat/"


@override_settings(MESAI_DISI_KURALI=False)
class FirmwareLabTestleri(Yardimci, TestCase):
    def setUp(self):
        self.hazirla()
        self.U = get_user_model()
        self.root = self.U.objects.create_superuser("root", "root@banka.example", "x")
        self.client.force_login(self.root)
        self.sira = 0x40
        self.lab = uuid.uuid4().hex[:20]      # oturum deposu süreç genelindedir: her test kendi sekmesini kullanır

    def etiket(self, ad):
        return f"{self.lab}-{ad}"

    def hat(self, satir, lab=None, client=None):
        lab = self.lab if lab is None else self.etiket(lab)
        return (client or self.client).post(HAT, data=json.dumps({"cerceve": satir, "lab_id": lab, "terminal": "ATM-01"}),
                                            content_type="application/json")

    def konus(self, yuk, sira=None, lab=None):
        """Yükü çerçeveleyip hatta yollar; cevabın (sıra, yük) çiftini döndürür."""
        if sira is None:
            self.sira = (self.sira + 1) & 0xFF
            sira = self.sira
        r = self.hat(cerceve.kodla(sira, yuk), lab)
        self.assertEqual(r.status_code, 200)
        v = r.json()
        return v, (cerceve.coz(v["cevap"]) if v["cevap"] else None)

    # ------------------------------------------------------------ erişim

    def test_sayfa_ve_uc_erisim_kurallari(self):
        self.assertEqual(Client().get("/firmware-lab/").status_code, 302)               # giriş yapmamış
        self.assertEqual(Client().post(HAT, data="{}", content_type="application/json").status_code, 302)
        self.client.force_login(self.U.objects.get(username="denetci"))                  # staff ama superuser değil
        self.assertEqual(self.client.get("/firmware-lab/").status_code, 403)
        self.assertEqual(self.hat(cerceve.kodla(1, "PING")).status_code, 403)
        self.client.force_login(self.root)
        r = self.client.get("/firmware-lab/")
        self.assertEqual(r.status_code, 200)
        govde = r.content.decode()
        self.assertIn("firmware-lab/vx_firmware.js", govde)
        self.assertIn('id="fl-veri"', govde)

    def test_kapaliysa_hicbir_uc_yoktur(self):
        with override_settings(SIMULATOR_AKTIF=False):
            self.assertEqual(self.client.get("/firmware-lab/").status_code, 404)
            self.assertEqual(self.hat(cerceve.kodla(1, "PING")).status_code, 404)

    def test_gecersiz_istekler_400(self):
        for govde in ("{}", "yok", json.dumps({"cerceve": "x" * 300, "lab_id": "a"}), json.dumps({"cerceve": "x", "lab_id": ""}),
                      json.dumps({"cerceve": 5, "lab_id": "a"})):
            r = self.client.post(HAT, data=govde, content_type="application/json")
            self.assertEqual(r.status_code, 400, govde)

    def test_statik_dosyalar_var(self):
        kok = Path(__file__).resolve().parent / "static" / "banka" / "firmware-lab"
        for ad in ("vx_firmware.js", "firmware-lab.js", "firmware-lab.css"):
            self.assertTrue((kok / ad).is_file(), ad)

    # ------------------------------------------------------------ hat

    def test_hello_ping_ve_kart(self):
        v, c = self.konus("HELLO|2")
        self.assertEqual(c[1], "HELLO|OK|2")
        v, c = self.konus("PING")
        self.assertEqual(c[1], "PONG")
        v, c = self.konus("KART|a0a0a001")
        self.assertEqual(c[1], "TIP|MUSTERI")
        self.assertEqual(v["istatistik"]["cerceve"], 3)

    def test_bozuk_crc_nak_doner_ve_komut_islenmez(self):
        bozuk = cerceve.kodla(0x50, "KART|a0a0a001")[:-1] + "0"
        if bozuk == cerceve.kodla(0x50, "KART|a0a0a001"):
            bozuk = bozuk[:-1] + "1"
        v = self.hat(bozuk).json()
        sira, yuk = cerceve.coz(v["cevap"])
        self.assertEqual((sira, yuk), (cerceve.NAK_SIRA, "NAK|CRC"))
        self.assertEqual(v["istatistik"]["bozuk"], 1)
        self.assertEqual(v["istatistik"]["cerceve"], 0)

    def test_ayni_istek_iki_kez_para_tek_kez_ciker_ikincisi_onbellekten(self):
        self.konus("HELLO|2")
        _, c = self.konus("KART|a0a0a001")
        _, c = self.konus("PIN|a0a0a001|1234")
        token = c[1].split("|")[1]
        ayni = cerceve.kodla(0x77, f"ISLEM|{token}|C|100|-|anahtar-lab-0001")
        ilk = self.hat(ayni).json()
        self.assertFalse(ilk["onbellekten"])
        self.assertTrue(cerceve.coz(ilk["cevap"])[1].startswith("TAMAM|"))
        yinelenen = self.hat(ayni).json()                      # cevap kaybolup terminal aynı çerçeveyi yeniden yollarsa
        self.assertTrue(yinelenen["onbellekten"])
        self.assertEqual(yinelenen["cevap"], ilk["cevap"])
        self.assertEqual(self.yenile(self.ali), Decimal("999900.00"))
        self.assertEqual(HesapHareketi.objects.filter(hesap=self.ali).count(), 1)

    def test_oturumlar_sekmeye_ozel_onbellek_sizmaz(self):
        self.konus("HELLO|2", lab="a")
        self.konus("HELLO|2", lab="b")
        ayni = cerceve.kodla(0x61, "KART|a0a0a001")
        self.assertFalse(self.hat(ayni, lab="a").json()["onbellekten"])
        self.assertTrue(self.hat(ayni, lab="a").json()["onbellekten"])
        self.assertFalse(self.hat(ayni, lab="b").json()["onbellekten"])

    # ------------------------------------------------------------ onay panosu (kasa açma)

    def talepler(self, client=None):
        r = (client or self.client).get("/firmware-lab/talepler/")
        self.assertEqual(r.status_code, 200)
        return r.json()["talepler"]

    def test_talepler_erisim_kurallari(self):
        self.assertEqual(Client().get("/firmware-lab/talepler/").status_code, 302)
        self.assertEqual(self.client.post("/firmware-lab/talepler/").status_code, 405)
        with override_settings(SIMULATOR_AKTIF=False):
            self.assertEqual(self.client.get("/firmware-lab/talepler/").status_code, 404)
        self.client.force_login(self.U.objects.get(username="denetci"))
        self.assertEqual(self.client.get("/firmware-lab/talepler/").status_code, 403)

    def test_talepler_bos_liste(self):
        self.assertEqual(self.talepler(), [])

    def test_kasa_acma_oylari_hat_uzerinden_panoda_gorunur(self):
        self.konus("HELLO|2")
        _, c = self.konus("KASAAC|b0b0b003|3333")                     # Cem (müdür yrd.) talebi başlatır
        talep_id = c[1].split("|")[1]
        self.assertEqual(c[1], f"BEKLE|{talep_id}|3")
        t = self.talepler()[0]
        self.assertEqual((t["tur"], t["durum"], t["verilen"], t["gereken"]), ("KASA_ACMA", "BEKLIYOR", 0, 3))
        self.assertEqual(t["en_az_rol"], "Müdür")
        for uid, pin in (("b0b0b001", "1111"), ("b0b0b002", "2222"), ("b0b0b004", "4444")):
            _, c = self.konus(f"ONAY|{talep_id}|{uid}|{pin}|E")
        self.assertEqual(c[1], "ONAY|3|3|BEKLIYOR")                    # 3 oy tamam ama müdür yok
        t = self.talepler()[0]
        self.assertTrue(t["ust_yetkili_gerekli"])
        self.assertEqual([(o["ad"], o["rol"], o["karar"]) for o in t["oylar"]],
                         [("Ayşe (görevli)", "Görevli", "ONAY"), ("Burak (görevli)", "Görevli", "ONAY"),
                          ("Deniz (müdür yrd.)", "Müdür yardımcısı", "ONAY")])
        _, c = self.konus(f"DURUM|{talep_id}")
        self.assertEqual(c[1].split("|")[5], "1")                      # terminal de aynı bilgiyi alır: üst yetkili gerekli
        _, c = self.konus(f"ONAY|{talep_id}|b0b0b005|5555|E")          # Elif (müdür)
        self.assertEqual(c[1], "ONAY|4|3|UYGULANDI")
        t = self.talepler()[0]
        self.assertEqual((t["durum"], t["verilen"], t["ust_yetkili_gerekli"]), ("UYGULANDI", 4, False))
        self.assertEqual(t["oylar"][-1]["rol"], "Müdür")

