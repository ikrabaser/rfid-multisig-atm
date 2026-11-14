"""Terminal izleme: heartbeat, çevrimdışı tespiti, otomatik arıza vakası, köprü ve panel entegrasyonu."""
import datetime
import sys
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from . import izleme
from .hashlog import zinciri_dogrula
from .models import ArizaVakasi, LogKaydi, Personel, Terminal
from .tests import ApiYardimci

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bridge"))
import cerceve  # noqa: E402

ESIK = 45


def sn(n):
    return datetime.timedelta(seconds=n)


@override_settings(TERMINAL_CEVRIMDISI_SN=ESIK, MESAI_DISI_KURALI=False)
class IzlemeServisiTestleri(ApiYardimci, TestCase):
    def setUp(self):
        self.api_hazirla()
        self.t0 = timezone.now()
        self.terminal = Terminal.objects.get(kod="ATM-01")

    def yenile(self):
        self.terminal.refresh_from_db()
        return self.terminal

    def test_hic_gorulmemis_terminal_bilinmiyor_ve_vaka_acilmaz(self):
        self.assertEqual(izleme.baglanti_durumu(self.yenile())["durum"], izleme.BILINMIYOR)
        self.assertEqual(izleme.tara(self.t0 + sn(3600)), [])
        self.assertEqual(ArizaVakasi.objects.count(), 0)

    def test_nabiz_terminali_cevrimici_yapar_esikten_sonra_cevrimdisi(self):
        izleme.nabiz("ATM-01", self.t0, zorla=True)
        self.assertEqual(izleme.baglanti_durumu(self.yenile(), self.t0 + sn(ESIK))["durum"], izleme.CEVRIMICI)
        d = izleme.baglanti_durumu(self.yenile(), self.t0 + sn(ESIK + 1))
        self.assertEqual((d["durum"], d["sn_once"], d["esik_sn"]), (izleme.CEVRIMDISI, ESIK + 1, ESIK))

    def test_durum_tara_ile_ayni_olcutu_kullanir_kesirli_saniyede_celiskisiz(self):
        """Eşiği 0.9 sn aşan terminal: tara() vaka açar, durum da ÇEVRİMDIŞI der (tam sayıya yuvarlama çelişki yaratmaz)."""
        izleme.nabiz("ATM-01", self.t0, zorla=True)
        an = self.t0 + sn(ESIK) + datetime.timedelta(milliseconds=900)
        self.assertEqual(len(izleme.tara(an)), 1)
        d = izleme.baglanti_durumu(self.yenile(), an)
        self.assertEqual((d["durum"], d["sn_once"]), (izleme.CEVRIMDISI, ESIK))

    def test_esik_asilinca_tek_vaka_acilir_tekrar_tarama_cift_vaka_acmaz(self):
        izleme.nabiz("ATM-01", self.t0, zorla=True)
        self.assertEqual(izleme.tara(self.t0 + sn(ESIK)), [])                 # tam eşikte henüz değil
        yeni = izleme.tara(self.t0 + sn(ESIK + 5))
        self.assertEqual(len(yeni), 1)
        self.assertEqual(izleme.tara(self.t0 + sn(ESIK + 60)), [])
        vaka = ArizaVakasi.objects.get()
        self.assertEqual((vaka.terminal.kod, vaka.tur, vaka.durum), ("ATM-01", "BAGLANTI", "ACIK"))
        self.assertEqual(vaka.son_gorulme, self.t0)
        self.assertIn("heartbeat yok", vaka.aciklama)

    def test_vaka_en_az_yuklu_gorevliye_atanir(self):
        gorevliler = list(Personel.objects.filter(rol=1, aktif=True).order_by("id"))
        self.assertGreaterEqual(len(gorevliler), 2)
        for kod in ("ATM-01", "ATM-02"):
            izleme.nabiz(kod, self.t0, zorla=True)
        izleme.tara(self.t0 + sn(ESIK + 5))
        atananlar = [v.atanan_id for v in ArizaVakasi.objects.order_by("id")]
        self.assertEqual(atananlar, [gorevliler[0].id, gorevliler[1].id])      # ikinci vaka diğer görevliye gider

    def test_atanacak_gorevli_yoksa_vaka_yine_acilir(self):
        Personel.objects.filter(rol=1).update(aktif=False)
        izleme.nabiz("ATM-01", self.t0, zorla=True)
        izleme.tara(self.t0 + sn(ESIK + 5))
        self.assertIsNone(ArizaVakasi.objects.get().atanan)

    def test_acilis_ve_kapanis_hash_zincirine_yazilir_ve_zincir_saglam_kalir(self):
        izleme.nabiz("ATM-01", self.t0, zorla=True)
        izleme.tara(self.t0 + sn(ESIK + 5))
        izleme.nabiz("ATM-01", self.t0 + sn(100), zorla=True)
        turler = list(LogKaydi.objects.order_by("sira").values_list("olay_turu", flat=True))
        self.assertIn("terminal_cevrimdisi", turler)
        self.assertIn("terminal_baglanti_geri", turler)
        self.assertTrue(zinciri_dogrula()["saglam"])

    def test_heartbeat_geri_gelince_vaka_cozulur_ve_sure_kaydedilir(self):
        izleme.nabiz("ATM-01", self.t0, zorla=True)
        izleme.tara(self.t0 + sn(ESIK + 5))
        izleme.nabiz("ATM-01", self.t0 + sn(120), zorla=True)
        vaka = ArizaVakasi.objects.get()
        self.assertEqual((vaka.durum, vaka.sure_sn), ("COZULDU", 120))
        self.assertEqual(vaka.kapanis, self.t0 + sn(120))
        self.assertEqual(izleme.baglanti_durumu(self.yenile(), self.t0 + sn(121))["durum"], izleme.CEVRIMICI)
        # sonraki kesinti yeni vaka açar (eski çözülmüş vaka engel olmaz)
        izleme.tara(self.t0 + sn(120 + ESIK + 5))
        self.assertEqual(ArizaVakasi.objects.filter(durum="ACIK").count(), 1)
        self.assertEqual(ArizaVakasi.objects.count(), 2)

    def test_sik_dokunma_veritabanina_yazmaz_ama_acik_vakayi_hemen_cozer(self):
        izleme.nabiz("ATM-01", self.t0, zorla=True)
        izleme.nabiz("ATM-01", self.t0 + sn(1))                                # 3 sn'den yeni: yazılmaz
        self.assertEqual(self.yenile().son_gorulme, self.t0)
        izleme.tara(self.t0 + sn(ESIK + 5))
        izleme.nabiz("ATM-01", self.t0 + sn(ESIK + 6))                         # açık vaka varken zorlamasız da çözer
        self.assertEqual(ArizaVakasi.objects.get().durum, "COZULDU")

    def test_kapali_veya_bilinmeyen_terminal_icin_nabiz_yok_sayilir(self):
        Terminal.objects.filter(kod="ATM-02").update(aktif=False)
        self.assertIsNone(izleme.nabiz("ATM-02", self.t0, zorla=True))
        self.assertIsNone(izleme.nabiz("ATM-99", self.t0, zorla=True))

    def test_idari_olarak_kapatilan_terminal_taranmaz(self):
        izleme.nabiz("ATM-02", self.t0, zorla=True)
        Terminal.objects.filter(kod="ATM-02").update(aktif=False)
        self.assertEqual(izleme.tara(self.t0 + sn(ESIK + 5)), [])

    def test_veritabani_ayni_terminal_icin_iki_acik_vaka_kabul_etmez(self):
        from django.db import IntegrityError, transaction
        ArizaVakasi.objects.create(terminal=self.terminal)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ArizaVakasi.objects.create(terminal=self.terminal)

    def test_terminal_izle_komutu(self):
        izleme.nabiz("ATM-01", timezone.now() - sn(ESIK + 30), zorla=True)
        from io import StringIO
        cikti = StringIO()
        call_command("terminal_izle", stdout=cikti)
        self.assertIn("ATM-01 çevrimdışı", cikti.getvalue())
        self.assertIn("Açık vaka: 1", cikti.getvalue())
        cikti = StringIO()
        call_command("terminal_izle", stdout=cikti)                           # ikinci çalıştırma: yeni vaka yok
        self.assertNotIn("çevrimdışı: vaka", cikti.getvalue())
        self.assertEqual(ArizaVakasi.objects.count(), 1)


