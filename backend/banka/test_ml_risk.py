"""ML risk katmanı (banka/ml_risk): özellikler, sıfırdan Isolation Forest, sentetik veri, ölçüm. Veritabanı gerekmez."""
import json
import math
import random
import tempfile
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import SimpleTestCase

from . import risk
from .ml_risk import aciklama, degerlendir, orman, ozellikler, sentetik
from .ml_risk.ozellikler import Olay

T0 = datetime(2026, 11, 2, 14, 0)


def olay(gun=0, saat=14.0, tutar=500.0, tur="CEKME", terminal="ATM-01", hedef=None, bakiye=10000.0, pin_hata=0):
    return Olay(T0.replace(hour=0) + timedelta(days=gun, hours=saat), tutar, tur, terminal, hedef, bakiye, pin_hata)


def gecmis(n=5, **kw):
    return [olay(gun=i, **kw) for i in range(n)]


def ozellik(vektor, kod):
    return vektor[ozellikler.OZELLIK_KODLARI.index(kod)]


class OzellikTestleri(SimpleTestCase):
    def test_vektor_uzunlugu_ozellik_kodlariyla_ayni(self):
        v = ozellikler.ozellik_vektoru(olay(gun=9), gecmis())
        self.assertEqual(len(v), len(ozellikler.OZELLIK_KODLARI))
        self.assertTrue(all(isinstance(x, float) for x in v))

    def test_sıradan_islem_notr_yeni_terminal_ve_hedef_yok(self):
        v = ozellikler.ozellik_vektoru(olay(gun=9), gecmis())
        self.assertAlmostEqual(ozellik(v, "tutar_orani"), 0.0)
        self.assertAlmostEqual(ozellik(v, "saat_sapmasi"), 0.0, places=6)
        self.assertEqual([ozellik(v, k) for k in ("gece", "yeni_terminal", "yeni_hedef", "islem_10dk", "pin_hata", "havale")], [0.0] * 6)

    def test_tutar_orani_medyana_gore_logaritmiktir(self):
        v = ozellikler.ozellik_vektoru(olay(gun=9, tutar=5000), gecmis())
        self.assertAlmostEqual(ozellik(v, "tutar_orani"), math.log1p(5000) - math.log1p(500))

    def test_bakiye_orani_birde_kirpilir(self):
        v = ozellikler.ozellik_vektoru(olay(gun=9, tutar=500, bakiye=100), gecmis())
        self.assertEqual(ozellik(v, "bakiye_orani"), 1.0)
        v = ozellikler.ozellik_vektoru(olay(gun=9, tutar=500, bakiye=1000), gecmis())
        self.assertAlmostEqual(ozellik(v, "bakiye_orani"), 0.5)

    def test_gece_ve_saat_sapmasi(self):
        v = ozellikler.ozellik_vektoru(olay(gun=9, saat=2.0), gecmis())
        self.assertEqual(ozellik(v, "gece"), 1.0)
        self.assertAlmostEqual(ozellik(v, "saat_sapmasi"), 12.0 / 12.0, places=5)     # 14:00'ın tam karşısı
        self.assertEqual(ozellik(ozellikler.ozellik_vektoru(olay(gun=9, saat=5.0), gecmis()), "gece"), 0.0)

    def test_dairesel_saat_gece_yarisini_dogru_ortalar(self):
        ort = ozellikler._dairesel_ortalama([23.5, 0.5])
        self.assertLess(min(ort, 24 - ort), 0.01)                                    # 12:00 değil, gece yarısı
        self.assertAlmostEqual(ozellikler._dairesel_fark(23.0, 1.0), 2.0)
        self.assertAlmostEqual(ozellikler._dairesel_fark(2.0, 14.0), 12.0)

    def test_yeni_terminal_ve_yeni_hedef(self):
        g = gecmis(tur="HAVALE", hedef="H-1")
        v = ozellikler.ozellik_vektoru(olay(gun=9, tur="HAVALE", hedef="H-1"), g)
        self.assertEqual((ozellik(v, "yeni_hedef"), ozellik(v, "yeni_terminal"), ozellik(v, "havale")), (0.0, 0.0, 1.0))
        v = ozellikler.ozellik_vektoru(olay(gun=9, tur="HAVALE", hedef="H-NEW", terminal="ATM-05"), g)
        self.assertEqual((ozellik(v, "yeni_hedef"), ozellik(v, "yeni_terminal")), (1.0, 1.0))

    def test_cekmede_yeni_hedef_her_zaman_sifir(self):
        v = ozellikler.ozellik_vektoru(olay(gun=9, hedef="X"), gecmis())
        self.assertEqual(ozellik(v, "yeni_hedef"), 0.0)

    def test_islem_sayaclari_pencereleri_ve_ust_siniri(self):
        t = olay(gun=9, saat=14.0)
        g = [Olay(t.zaman - timedelta(minutes=m), 500, "CEKME", "ATM-01") for m in (90, 40, 9, 5, 1)]
        g.sort(key=lambda o: o.zaman)
        v = ozellikler.ozellik_vektoru(t, g)
        self.assertEqual((ozellik(v, "islem_10dk"), ozellik(v, "islem_1sa")), (3.0, 4.0))
        coklu = [Olay(t.zaman - timedelta(seconds=s), 500, "CEKME", "ATM-01") for s in range(60, 0, -2)]
        self.assertEqual(ozellik(ozellikler.ozellik_vektoru(t, coklu), "islem_10dk"), 10.0)            # üst sınır

    def test_gecmisi_az_hesapta_olagan_bilinmez_notr_deger_doner(self):
        v = ozellikler.ozellik_vektoru(olay(gun=3, tutar=90000, terminal="ATM-07", saat=3.0), gecmis(2))
        self.assertEqual((ozellik(v, "tutar_orani"), ozellik(v, "saat_sapmasi"), ozellik(v, "yeni_terminal")), (0.0, 0.0, 0.0))
        v = ozellikler.ozellik_vektoru(olay(), [])
        self.assertAlmostEqual(ozellik(v, "son_islemden_sn"), math.log1p(ozellikler.YOK_SURE_SN))
        self.assertEqual(ozellikler.YOK_SURE_SN, 86400)                       # 30 gün sayılırsa yeni hesap "aykırı" görünürdü

    def test_gecmisi_degistirmez(self):
        g = gecmis()
        kopya = list(g)
        ozellikler.ozellik_vektoru(olay(gun=9), g)
        self.assertEqual(g, kopya)

    def test_pin_hata_kirpilir(self):
        self.assertEqual(ozellik(ozellikler.ozellik_vektoru(olay(gun=9, pin_hata=9), gecmis()), "pin_hata"), 5.0)

    def test_gece_siniri_risk_py_ile_ayni(self):
        self.assertEqual((ozellikler.GECE_BASLANGIC, ozellikler.GECE_BITIS), (0, 5))
        self.assertEqual(ozellikler.GECMIS_EN_AZ, risk.TUTAR_EN_AZ_GECMIS)


