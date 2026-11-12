"""Banka Operasyon Simülasyonu testleri: parametre, ölçüm, sandbox, Centrifugo, koşucu, yürütücü, görev ve panel uçları."""
import base64
import hashlib
import hmac
import http.server
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase, override_settings

from .models import LogKaydi, Musteri, SimulasyonKosusu
from .operasyon import centrifugo, gorevler, metrikler, sandbox, veri, yurutucu
from .operasyon.kosucu import Kosucu
from .operasyon.parametreler import SINIRLAR, ParametreHatasi, Parametreler

D = SimulasyonKosusu.Durum
KUCUK = {"musteri_sayisi": 6, "sure_sn": 3, "zaman_olcegi": 0, "isci_sayisi": 1, "terminal_sayisi": 2, "ortalama_islem": 3}


def _b64coz(parca):
    return base64.urlsafe_b64decode(parca + "=" * (-len(parca) % 4))


class ParametreTestleri(SimpleTestCase):
    def test_varsayilanlar_gecerli_ve_gidis_donus(self):
        p = Parametreler.al({})
        self.assertEqual(p.musteri_sayisi, 100)
        self.assertEqual(Parametreler.al(p.sozluk()), p)

    def test_metin_degerler_cevrilir(self):
        p = Parametreler.al({"musteri_sayisi": "25", "onay_red_orani": "0.3", "gercek_hash": "on"})
        self.assertEqual((p.musteri_sayisi, p.onay_red_orani, p.gercek_hash), (25, 0.3, True))
        self.assertFalse(Parametreler.al({"gercek_hash": "false"}).gercek_hash)

    def test_bilinmeyen_bozuk_ve_sinir_disi_reddedilir(self):
        for kotu in ({"yok_boyle": 1}, {"musteri_sayisi": "abc"}, {"musteri_sayisi": 0}, {"musteri_sayisi": 501},
                     {"zaman_olcegi": -1}, {"hatali_pin_orani": 0.9}, {"cekme_agirligi": -1}):
            with self.assertRaises(ParametreHatasi, msg=str(kotu)):
                Parametreler.al(kotu)

    def test_tum_sinirlar_kenar_degerlerde_kabul_edilir(self):
        for ad, (en_az, en_cok) in SINIRLAR.items():
            for deger in (en_az, en_cok):
                Parametreler.al({ad: deger})

    def test_agirliklar_sifir_olamaz(self):
        with self.assertRaises(ParametreHatasi):
            Parametreler.al({"cekme_agirligi": 0, "yatirma_agirligi": 0, "havale_agirligi": 0, "buyuk_cekme_agirligi": 0})


class MetrikTestleri(SimpleTestCase):
    def test_yuzdelik_en_yakin_sira(self):
        self.assertIsNone(metrikler.yuzdelik([], 95))
        self.assertEqual(metrikler.yuzdelik(list(range(1, 101)), 95), 95)
        self.assertEqual(metrikler.yuzdelik([5], 99), 5)
        self.assertEqual(metrikler.yuzdelik([3, 1, 2], 50), 2)

    def test_dagilim(self):
        self.assertEqual(metrikler.dagilim([])["n"], 0)
        d = metrikler.dagilim([10, 20, 30, 40])
        self.assertEqual((d["n"], d["ort"], d["en_yuksek"]), (4, 25.0, 40.0))

    def test_olcer_sayar_ve_akisi_bosaltir(self):
        o = metrikler.Olcer()
        o.islem("cekme", 12.0, None, "ATM-01")
        o.islem("cekme", 30.0, "YETERSIZ_BAKIYE", "ATM-01")
        o.islem("giris", 5.0, None, "ATM-01")             # başarılı giriş akışa girmez
        o.islem("giris", 5.0, "PIN_HATALI", "ATM-02")
        self.assertEqual(o.toplam(), (2, 2))
        akis, n = o.akis_al()
        self.assertEqual(n, 3)
        self.assertEqual([a["islem"] for a in akis], ["cekme", "cekme", "giris"])
        self.assertEqual(o.akis_al(), ([], 0))
        self.assertEqual(o.ozet()["islemler"]["cekme"]["hata_kodlari"], {"YETERSIZ_BAKIYE": 1})
        self.assertEqual(o.terminal_islem["ATM-01"], 2)    # giriş terminal yükü sayılmaz

    def test_akis_sinirli(self):
        o = metrikler.Olcer()
        for i in range(500):
            o.islem("cekme", 1.0, None, "ATM-01")
        akis, n = o.akis_al(10)
        self.assertEqual((len(akis), n), (10, 200))