@override_settings(TERMINAL_CEVRIMDISI_SN=ESIK, MESAI_DISI_KURALI=False)
class HeartbeatApiTestleri(ApiYardimci, TestCase):
    def setUp(self):
        self.api_hazirla()

    def test_heartbeat_anahtar_ister(self):
        self.assertEqual(self.api("/api/heartbeat/", {}, terminal="ATM-01", anahtar=False).status_code, 401)
        self.assertIsNone(Terminal.objects.get(kod="ATM-01").son_gorulme)

    def test_heartbeat_terminali_gorulmus_yapar(self):
        r = self.api("/api/heartbeat/", {}, terminal="ATM-02")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["durum"], r.json()["esik_sn"]), ("CEVRIMICI", ESIK))
        self.assertIsNotNone(Terminal.objects.get(kod="ATM-02").son_gorulme)
        self.assertIsNone(Terminal.objects.get(kod="ATM-01").son_gorulme)

    def test_bilinmeyen_terminalin_heartbeati_reddedilir(self):
        r = self.api("/api/heartbeat/", {}, terminal="ATM-99")
        self.assertEqual((r.status_code, r.json()["kod"]), (403, "terminal"))

    def test_baska_api_istegi_de_terminali_gorulmus_yapar(self):
        self.api("/api/kart/", {"uid": "a0a0a001"}, terminal="ATM-02")
        self.assertIsNotNone(Terminal.objects.get(kod="ATM-02").son_gorulme)

    def test_heartbeat_acik_vakayi_cozer(self):
        eski = timezone.now() - sn(ESIK + 100)
        Terminal.objects.filter(kod="ATM-01").update(son_gorulme=eski)
        izleme.tara()
        self.assertEqual(ArizaVakasi.objects.filter(durum="ACIK").count(), 1)
        self.api("/api/heartbeat/", {}, terminal="ATM-01")
        self.assertEqual(ArizaVakasi.objects.get().durum, "COZULDU")