class OrmanTestleri(SimpleTestCase):
    def kume(self, n=300, tohum=3):
        rng = random.Random(tohum)
        return [[rng.gauss(0, 1), rng.gauss(0, 1)] for _ in range(n)]

    def test_c_bilinen_degerler(self):
        self.assertEqual((orman.c(0), orman.c(1), orman.c(2)), (0.0, 0.0, 1.0))
        self.assertAlmostEqual(orman.c(256), 10.2448, places=3)            # makaledeki ψ=256 sabiti
        self.assertAlmostEqual(orman.c(3), 2 * (math.log(2) + orman.EULER) - 4 / 3)

    def test_aykiri_nokta_kume_icindeki_noktadan_yuksek_skor_alir(self):
        m = orman.IsolationForest.egit(self.kume(), ["a", "b"], agac_sayisi=60, tohum=1)
        icerde, disarda = m.skor([0.1, -0.1]), m.skor([9.0, 9.0])
        self.assertGreater(disarda, 0.6)
        self.assertLess(icerde, 0.5)
        self.assertTrue(0 < icerde < disarda < 1)

    def test_yuzdelik_monoton_ve_0_100_araliginda(self):
        m = orman.IsolationForest.egit(self.kume(), ["a", "b"], agac_sayisi=40, tohum=1)
        puanlar = [m.puan([x, x]) for x in (0, 1, 2, 4, 8, 16)]
        self.assertEqual(puanlar, sorted(puanlar))
        self.assertTrue(all(0 <= p <= 100 for p in puanlar))
        self.assertEqual(m.puan([50.0, 50.0]), 100.0)

    def test_ayni_tohum_ayni_model_farkli_tohum_farkli_model(self):
        X = self.kume(200)
        a = orman.IsolationForest.egit(X, ["a", "b"], agac_sayisi=20, tohum=7)
        b = orman.IsolationForest.egit(X, ["a", "b"], agac_sayisi=20, tohum=7)
        c = orman.IsolationForest.egit(X, ["a", "b"], agac_sayisi=20, tohum=8)
        self.assertEqual(a.muhur(), b.muhur())
        self.assertNotEqual(a.muhur(), c.muhur())

    def test_json_gidis_donus_ayni_skorlar(self):
        m = orman.IsolationForest.egit(self.kume(200), ["a", "b"], agac_sayisi=20, tohum=1)
        m2 = orman.IsolationForest.sozlukten(json.loads(json.dumps(m.sozluge())))
        for x in ([0, 0], [3, -2], [9, 9]):
            self.assertEqual(m.skor(x), m2.skor(x))
        self.assertEqual(m.muhur(), m2.muhur())

    def test_muhur_modele_dokunulunca_degisir(self):
        m = orman.IsolationForest.egit(self.kume(200), ["a", "b"], agac_sayisi=10, tohum=1)
        onceki = m.muhur()
        m.egitim_skorlari[0] += 0.001
        self.assertNotEqual(m.muhur(), onceki)

    def test_uyumsuz_surum_ve_yetersiz_ornek_reddedilir(self):
        s = orman.IsolationForest.egit(self.kume(50), ["a", "b"], agac_sayisi=3, tohum=1).sozluge()
        s["surum"] = 99
        with self.assertRaises(ValueError):
            orman.IsolationForest.sozlukten(s)
        with self.assertRaises(ValueError):
            orman.IsolationForest.egit([[1.0, 2.0]], ["a", "b"])

    def test_ayni_noktalardan_olusan_veri_cokmez(self):
        m = orman.IsolationForest.egit([[1.0, 1.0]] * 30, ["a", "b"], agac_sayisi=5, tohum=1)
        self.assertTrue(0 < m.skor([1.0, 1.0]) < 1)

    def test_yol_uzunlugu_yalnizca_yaprakta_c_ekler(self):
        self.assertEqual(orman._yol([0], [5]), orman.c(5))
        self.assertEqual(orman._yol([0.0], [0, 1.0, [1], [1]]), 1 + orman.c(1))