class SandboxTestleri(SimpleTestCase):
    def test_sqlite_hazirla_eski_dosyayi_siler(self):
        with tempfile.TemporaryDirectory() as d:
            yol = Path(d) / "sim.sqlite3"
            yol.write_text("eski")
            with override_settings(OPERASYON_SANDBOX_SQLITE=str(yol)), mock.patch.object(sandbox, "motor", return_value="sqlite"):
                sandbox.hazirla()
            self.assertFalse(yol.exists())

    def test_ana_veritabaniyla_ayni_olamaz(self):
        ana = {"ENGINE": "django.db.backends.sqlite3", "NAME": "/tmp/ana.sqlite3"}
        with override_settings(OPERASYON_SANDBOX_SQLITE="/tmp/ana.sqlite3", DATABASES={"default": ana}):
            with self.assertRaises(sandbox.SandboxHatasi):
                sandbox.hazirla()
        pg = {"ENGINE": "django.db.backends.postgresql", "NAME": "kasa", "USER": "kasa", "PASSWORD": "", "HOST": "", "PORT": ""}
        with override_settings(OPERASYON_SANDBOX_DB="kasa", DATABASES={"default": pg}):
            with self.assertRaises(sandbox.SandboxHatasi):
                sandbox.hazirla()

    def test_gecersiz_pg_adi_reddedilir(self):
        pg = {"ENGINE": "django.db.backends.postgresql", "NAME": "kasa", "USER": "kasa", "PASSWORD": "", "HOST": "", "PORT": ""}
        for kotu in ('x"; DROP DATABASE kasa; --', "a b", "x" * 60):
            with override_settings(OPERASYON_SANDBOX_DB=kotu, DATABASES={"default": pg}):
                with self.assertRaises(sandbox.SandboxHatasi, msg=kotu):
                    sandbox.hazirla()

    def test_cocuk_ortami_sandboxa_yonlendirir_ve_dis_dunyayi_kapatir(self):
        p = Parametreler.al({})
        pg = {"ENGINE": "django.db.backends.postgresql", "NAME": "kasa", "USER": "kasa", "PASSWORD": "", "HOST": "", "PORT": ""}
        with override_settings(OPERASYON_SANDBOX_DB="kasa_sim", DATABASES={"default": pg}):
            env = sandbox.cocuk_ortami(p)
        self.assertEqual(env["POSTGRES_DB"], "kasa_sim")
        self.assertEqual(env["OPERASYON_SANDBOX_SUREC"], "1")
        self.assertEqual(env["CENTRIFUGO_AKTIF"], "0")
        self.assertEqual(env["HIZLI_PIN_HASH"], "1")
        self.assertIn("BellekSms", env["SMS_SAGLAYICI"])
        self.assertEqual(sandbox.cocuk_ortami(Parametreler.al({"gercek_hash": True}))["HIZLI_PIN_HASH"], "0")


class _Stub(http.server.BaseHTTPRequestHandler):
    kayitlar, cevap = [], b"{}"

    def do_POST(self):
        uzunluk = int(self.headers.get("Content-Length", 0))
        type(self).kayitlar.append({"yol": self.path, "anahtar": self.headers.get("X-API-Key"),
                                    "govde": json.loads(self.rfile.read(uzunluk))})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(type(self).cevap)

    def log_message(self, *a):
        pass