class KopruNabizTestleri(TestCase):
    """bridge/cerceve.py Oturum: HELLO/PING heartbeat geri çağrısını çalıştırır, hata cevabı bozmaz."""

    def oturum(self, nabiz):
        return cerceve.Oturum(lambda yuk: "TIP|YOK", kati=True, nabiz=nabiz)

    def test_hello_ve_ping_nabiz_cagirir_diger_komutlar_cagirmaz(self):
        sayac = []
        o = self.oturum(lambda: sayac.append(1))
        o.satir_isle(cerceve.kodla(1, "HELLO|2"))
        o.satir_isle(cerceve.kodla(2, "PING"))
        self.assertEqual(len(sayac), 2)
        o.satir_isle(cerceve.kodla(3, "KART|a0a0a001"))
        self.assertEqual(len(sayac), 2)

    def test_nabiz_hatasi_pong_cevabini_bozmaz(self):
        def patla():
            raise RuntimeError("sunucu yok")
        o = self.oturum(patla)
        cevap = o.satir_isle(cerceve.kodla(5, "PING"))
        self.assertEqual(cerceve.coz(cevap), (5, "PONG"))
        self.assertEqual(o.istatistik["nabiz_hatasi"], 1)

    def test_bozuk_cerceve_nabiz_sayilmaz(self):
        sayac = []
        o = self.oturum(lambda: sayac.append(1))
        bozuk = cerceve.kodla(1, "PING")[:-1] + "0"
        if bozuk == cerceve.kodla(1, "PING"):
            bozuk = bozuk[:-1] + "1"
        o.satir_isle(bozuk)
        self.assertEqual(sayac, [])