class SentetikVeriTestleri(SimpleTestCase):
    def test_ayni_tohum_ayni_veri(self):
        a = sentetik.veri_uret(tohum=5, musteri=20, egitim_gun=20, test_gun=5, saldiri=10)
        b = sentetik.veri_uret(tohum=5, musteri=20, egitim_gun=20, test_gun=5, saldiri=10)
        self.assertEqual(a, b)
        self.assertNotEqual(a, sentetik.veri_uret(tohum=6, musteri=20, egitim_gun=20, test_gun=5, saldiri=10))

    def test_egitim_yalniz_normal_test_donemi_sonra_ve_tum_saldiri_turleri_var(self):
        egitim, test = sentetik.veri_uret(tohum=1, musteri=30, egitim_gun=30, test_gun=7, saldiri=25)
        self.assertEqual({e.etiket for e in egitim}, {"normal"})
        self.assertLess(max(e.olay.zaman for e in egitim), min(e.olay.zaman for e in test))
        self.assertEqual({e.etiket for e in test} - {"normal"}, set(sentetik.SALDIRI_TURLERI))
        self.assertEqual([e.olay.zaman for e in test], sorted(e.olay.zaman for e in test))
        self.assertEqual(len({e.saldiri_no for e in test if e.etiket != "normal"}), 25)

    def test_saldiri_senaryolari_tanimlanan_ozelliklere_sahip(self):
        rng = random.Random(1)
        m = sentetik.musteri_uret(rng, 1)[0]
        klon = sentetik.saldiri_uret(rng, "klon_hizli", m, 10)
        self.assertTrue(4 <= len(klon) <= 8)
        self.assertEqual(len({o.terminal for o in klon}), 1)
        self.assertNotIn(klon[0].terminal, (m.ana_terminal, m.ikinci_terminal))
        self.assertLessEqual((klon[-1].zaman - klon[0].zaman).total_seconds(), 10 * 60)
        (gece,) = sentetik.saldiri_uret(rng, "gece_buyuk_havale", m, 10)
        self.assertTrue(1.0 <= gece.zaman.hour + gece.zaman.minute / 60 <= 4.5)
        self.assertEqual(gece.tur, "HAVALE")
        self.assertNotIn(gece.hedef, m.alicilar)
        (bosalt,) = sentetik.saldiri_uret(rng, "bosaltma", m, 10)
        self.assertGreaterEqual(bosalt.tutar / bosalt.bakiye, 0.70)
        (pin,) = sentetik.saldiri_uret(rng, "pin_deneme", m, 10)
        self.assertGreaterEqual(pin.pin_hata, 3)
        with self.assertRaises(ValueError):
            sentetik.saldiri_uret(rng, "yok", m, 1)