class CentrifugoTestleri(SimpleTestCase):
    def _sunucu(self):
        _Stub.kayitlar, _Stub.cevap = [], b"{}"
        s = http.server.HTTPServer(("127.0.0.1", 0), _Stub)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        self.addCleanup(s.shutdown)
        self.addCleanup(s.server_close)
        return f"http://127.0.0.1:{s.server_port}/api"

    def test_kanal_adinda_ad_alani_ayraci_yok(self):
        self.assertEqual(centrifugo.kanal(7), "simulasyon-7")
        self.assertNotIn(":", centrifugo.kanal(7))

    @override_settings(CENTRIFUGO_HMAC_ANAHTARI="sir")
    def test_jwt_imzali_ve_yalnizca_kendi_kanalina_abone(self):
        jeton = centrifugo.baglanti_jetonu("ali", 5, sure_sn=60, an=1000)
        b, g, i = jeton.split(".")
        self.assertEqual(json.loads(_b64coz(b)), {"alg": "HS256", "typ": "JWT"})
        beklenen = hmac.new(b"sir", f"{b}.{g}".encode(), hashlib.sha256).digest()
        self.assertTrue(hmac.compare_digest(_b64coz(i), beklenen))
        iddia = json.loads(_b64coz(g))
        self.assertEqual((iddia["sub"], iddia["iat"], iddia["exp"]), ("ali", 1000, 1060))
        self.assertEqual(iddia["subs"], {"simulasyon-5": {}})
        self.assertNotIn("channels", iddia)

    def test_yayin_api_anahtariyla_gonderilir(self):
        y = centrifugo.Yayinci(api_url=self._sunucu(), anahtar="api-sir", acik=True)
        self.assertTrue(y.yayinla("simulasyon-1", {"tur": "tick", "islem": 3}))
        kayit = _Stub.kayitlar[0]
        self.assertEqual(kayit["yol"], "/api/publish")
        self.assertEqual(kayit["anahtar"], "api-sir")
        self.assertEqual(kayit["govde"], {"channel": "simulasyon-1", "data": {"tur": "tick", "islem": 3}})

    def test_kapaliyken_hic_istek_yok(self):
        y = centrifugo.Yayinci(api_url=self._sunucu(), anahtar="x", acik=False)
        self.assertFalse(y.yayinla("k", {}))
        self.assertEqual(_Stub.kayitlar, [])

    def test_sunucu_hatasi_yutulur_sayilir(self):
        y = centrifugo.Yayinci(api_url=self._sunucu(), anahtar="x", acik=True)
        _Stub.cevap = b'{"error":{"code":102,"message":"unknown channel"}}'
        self.assertFalse(y.yayinla("k", {}))
        self.assertEqual(y.basarisiz, 1)

    def test_ulasilamayan_sunucu_devre_kesici(self):
        y = centrifugo.Yayinci(api_url="http://127.0.0.1:1/api", anahtar="x", acik=True, zaman_asimi=0.3)
        for _ in range(centrifugo.Yayinci.ESIK + 3):
            self.assertFalse(y.yayinla("k", {}))
        self.assertEqual(y.basarisiz, centrifugo.Yayinci.ESIK)      # eşikten sonra hiç denenmez


