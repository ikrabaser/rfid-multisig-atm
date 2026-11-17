"""ML risk gölge modu (ml_golge.py): skor kaydedilir, karar değişmez, her arıza sessizce yutulur, model mührü zincirle doğrulanır."""
import io
import json
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from . import baglam, hashlog, ml_golge, services
from .ml_risk import degerlendir, ozellikler, sentetik
from .ml_risk.orman import IsolationForest
from .models import Islem, Kart, LogKaydi
from .tests import Yardimci


def kucuk_model(tohum=1):
    egitim, _ = sentetik.veri_uret(tohum=tohum, musteri=30, egitim_gun=30, test_gun=1, saldiri=1)
    X = [v for _, v, _ in degerlendir.ozellikleri_cikar(egitim)]
    return IsolationForest.egit(X, ozellikler.OZELLIK_KODLARI, agac_sayisi=15, tohum=tohum)


@override_settings(MESAI_DISI_KURALI=False, RISK_MOTORU_AKTIF=False)
class GolgeModTestleri(Yardimci, TestCase):
    UID = "a0a0a001"

    def setUp(self):
        self.hazirla()
        self.dizin = tempfile.TemporaryDirectory()
        self.addCleanup(self.dizin.cleanup)
        self.yol = str(Path(self.dizin.name) / "model.json")
        ayar = override_settings(ML_RISK_MODEL_YOLU=self.yol)
        ayar.enable()
        self.addCleanup(ayar.disable)
        ml_golge._onbellek.clear()
        ml_golge._son_uyari[0] = None

    def golge(self):
        ayar = override_settings(ML_RISK_MODU="golge")
        ayar.enable()
        self.addCleanup(ayar.disable)

    def cek(self, tutar="100", tur="CEKME", **ek):
        with baglam.terminal_baglami("ATM-01"):
            token = services.musteri_giris(self.UID, "1234")["token"]
            return services.islem_baslat(token, tur, tutar, **ek)

    def golge_loglari(self):
        return [json.loads(v) for v in LogKaydi.objects.filter(olay_turu=ml_golge.GOLGE_OLAYI).values_list("veri", flat=True)]

    # ---- kapalı (varsayılan)
    def test_varsayilan_kapali_ve_hicbir_sey_yazmaz(self):
        from django.conf import settings
        self.assertEqual(settings.ML_RISK_MODU, "kapali")
        ml_golge.modeli_yaz(kucuk_model(), self.yol)
        islem = Islem.objects.get(id=self.cek()["islem_id"])
        self.assertIsNone(islem.ml_puan)
        self.assertEqual((islem.ml_gerekce, self.golge_loglari()), ("", []))

    # ---- gölge
    def test_golge_skor_islemle_ve_zincirde_saklanir(self):
        ml_golge.modeli_yaz(kucuk_model(), self.yol)
        self.golge()
        r = self.cek("250")
        islem = Islem.objects.get(id=r["islem_id"])
        self.assertIsNotNone(islem.ml_puan)
        self.assertTrue(0 <= islem.ml_puan <= 100)
        (kayit,) = self.golge_loglari()
        self.assertEqual((kayit["islem_id"], kayit["ml_puan"], kayit["tur"], kayit["tutar"]), (islem.id, islem.ml_puan, "CEKME", "250.00"))
        self.assertEqual((kayit["kural_skoru"], kayit["kural_karari"]), (0, "GEC"))
        self.assertTrue(hashlog.zinciri_dogrula()["saglam"])

    def test_havale_de_skorlanir_yatirma_skorlanmaz(self):
        ml_golge.modeli_yaz(kucuk_model(), self.yol)
        self.golge()
        havale = Islem.objects.get(id=self.cek("50", "HAVALE", hedef_hesap_no="100002")["islem_id"])
        self.assertIsNotNone(havale.ml_puan)
        yatirma = Islem.objects.get(id=self.cek("50", "YATIRMA")["islem_id"])
        self.assertIsNone(yatirma.ml_puan)

    def test_karar_degismez_yuksek_ml_puani_kural_gecen_islemi_durdurmaz(self):
        self.golge()
        sahte = {"puan": 100, "skor": 0.9, "gerekce": ["tutarın olağan tutara oranı (log)"], "muhur": "x" * 16}
        with mock.patch.object(ml_golge, "skorla", return_value=sahte):
            r = self.cek("100")
        self.assertEqual(r["durum"], "uygulandi")
        islem = Islem.objects.get(id=r["islem_id"])
        self.assertEqual((islem.ml_puan, islem.durum), (100, Islem.Durum.UYGULANDI))
        self.assertIn("olağan tutar", islem.ml_gerekce)

    def test_ayni_islem_acik_ve_kapali_modda_ayni_sonucu_verir(self):
        ml_golge.modeli_yaz(kucuk_model(), self.yol)
        kapali = Islem.objects.get(id=self.cek("300")["islem_id"])
        self.golge()
        acik = Islem.objects.get(id=self.cek("300")["islem_id"])
        self.assertEqual((kapali.durum, kapali.risk_skoru, kapali.tutar), (acik.durum, acik.risk_skoru, acik.tutar))

    # ---- arıza yutma
    def test_model_dosyasi_yoksa_islem_normal_surer(self):
        self.golge()
        islem = Islem.objects.get(id=self.cek()["islem_id"])
        self.assertEqual((islem.durum, islem.ml_puan), (Islem.Durum.UYGULANDI, None))
        self.assertEqual(self.golge_loglari(), [])

    def test_dosya_muhru_tutmuyorsa_devre_disi(self):
        ml_golge.modeli_yaz(kucuk_model(), self.yol)
        veri = json.loads(Path(self.yol).read_text(encoding="utf-8"))
        veri["model"]["egitim_skorlari"][0] += 0.01
        Path(self.yol).write_text(json.dumps(veri), encoding="utf-8")
        self.golge()
        model, sebep = ml_golge.modeli_yukle()
        self.assertIsNone(model)
        self.assertIn("mührü", sebep)
        self.assertIsNone(Islem.objects.get(id=self.cek()["islem_id"]).ml_puan)

    def test_dosya_mühruyle_birlikte_degistirilse_bile_zincir_yakalar(self):
        ml_golge.modeli_yaz(kucuk_model(1), self.yol)
        baska = kucuk_model(2)                                   # saldırgan: kendi tutarlı modelini ve mührünü koyar
        Path(self.yol).write_text(json.dumps({"muhur": baska.muhur(), "model": baska.sozluge()}), encoding="utf-8")
        ml_golge._onbellek.clear()
        model, sebep = ml_golge.modeli_yukle()
        self.assertIsNone(model)
        self.assertIn("zincir", sebep)

    def test_zincirde_egitim_kaydi_yoksa_devre_disi(self):
        m = kucuk_model()
        Path(self.yol).write_text(json.dumps({"muhur": m.muhur(), "model": m.sozluge()}), encoding="utf-8")
        model, sebep = ml_golge.modeli_yukle()
        self.assertIsNone(model)
        self.assertIn("zincir", sebep)

    def test_uyumsuz_ozellik_listesi_reddedilir(self):
        egitim, _ = sentetik.veri_uret(tohum=1, musteri=20, egitim_gun=20, test_gun=1, saldiri=1)
        X = [v[:3] for _, v, _ in degerlendir.ozellikleri_cikar(egitim)]
        m = IsolationForest.egit(X, ["a", "b", "c"], agac_sayisi=5, tohum=1)
        ml_golge.modeli_yaz(m, self.yol)
        model, sebep = ml_golge.modeli_yukle()
        self.assertIsNone(model)
        self.assertIn("uyumsuz", sebep)

    def test_skorlama_patlarsa_islem_yine_de_uygulanir(self):
        ml_golge.modeli_yaz(kucuk_model(), self.yol)
        self.golge()
        with mock.patch.object(ml_golge.ozellikler, "ozellik_vektoru", side_effect=RuntimeError("boom")):
            with self.assertLogs("banka.ml_golge", level="ERROR"):
                r = self.cek("100")
        islem = Islem.objects.get(id=r["islem_id"])
        self.assertEqual((islem.durum, islem.ml_puan), (Islem.Durum.UYGULANDI, None))

    def test_zincir_yazimi_patlarsa_islem_yine_de_uygulanir(self):
        ml_golge.modeli_yaz(kucuk_model(), self.yol)
        self.golge()
        gercek = ml_golge.kayit_ekle

        def hatali(olay, veri):
            if olay == ml_golge.GOLGE_OLAYI:
                raise RuntimeError("zincir")
            return gercek(olay, veri)
        with mock.patch.object(ml_golge, "kayit_ekle", hatali), self.assertLogs("banka.ml_golge", level="ERROR"):
            r = self.cek("100")
        self.assertEqual(r["durum"], "uygulandi")
        self.assertEqual(self.golge_loglari(), [])

    def test_ayni_uyari_tekrar_tekrar_yazilmaz(self):
        self.golge()
        with self.assertLogs("banka.ml_golge", level="WARNING") as g:
            self.cek("100")
            self.cek("100")
        self.assertEqual(len(g.output), 1)

    # ---- özellik hesabı gerçek veriden
    def test_gecmis_islemler_ozellige_yansir_ayni_hesabin_olagan_tutari_dusuk_puan_ister(self):
        ml_golge.modeli_yaz(kucuk_model(), self.yol)
        self.golge()
        for _ in range(4):
            self.cek("100")
        kart = Kart.objects.get(uid=self.UID)
        hesap = self.ali
        hesap.refresh_from_db()
        with baglam.terminal_baglami("ATM-01"):
            olagan = ml_golge.skorla(kart, hesap, "CEKME", Decimal("100"))
            garip = ml_golge.skorla(kart, hesap, "CEKME", Decimal("9000"))
        self.assertGreater(garip["puan"], olagan["puan"])
        self.assertTrue(any(g.startswith("tutarın olağan tutara oranı") for g in garip["gerekce"]))