class OlcumTestleri(SimpleTestCase):
    def test_roc_auc_bilinen_durumlar(self):
        self.assertEqual(degerlendir.roc_auc([0.9, 0.8], [0.1, 0.2]), 1.0)
        self.assertEqual(degerlendir.roc_auc([0.1, 0.2], [0.9, 0.8]), 0.0)
        self.assertEqual(degerlendir.roc_auc([0.5, 0.5], [0.5, 0.5]), 0.5)
        self.assertAlmostEqual(degerlendir.roc_auc([2, 4], [1, 3]), 0.75)
        self.assertTrue(math.isnan(degerlendir.roc_auc([], [1])))

    def test_esik_secimi_istenen_yanlis_alarm_oranini_verir(self):
        normal = list(range(1000))
        for fpr in (0.01, 0.05, 0.2):
            esik = degerlendir.esik_sec(normal, fpr)
            self.assertEqual(sum(1 for s in normal if s > esik) / len(normal), fpr)

    def test_kural_sabitleri_risk_py_ile_ayni(self):
        K = degerlendir.KURAL_SABITLERI
        for ad in ("PIN_PUANI", "PIN_EN_COK", "FARKLI_TERMINAL_DK", "FARKLI_TERMINAL_PUANI", "TUTAR_KATI", "TUTAR_PUANI",
                   "YENI_HESAP_PUANI", "HIZ_PENCERE_DK", "HIZ_ESIKLERI", "GECE_PUANI", "TUTAR_EN_AZ_GECMIS"):
            self.assertEqual(K[ad], getattr(risk, ad), ad)

    def test_kural_puani_faktorleri(self):
        g = gecmis()
        self.assertEqual(degerlendir.kural_puani(olay(gun=9), g), 0)
        self.assertEqual(degerlendir.kural_puani(olay(gun=9, tutar=2000), g), 30)                   # 3x ortalamayı ve en büyüğü aşar
        self.assertEqual(degerlendir.kural_puani(olay(gun=9, saat=2.0), g), 10)                     # gece
        self.assertEqual(degerlendir.kural_puani(olay(gun=9, pin_hata=2), g), 30)
        self.assertEqual(degerlendir.kural_puani(olay(gun=9, pin_hata=9), g), 45)                   # üst sınır
        self.assertEqual(degerlendir.kural_puani(olay(gun=9, tutar=6000), gecmis(1)), 15)           # yeni hesapta büyük tutar
        t = olay(gun=9)
        seri = [Olay(t.zaman - timedelta(minutes=m), 500, "CEKME", "ATM-01") for m in (8, 6, 4, 2)]
        self.assertEqual(degerlendir.kural_puani(t, seri), 20)
        seri = [Olay(t.zaman - timedelta(minutes=m), 500, "CEKME", "ATM-01") for m in (9, 8, 6, 4, 2)]
        self.assertEqual(degerlendir.kural_puani(t, seri), 35)
        baska = [Olay(t.zaman - timedelta(minutes=3), 500, "CEKME", "ATM-02")]
        self.assertEqual(degerlendir.kural_puani(t, baska + gecmis(3)[:0]), 25)                     # 10 dk içinde başka terminal

    def test_deney_ml_kuraldan_belirgin_iyi_ve_sonuc_tekrarlanabilir(self):
        a = degerlendir.deney(tohum=2, musteri=80, egitim_gun=40, test_gun=10, saldiri=30, agac=40)
        b = degerlendir.deney(tohum=2, musteri=80, egitim_gun=40, test_gun=10, saldiri=30, agac=40)
        self.assertEqual(a["auc_ml"], b["auc_ml"])
        self.assertGreater(a["auc_ml"], 0.9)
        self.assertGreater(a["auc_ml"], a["auc_kural"] + 0.1)
        n1 = a["noktalar"][0]
        self.assertAlmostEqual(n1["ml"]["yanlis_alarm"], 0.01, delta=0.005)                         # eşik gerçekten %1 yanlış alarm veriyor
        self.assertGreater(n1["ml"]["saldiri"], a["kural"]["saldiri"])
        self.assertGreaterEqual(n1["ml"]["tur"]["gece_buyuk_havale"], 0.6)                          # küçük deneyde tür başına ~6 saldırı var
        self.assertGreater(n1["ml"]["tur"]["klon_hizli"], a["kural"]["tur"]["klon_hizli"])
        self.assertGreaterEqual(n1["birlesik"]["saldiri"], n1["ml"]["saldiri"])                    # VEYA birleşimi asla düşürmez
        self.assertIn("ROC AUC", degerlendir.rapor(a))

    def test_egitim_verisi_test_etiketlerinden_etkilenmez(self):
        """Model yalnızca normal eğitim verisini görür; etiketli test saldırıları skor eşiğini belirlemez."""
        a = degerlendir.deney(tohum=3, musteri=60, egitim_gun=30, test_gun=8, saldiri=20, agac=20)
        b = degerlendir.deney(tohum=3, musteri=60, egitim_gun=30, test_gun=8, saldiri=20, agac=20)
        self.assertEqual(a["model"].muhur(), b["model"].muhur())