class KosucuTestleri(TestCase):
    def _kos(self, **ek):
        p = Parametreler.al({**KUCUK, **ek})
        dunya = veri.kur(p)
        olaylar = []
        ozet = Kosucu(p, dunya, yayin=olaylar.append, tick_sn=0.05).calistir()
        return p, dunya, olaylar, ozet

    def test_dunya_kurulumu(self):
        p = Parametreler.al(KUCUK)
        dunya = veri.kur(p)
        self.assertEqual(len(dunya.musteriler), 6)
        self.assertEqual(len(dunya.terminaller), 2)
        self.assertGreaterEqual(len(dunya.yetkililer), 3)
        self.assertGreaterEqual(Musteri.objects.count(), 6)

    def test_plan_ayni_tohumla_ayni(self):
        p = Parametreler.al(KUCUK)
        dunya = veri.kur(p)
        a, b = Kosucu(p, dunya), Kosucu(p, dunya)
        a.plan_uret(), b.plan_uret()
        self.assertEqual(sorted(a._heap), sorted(b._heap))
        self.assertGreater(a._planli, 0)
        c = Kosucu(Parametreler.al({**KUCUK, "tohum": 7}), dunya)
        c.plan_uret()
        self.assertNotEqual(sorted(a._heap), sorted(c._heap))

    def test_kosu_rapor_ve_butunluk(self):
        p, dunya, olaylar, ozet = self._kos()
        t = ozet["toplam"]
        self.assertGreater(t["islem"], 0)
        self.assertEqual(t["islem"], t["basarili"] + t["basarisiz"])
        self.assertEqual(ozet["istisnalar"], [])
        self.assertTrue(ozet["butunluk"]["defter_saglam"])
        self.assertTrue(ozet["butunluk"]["zincir_saglam"])
        self.assertEqual(len(ozet["terminaller"]), 2)
        self.assertIn("cekme", ozet["islemler"])
        self.assertEqual(ozet["parametreler"]["musteri_sayisi"], 6)
        # sandbox mantığı: işlemler gerçek servis katmanından geçti, hash zincirine yazıldı
        self.assertTrue(LogKaydi.objects.exists())

    def test_canli_olaylar_ve_akis(self):
        _, _, olaylar, ozet = self._kos()
        tickler = [o for o in olaylar if o["tur"] == "tick"]
        self.assertGreaterEqual(len(tickler), 1)
        son = tickler[-1]
        self.assertEqual(son["islem"], ozet["toplam"]["islem"])
        self.assertIn("akis", son)
        self.assertTrue(all("akis" not in s for s in ozet["zaman_serisi"]))   # rapor akışla şişmez

    def test_buyuk_cekmeler_onay_uretir_ve_gecikme_olculur(self):
        _, _, _, ozet = self._kos(cekme_agirligi=0, yatirma_agirligi=0, havale_agirligi=0, buyuk_cekme_agirligi=1,
                                  onay_gecikme_sn=0.1, onay_red_orani=0, musteri_sayisi=8)
        self.assertGreater(sum(ozet["onay"]["sonuclar"].values()), 0)
        self.assertEqual(ozet["onay"]["bekleme_sn"]["n"], sum(ozet["onay"]["sonuclar"].values()))
        self.assertTrue(ozet["butunluk"]["defter_saglam"])


@unittest.skipUnless(connection.vendor == "postgresql", "Çok iş parçacıklı koşu yalnızca PostgreSQL'de anlamlıdır")
class KosucuEszamanliTestleri(TransactionTestCase):
    def test_cok_isciyle_butunluk_bozulmaz(self):
        p = Parametreler.al({**KUCUK, "musteri_sayisi": 12, "isci_sayisi": 4})
        ozet = Kosucu(p, veri.kur(p), tick_sn=0.05).calistir()
        self.assertEqual(ozet["istisnalar"], [])
        self.assertTrue(ozet["butunluk"]["defter_saglam"])
        self.assertTrue(ozet["butunluk"]["zincir_saglam"])
        self.assertGreater(ozet["toplam"]["islem"], 0)


class SahteYayinci:
    def __init__(self, her_tickte=None):
        self.olaylar, self.her_tickte = [], her_tickte

    def yayinla(self, kanal_adi, veri_):
        self.olaylar.append((kanal_adi, veri_))
        if veri_.get("tur") == "tick" and self.her_tickte:
            self.her_tickte()
        return True