@override_settings(RISK_MOTORU_AKTIF=False)
class EgitimKomutuTestleri(TestCase):
    def setUp(self):
        self.dizin = tempfile.TemporaryDirectory()
        self.addCleanup(self.dizin.cleanup)
        self.yol = str(Path(self.dizin.name) / "m.json")
        ml_golge._onbellek.clear()

    def calistir(self, *arg):
        cikti = io.StringIO()
        call_command("ml_risk_egit", "--yol", self.yol, *arg, stdout=cikti)
        return cikti.getvalue()

    def test_egitir_zincire_yazar_ve_dogrular(self):
        cikti = self.calistir("--musteri", "30", "--agac", "10")
        self.assertIn("mühür", cikti)
        self.assertEqual(LogKaydi.objects.filter(olay_turu=ml_golge.EGITIM_OLAYI).count(), 1)
        self.assertIn("Model geçerli", self.calistir("--dogrula"))

    def test_dogrula_bozuk_ya_da_olmayan_modelde_hata_verir(self):
        with self.assertRaises(CommandError):
            self.calistir("--dogrula")
        self.calistir("--musteri", "30", "--agac", "5")
        veri = json.loads(Path(self.yol).read_text(encoding="utf-8"))
        veri["muhur"] = "0" * 64
        Path(self.yol).write_text(json.dumps(veri), encoding="utf-8")
        ml_golge._onbellek.clear()
        with self.assertRaises(CommandError):
            self.calistir("--dogrula")

    def test_yeniden_egitim_zincirde_yeni_kayit_acar_ve_eski_dosya_gecersiz_olur(self):
        self.calistir("--musteri", "30", "--agac", "5", "--tohum", "1")
        eski = Path(self.yol).read_text(encoding="utf-8")
        self.calistir("--musteri", "30", "--agac", "5", "--tohum", "2")
        Path(self.yol).write_text(eski, encoding="utf-8")        # eski (geçerli ama artık güncel olmayan) dosya geri konur
        ml_golge._onbellek.clear()
        with self.assertRaises(CommandError):
            self.calistir("--dogrula")