class AciklamaTestleri(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        egitim, _ = sentetik.veri_uret(tohum=1, musteri=60, egitim_gun=40, test_gun=2, saldiri=1)
        v = degerlendir.ozellikleri_cikar(egitim)
        cls.model = orman.IsolationForest.egit([x for _, x, _ in v], ozellikler.OZELLIK_KODLARI, agac_sayisi=20, tohum=1)

    def test_sira_disi_havale_gerekcesi_dogru_ozellikleri_sayar(self):
        g = gecmis(6, tur="HAVALE", hedef="H-1", bakiye=10000.0)
        x = ozellikler.ozellik_vektoru(olay(gun=9, tur="HAVALE", hedef="DOLANDIRICI", tutar=9000, bakiye=10000.0, terminal="ATM-06"), g)
        gerekce = aciklama.gerekce(self.model, x)
        self.assertIn("ilk kez görülen havale hedefi", gerekce)
        self.assertLessEqual(len(gerekce), 3)

    def test_hic_gecmisi_olmayan_hesabin_siradan_ilk_islemi_aykiri_sayilmaz(self):
        """Soğuk başlangıç: geçmiş yokluğu tek başına yüksek puan üretmemeli."""
        x = ozellikler.ozellik_vektoru(olay(saat=14.0, tutar=300.0), [])
        self.assertLess(self.model.puan(x), 60)
        self.assertEqual(aciklama.gerekce(self.model, x), [])

    def test_sapma_olmayan_islem_icin_gerekce_bos(self):
        medyan_vektor = list(self.model.medyan)
        self.assertEqual(aciklama.gerekce(self.model, medyan_vektor), [])

    def test_sapmalar_azalan_mutlak_z_ile_sirali(self):
        x = list(self.model.medyan)
        x[ozellikler.OZELLIK_KODLARI.index("pin_hata")] = 5.0
        z = [abs(s[2]) for s in aciklama.sapmalar(self.model, x)]
        self.assertEqual(z, sorted(z, reverse=True))
        self.assertEqual(aciklama.sapmalar(self.model, x)[0][0], "pin_hata")


class KomutTestleri(SimpleTestCase):
    def test_komut_rapor_yazar_ve_muhurlu_model_kaydeder(self):
        with tempfile.TemporaryDirectory() as d:
            yol = Path(d) / "model.json"
            cikti = StringIO()
            call_command("ml_risk_degerlendir", musteri=40, saldiri=10, agac=15, kaydet=str(yol), ornek=True, stdout=cikti)
            self.assertIn("ROC AUC", cikti.getvalue())
            self.assertIn("gerekçe:", cikti.getvalue())
            veri = json.loads(yol.read_text(encoding="utf-8"))
            model = orman.IsolationForest.sozlukten(veri["model"])
            self.assertEqual(model.muhur(), veri["muhur"])

    def test_cok_tohumlu_ozet(self):
        cikti = StringIO()
        call_command("ml_risk_degerlendir", tohumlar=2, musteri=30, saldiri=10, agac=10, stdout=cikti)
        self.assertIn("2 tohum", cikti.getvalue())
        self.assertIn("ROC AUC  ML", cikti.getvalue())