class YurutucuTestleri(TestCase):
    def setUp(self):
        self.gecici = tempfile.TemporaryDirectory()
        self.addCleanup(self.gecici.cleanup)
        ayar = override_settings(OPERASYON_SANDBOX_SQLITE=str(Path(self.gecici.name) / "sim.sqlite3"))
        ayar.enable()
        self.addCleanup(ayar.disable)
        if sandbox.motor() == "sqlite":          # sqlite denemesinde sandbox dosyası geçici klasörde
            pass

    def test_yeni_kosu_dogrular_ve_tek_aktif_kosu(self):
        k = yurutucu.yeni_kosu("root", KUCUK)
        self.assertEqual((k.durum, k.olusturan, k.parametreler["musteri_sayisi"]), (D.BEKLIYOR, "root", 6))
        with self.assertRaisesMessage(ValueError, "Zaten"):
            yurutucu.yeni_kosu("root", KUCUK)
        k.durum = D.TAMAMLANDI
        k.save()
        yurutucu.yeni_kosu("root", KUCUK)
        with self.assertRaises(ParametreHatasi):
            SimulasyonKosusu.objects.update(durum=D.IPTAL)
            yurutucu.yeni_kosu("root", {"musteri_sayisi": 9999})

    def test_gercek_alt_surecte_tam_kosu(self):
        k = yurutucu.yeni_kosu("root", KUCUK)
        yay = SahteYayinci()
        yurutucu.calistir(k.id, yayinci=yay)
        k.refresh_from_db()
        self.assertEqual(k.durum, D.TAMAMLANDI, k.hata)
        self.assertGreater(k.ozet["toplam"]["islem"], 0)
        self.assertTrue(k.ozet["butunluk"]["defter_saglam"] and k.ozet["butunluk"]["zincir_saglam"])
        self.assertIsNotNone(k.bitis)
        turler = [o["tur"] for _, o in yay.olaylar]
        self.assertEqual(turler[0], "asama")
        self.assertEqual(turler[-1], "bitti")
        self.assertIn("tick", turler)
        self.assertTrue(all(kanal == f"simulasyon-{k.id}" for kanal, _ in yay.olaylar))
        # gerçek (test) veritabanına sanal müşteri sızmadı
        self.assertFalse(Musteri.objects.filter(hesaplar__hesap_no__startswith="2000").exists())

    def test_ikinci_calistirma_sonuc_degistirmez(self):
        k = yurutucu.yeni_kosu("root", KUCUK)
        k.durum = D.TAMAMLANDI
        k.save()
        yay = SahteYayinci()
        yurutucu.calistir(k.id, yayinci=yay)
        self.assertEqual(yay.olaylar, [])

    def test_alt_surec_cokerse_hata(self):
        k = yurutucu.yeni_kosu("root", KUCUK)
        yurutucu.calistir(k.id, yayinci=SahteYayinci(), komut=[sys.executable, "-c", "import sys; sys.stderr.write('patladi'); sys.exit(3)"])
        k.refresh_from_db()
        self.assertEqual(k.durum, D.HATA)
        self.assertIn("patladi", k.hata)

    def test_rapor_gelmeden_temiz_cikarsa_hata(self):
        k = yurutucu.yeni_kosu("root", KUCUK)
        yurutucu.calistir(k.id, yayinci=SahteYayinci(), komut=[sys.executable, "-c", "print('{\"tur\":\"asama\",\"asama\":\"x\"}')"])
        k.refresh_from_db()
        self.assertEqual(k.durum, D.HATA)

    def test_iptal_alt_sureci_durdurur(self):
        k = yurutucu.yeni_kosu("root", KUCUK)
        betik = ("import json,time,sys\n"
                 "print(json.dumps({'tur':'tick','gecen_sn':1.0,'islem':1}),flush=True)\n"
                 "time.sleep(60)\n")
        yay = SahteYayinci(her_tickte=lambda: SimulasyonKosusu.objects.filter(id=k.id).update(iptal_istendi=True))
        yurutucu.calistir(k.id, yayinci=yay, komut=[sys.executable, "-c", betik])
        k.refresh_from_db()
        self.assertEqual(k.durum, D.IPTAL)
        self.assertEqual(k.son_durum["islem"], 1)

    def test_sandbox_hazirlanamazsa_hata(self):
        k = yurutucu.yeni_kosu("root", KUCUK)
        with mock.patch.object(sandbox, "hazirla", side_effect=sandbox.SandboxHatasi("CREATEDB yok")):
            yurutucu.calistir(k.id, yayinci=SahteYayinci())
        k.refresh_from_db()
        self.assertEqual((k.durum, k.hata), (D.HATA, "CREATEDB yok"))


class GorevTestleri(TestCase):
    def setUp(self):
        gorevler.app.connector.reset()

    def test_kuyruga_atar_ve_is_no_yazar(self):
        k = yurutucu.yeni_kosu("root", KUCUK)
        is_no = gorevler.kuyruga_at(k)
        k.refresh_from_db()
        self.assertIsNotNone(is_no)
        self.assertEqual(k.gorev_id, is_no)
        isler = list(gorevler.app.connector.jobs.values())
        self.assertEqual(len(isler), 1)
        self.assertEqual(isler[0]["task_name"], "banka.operasyon.simulasyon_calistir")
        self.assertEqual(isler[0]["queue_name"], "simulasyon")
        self.assertEqual(isler[0]["args"], {"kosu_id": k.id})

    def test_kuyrukta_ikinci_is_acilmaz(self):
        k1 = yurutucu.yeni_kosu("root", KUCUK)
        gorevler.kuyruga_at(k1)
        k2 = SimulasyonKosusu.objects.create(olusturan="root", parametreler=Parametreler().sozluk())
        self.assertIsNone(gorevler.kuyruga_at(k2))
        k2.refresh_from_db()
        self.assertEqual(k2.durum, D.HATA)
        self.assertIn("Kuyruğa atılamadı", k2.hata)
        self.assertEqual(len(gorevler.app.connector.jobs), 1)

    def test_gorev_yurutucuyu_cagirir(self):
        with mock.patch("banka.operasyon.yurutucu.calistir") as c:
            gorevler.simulasyon_calistir(12)
        c.assert_called_once_with(12)


class KomutTestleri(TestCase):
    def test_calistir_sandbox_disinda_reddeder(self):
        with self.assertRaisesMessage(CommandError, "sandbox"):
            call_command("operasyon_calistir", "--parametreler", "{}")

    def test_calistir_bozuk_parametreyi_reddeder(self):
        with self.assertRaises(CommandError):
            call_command("operasyon_calistir", "--parametreler", '{"musteri_sayisi": 0}', "--kendi-veritabanimda")
        with self.assertRaises(CommandError):
            call_command("operasyon_calistir", "--parametreler", "{bozuk", "--kendi-veritabanimda")

    def test_worker_bellek_kuyrugunda_calismaz(self):
        with self.assertRaisesMessage(CommandError, "bellekte"):
            call_command("operasyon_worker")


@override_settings(OPERASYON_SIM_AKTIF=True, CENTRIFUGO_AKTIF=False)
class GorunumTestleri(TestCase):
    def setUp(self):
        U = get_user_model()
        self.root = U.objects.create_superuser("root_op", "r@banka.example", "dogru-parola-77")
        self.staff = U.objects.create_user("ali_op", "a@banka.example", "dogru-parola-77", is_staff=True)
        self.normal = U.objects.create_user("normal_op", "n@banka.example", "dogru-parola-77")
        gorevler.app.connector.reset()
        self.kosu = SimulasyonKosusu.objects.create(olusturan="root_op", parametreler=Parametreler().sozluk(),
                                                    durum=D.TAMAMLANDI, ozet={"toplam": {"islem": 5}, "zaman_serisi": []})

    def _giris(self, kullanici):
        c = Client()
        c.force_login(kullanici)
        return c

    def test_yetki_matrisi_okuma(self):
        adresler = ["/operasyon/", f"/operasyon/{self.kosu.id}/", f"/operasyon/{self.kosu.id}/durum/"]
        for a in adresler:
            self.assertEqual(Client().get(a).status_code, 302, a)                     # girişsiz: giriş sayfası
            self.assertEqual(self._giris(self.normal).get(a).status_code, 403, a)     # staff değil
            self.assertEqual(self._giris(self.staff).get(a).status_code, 200, a)
            self.assertEqual(self._giris(self.root).get(a).status_code, 200, a)

    def test_yetki_matrisi_yazma(self):
        for url in ("/operasyon/baslat/", f"/operasyon/{self.kosu.id}/iptal/"):
            self.assertEqual(self._giris(self.staff).post(url).status_code, 403, url)   # staff başlatamaz/iptal edemez
            self.assertEqual(self._giris(self.normal).post(url).status_code, 403, url)
            self.assertEqual(Client().post(url).status_code, 302, url)
            self.assertEqual(self._giris(self.root).get(url).status_code, 405, url)     # yalnızca POST
        self.assertEqual(SimulasyonKosusu.objects.count(), 1)

    def test_csrf_zorunlu(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.root)
        self.assertEqual(c.post("/operasyon/baslat/", {"musteri_sayisi": 5}).status_code, 403)
        self.assertEqual(SimulasyonKosusu.objects.count(), 1)

    def test_kapaliyken_hicbir_uc_yok(self):
        with override_settings(OPERASYON_SIM_AKTIF=False):
            c = self._giris(self.root)
            for a in ("/operasyon/", f"/operasyon/{self.kosu.id}/", f"/operasyon/{self.kosu.id}/durum/", f"/operasyon/{self.kosu.id}/jeton/"):
                self.assertEqual(c.get(a).status_code, 404, a)
            for a in ("/operasyon/baslat/", f"/operasyon/{self.kosu.id}/iptal/"):
                self.assertEqual(c.post(a).status_code, 404, a)
            self.assertNotContains(c.get("/"), "Operasyon simülasyonu")

    def test_panelde_baglanti_yalniz_aciksa(self):
        self.assertContains(self._giris(self.staff).get("/"), 'href="/operasyon/"')

    def test_baslat_kayit_acar_kuyruga_atar_yonlendirir(self):
        c = self._giris(self.root)
        r = c.post("/operasyon/baslat/", {**{k: str(v).replace(".", ",") for k, v in KUCUK.items()}, "gercek_hash": "on"})
        SimulasyonKosusu.objects.filter(id=self.kosu.id).delete()
        yeni = SimulasyonKosusu.objects.get()
        self.assertRedirects(r, f"/operasyon/{yeni.id}/", fetch_redirect_response=False)
        self.assertEqual((yeni.olusturan, yeni.parametreler["musteri_sayisi"], yeni.parametreler["gercek_hash"]), ("root_op", 6, True))
        self.assertIsNotNone(yeni.gorev_id)
        self.assertEqual(len(gorevler.app.connector.jobs), 1)

    def test_bos_alan_varsayilani_kullanir(self):
        self._giris(self.root).post("/operasyon/baslat/", {"musteri_sayisi": "", "tohum": "9"})
        yeni = SimulasyonKosusu.objects.exclude(id=self.kosu.id).get()
        self.assertEqual((yeni.parametreler["musteri_sayisi"], yeni.parametreler["tohum"]), (100, 9))

    def test_gecersiz_parametre_400_ve_kayit_yok(self):
        r = self._giris(self.root).post("/operasyon/baslat/", {"musteri_sayisi": "9999"})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "musteri_sayisi", status_code=400)
        self.assertEqual(SimulasyonKosusu.objects.count(), 1)

    def test_aktif_kosu_varken_ikincisi_reddedilir(self):
        SimulasyonKosusu.objects.create(olusturan="x", parametreler={}, durum=D.CALISIYOR)
        r = self._giris(self.root).post("/operasyon/baslat/", {})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "Zaten", status_code=400)

    def test_durum_json_ozet_yalniz_bitince(self):
        c = self._giris(self.staff)
        bitmis = c.get(f"/operasyon/{self.kosu.id}/durum/").json()
        self.assertEqual((bitmis["durum"], bitmis["ozet"]["toplam"]["islem"]), ("TAMAMLANDI", 5))
        self.assertEqual(c.get(f"/operasyon/{self.kosu.id}/durum/")["Cache-Control"], "no-store")
        calisan = SimulasyonKosusu.objects.create(olusturan="x", parametreler={}, durum=D.CALISIYOR,
                                                  son_durum={"tur": "tick", "gecen_sn": 1, "islem": 2}, ozet={"gizli": 1})
        d = c.get(f"/operasyon/{calisan.id}/durum/").json()
        self.assertIsNone(d["ozet"])
        self.assertEqual(d["son_durum"]["islem"], 2)
        self.assertEqual(c.get("/operasyon/99999/durum/").status_code, 404)

    def test_hata_metni_kacirilir(self):
        k = SimulasyonKosusu.objects.create(olusturan="x", parametreler={}, durum=D.HATA, hata="<script>alert(1)</script>")
        r = self._giris(self.staff).get(f"/operasyon/{k.id}/")
        self.assertNotContains(r, "<script>alert(1)</script>")
        self.assertContains(r, "&lt;script&gt;")

    def test_jeton_kapaliyken_404_aciksa_yalniz_bu_kanal(self):
        c = self._giris(self.staff)
        self.assertEqual(c.get(f"/operasyon/{self.kosu.id}/jeton/").status_code, 404)
        with override_settings(CENTRIFUGO_AKTIF=True, CENTRIFUGO_HMAC_ANAHTARI="sir", CENTRIFUGO_WS_URL="ws://x/ws"):
            self.assertEqual(self._giris(self.normal).get(f"/operasyon/{self.kosu.id}/jeton/").status_code, 403)
            veri_ = c.get(f"/operasyon/{self.kosu.id}/jeton/").json()
            iddia = json.loads(_b64coz(veri_["jeton"].split(".")[1]))
        self.assertEqual(veri_["adres"], "ws://x/ws")
        self.assertEqual(iddia["subs"], {f"simulasyon-{self.kosu.id}": {}})
        self.assertEqual(iddia["sub"], "ali_op")
        self.assertNotIn("sir", json.dumps(veri_))                  # anahtar tarayıcıya gitmez
        self.assertNotIn("api", json.dumps(veri_).lower().replace("jeton", ""))

    def test_iptal_kuyruktaki_ve_calisan(self):
        c = self._giris(self.root)
        bekleyen = SimulasyonKosusu.objects.create(olusturan="x", parametreler={}, durum=D.BEKLIYOR)
        c.post(f"/operasyon/{bekleyen.id}/iptal/")
        bekleyen.refresh_from_db()
        self.assertEqual((bekleyen.durum, bool(bekleyen.bitis)), (D.IPTAL, True))
        calisan = SimulasyonKosusu.objects.create(olusturan="x", parametreler={}, durum=D.CALISIYOR)
        c.post(f"/operasyon/{calisan.id}/iptal/")
        calisan.refresh_from_db()
        self.assertEqual((calisan.durum, calisan.iptal_istendi), (D.CALISIYOR, True))      # süreci yürütücü durdurur
        c.post(f"/operasyon/{self.kosu.id}/iptal/")
        self.kosu.refresh_from_db()
        self.assertEqual(self.kosu.durum, D.TAMAMLANDI)                                    # biten koşu etkilenmez

    def test_liste_baslatma_formunu_yalniz_superusera_gosterir(self):
        self.assertContains(self._giris(self.root).get("/operasyon/"), 'action="/operasyon/baslat/"')
        self.assertNotContains(self._giris(self.staff).get("/operasyon/"), 'action="/operasyon/baslat/"')


@override_settings(OPERASYON_SIM_AKTIF=True, LANGUAGE_CODE="tr", USE_L10N=True)
class FormDegerleriTestleri(TestCase):
    def test_ondalik_degerler_noktayla_yazilir(self):
        """Türkçe yerelde 0,02 yazılırsa number alanı boş görünür; değerler nokta ile verilmeli."""
        root = get_user_model().objects.create_superuser("root_fd", "r@banka.example", "dogru-parola-77")
        c = Client()
        c.force_login(root)
        html = c.get("/operasyon/").content.decode()
        for ad, deger in (("zaman_olcegi", "1.0"), ("hatali_pin_orani", "0.02"), ("ortalama_islem", "4.0"), ("onay_red_orani", "0.1")):
            self.assertIn(f'name="{ad}" value="{deger}"', html)
