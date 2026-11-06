import datetime
import io
import json
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from . import services
from .hashlog import zinciri_dogrula
from .models import Hesap, Islem, Kart, LogKaydi, OnayTalebi, Personel, Rol
from .services import IslemHatasi

# seed_demo verisi: Ali 100001 (a0a0a001/1234), Zeynep 100002 (a0a0a002/4321)
# Yetkililer: b0b0b001..002 görevli, 003..004 müdür yrd., 005 müdür; PIN 1111..5555
AYSE = ("b0b0b001", "1111")
BURAK = ("b0b0b002", "2222")
CEM = ("b0b0b003", "3333")
DENIZ = ("b0b0b004", "4444")
ELIF = ("b0b0b005", "5555")


@override_settings(MESAI_DISI_KURALI=False)
class BankaTestleri(TestCase):
    def setUp(self):
        call_command("seed_demo", stdout=io.StringIO())
        self.ali = Hesap.objects.get(hesap_no="100001")
        self.zeynep = Hesap.objects.get(hesap_no="100002")

    def giris(self, uid="a0a0a001", pin="1234"):
        return services.musteri_giris(uid, pin)["token"]

    def bakiye(self, hesap):
        hesap.refresh_from_db()
        return hesap.bakiye

    def onayla(self, talep_id, kisi, karar="ONAY"):
        return services.onay_karar(talep_id, kisi[0], kisi[1], karar)

    # ------------------------------------------------------------ islemler

    def test_kucuk_cekim_onaysiz_gecer(self):
        s = services.islem_baslat(self.giris(), "CEKME", "1000")
        self.assertEqual(s["durum"], "uygulandi")
        self.assertEqual(self.bakiye(self.ali), Decimal("999000.00"))
        self.assertTrue(zinciri_dogrula()["saglam"])

    def test_yatirma_onaysiz_gecer(self):
        services.islem_baslat(self.giris(), "YATIRMA", "2500.50")
        self.assertEqual(self.bakiye(self.ali), Decimal("1002500.50"))

    def test_yetersiz_bakiye(self):
        token = self.giris("a0a0a002", "4321")
        with self.assertRaises(IslemHatasi) as e:
            services.islem_baslat(token, "CEKME", "300000")
        self.assertEqual(e.exception.kod, "bakiye")
        self.assertEqual(self.bakiye(self.zeynep), Decimal("250000.00"))

    def test_gecersiz_tutarlar(self):
        token = self.giris()
        for kotu in ["0", "-5", "abc", "nan", "inf", ""]:
            with self.assertRaises(IslemHatasi, msg=kotu):
                services.islem_baslat(token, "CEKME", kotu)

    def test_kucuk_havale_iki_hesabi_gunceller(self):
        toplam = self.ali.bakiye + self.zeynep.bakiye
        services.islem_baslat(self.giris(), "HAVALE", "3000", "100002")
        self.assertEqual(self.bakiye(self.ali), Decimal("997000.00"))
        self.assertEqual(self.bakiye(self.zeynep), Decimal("253000.00"))
        self.assertEqual(self.bakiye(self.ali) + self.bakiye(self.zeynep), toplam)

    def test_kendine_havale_ve_olmayan_hesap(self):
        token = self.giris()
        with self.assertRaises(IslemHatasi):
            services.islem_baslat(token, "HAVALE", "10", "100001")
        with self.assertRaises(IslemHatasi):
            services.islem_baslat(token, "HAVALE", "10", "999999")

    # ------------------------------------------------------------ onay akisi

    def test_orta_tutar_bir_onay_ister(self):
        s = services.islem_baslat(self.giris(), "CEKME", "10000")
        self.assertEqual(s["durum"], "onay_bekliyor")
        self.assertEqual(s["gereken"], 1)
        self.assertEqual(self.bakiye(self.ali), Decimal("1000000.00"))  # henuz dusmedi
        sonuc = self.onayla(s["talep_id"], AYSE)
        self.assertEqual(sonuc["durum"], "UYGULANDI")
        self.assertEqual(self.bakiye(self.ali), Decimal("990000.00"))

    def test_buyuk_tutar_rol_sarti(self):
        s = services.islem_baslat(self.giris(), "CEKME", "600000")
        self.assertEqual(s["gereken"], 3)
        self.onayla(s["talep_id"], AYSE)
        self.onayla(s["talep_id"], BURAK)
        sonuc = self.onayla(s["talep_id"], CEM)  # 3 onay var ama mudur yok
        self.assertEqual(sonuc["durum"], "BEKLIYOR")
        self.assertTrue(sonuc["ust_yetkili_gerekli"])
        self.assertEqual(self.bakiye(self.ali), Decimal("1000000.00"))
        sonuc = self.onayla(s["talep_id"], ELIF)
        self.assertEqual(sonuc["durum"], "UYGULANDI")
        self.assertEqual(self.bakiye(self.ali), Decimal("400000.00"))

    def test_onayli_havale_atomik(self):
        s = services.islem_baslat(self.giris(), "HAVALE", "60000", "100002")
        self.assertEqual(s["gereken"], 2)
        self.onayla(s["talep_id"], AYSE)
        self.assertEqual(self.bakiye(self.zeynep), Decimal("250000.00"))
        self.onayla(s["talep_id"], CEM)
        self.assertEqual(self.bakiye(self.ali), Decimal("940000.00"))
        self.assertEqual(self.bakiye(self.zeynep), Decimal("310000.00"))

    def test_ayni_yetkili_iki_kez_sayilmaz(self):
        s = services.islem_baslat(self.giris(), "CEKME", "100000")
        self.onayla(s["talep_id"], AYSE)
        with self.assertRaises(IslemHatasi) as e:
            self.onayla(s["talep_id"], AYSE)
        self.assertEqual(e.exception.kod, "tekrar")
        self.assertEqual(services.talep_durumu(s["talep_id"])["verilen"], 1)

    def test_baslatan_kendi_islemini_onaylayamaz(self):
        p = Personel(ad="Ali (aynı kart)", kart_uid="a0a0a001", rol=Rol.MUDUR)
        p.pin_ayarla("9999")
        p.save()
        s = services.islem_baslat(self.giris(), "CEKME", "10000")
        with self.assertRaises(IslemHatasi) as e:
            services.onay_karar(s["talep_id"], "a0a0a001", "9999", "ONAY")
        self.assertEqual(e.exception.kod, "kendi_islemi")
        self.assertEqual(services.talep_durumu(s["talep_id"])["durum"], "BEKLIYOR")

    def test_sure_dolunca_islem_iptal(self):
        s = services.islem_baslat(self.giris(), "CEKME", "10000")
        OnayTalebi.objects.filter(id=s["talep_id"]).update(
            son_tarih=timezone.now() - datetime.timedelta(seconds=1)
        )
        sonuc = self.onayla(s["talep_id"], AYSE)
        self.assertEqual(sonuc["durum"], "SURESI_DOLDU")
        self.assertEqual(self.bakiye(self.ali), Decimal("1000000.00"))
        self.assertEqual(Islem.objects.get(id=s["islem_id"]).durum, "SURESI_DOLDU")
        self.assertIsNone(services.bekleyen_talep())

    def test_yetkili_reddederse_islem_iptal(self):
        s = services.islem_baslat(self.giris(), "CEKME", "10000")
        sonuc = self.onayla(s["talep_id"], AYSE, "RED")
        self.assertEqual(sonuc["durum"], "REDDEDILDI")
        self.assertEqual(self.bakiye(self.ali), Decimal("1000000.00"))

    def test_musteri_karti_onay_veremez(self):
        s = services.islem_baslat(self.giris(), "CEKME", "10000")
        with self.assertRaises(IslemHatasi) as e:
            services.onay_karar(s["talep_id"], "a0a0a002", "4321", "ONAY")
        self.assertEqual(e.exception.kod, "yetkisiz")

    def test_bekleyen_talep_ve_durum(self):
        self.assertIsNone(services.bekleyen_talep())
        s = services.islem_baslat(self.giris(), "CEKME", "100000")
        b = services.bekleyen_talep()
        self.assertEqual(b["talep_id"], s["talep_id"])
        self.assertEqual((b["verilen"], b["gereken"]), (0, 2))
        self.assertGreater(b["kalan_sn"], 0)

    def test_mesai_disi_kurali(self):
        gece = datetime.datetime(2026, 11, 4, 3, 0, tzinfo=ZoneInfo("Europe/Istanbul"))
        gunduz = datetime.datetime(2026, 11, 4, 12, 0, tzinfo=ZoneInfo("Europe/Istanbul"))
        with override_settings(MESAI_DISI_KURALI=True):
            self.assertEqual(services.gerekli_onay(Decimal("10000"), gunduz), (1, Rol.GOREVLI))
            self.assertEqual(services.gerekli_onay(Decimal("10000"), gece), (2, Rol.MUDUR_YRD))
            self.assertEqual(services.gerekli_onay(Decimal("1000"), gece)[0], 0)  # küçük işlem yine onaysız

    # ------------------------------------------------------------ PIN

    def test_uc_yanlis_pin_karti_bloke_eder(self):
        with self.assertRaises(IslemHatasi) as e:
            services.musteri_giris("a0a0a001", "0000")
        self.assertEqual(e.exception.ekstra["kalan_hak"], 2)
        with self.assertRaises(IslemHatasi):
            services.musteri_giris("a0a0a001", "0000")
        with self.assertRaises(IslemHatasi) as e:
            services.musteri_giris("a0a0a001", "0000")
        self.assertEqual(e.exception.kod, "bloke")
        with self.assertRaises(IslemHatasi) as e:  # dogru PIN bile artik gecmez
            services.musteri_giris("a0a0a001", "1234")
        self.assertEqual(e.exception.kod, "bloke")
        self.assertEqual(services.kart_tipi("a0a0a001"), "BLOKE")

    def test_dogru_pin_sayaci_sifirlar(self):
        with self.assertRaises(IslemHatasi):
            services.musteri_giris("a0a0a001", "0000")
        self.giris()
        self.assertEqual(Kart.objects.get(uid="a0a0a001").hatali_deneme, 0)

    def test_kart_tipleri_ve_uid_bicimi(self):
        self.assertEqual(services.kart_tipi("A0A0A001"), "MUSTERI")
        self.assertEqual(services.kart_tipi("b0b0b003"), "PERSONEL")
        self.assertEqual(services.kart_tipi("ffffffff"), "BILINMIYOR")

    def test_token_gecersiz(self):
        with self.assertRaises(IslemHatasi):
            services.bakiye_sorgula("uydurma-token")
        with override_settings(OTURUM_SURESI_SN=-1):
            token = self.giris()
            with self.assertRaises(IslemHatasi):
                services.bakiye_sorgula(token)

    # ------------------------------------------------------------ hash zinciri

    def test_zincir_saglam_ve_degisiklik_yakalanir(self):
        token = self.giris()
        services.islem_baslat(token, "CEKME", "1000")
        services.islem_baslat(token, "YATIRMA", "500")
        self.assertTrue(zinciri_dogrula()["saglam"])
        LogKaydi.objects.filter(sira=2).update(veri='{"hile":true}')
        sonuc = zinciri_dogrula()
        self.assertFalse(sonuc["saglam"])
        self.assertEqual(sonuc["bozuk_sira"], 2)

    def test_zincirden_kayit_silinirse_yakalanir(self):
        token = self.giris()
        services.islem_baslat(token, "CEKME", "1000")
        services.islem_baslat(token, "YATIRMA", "500")
        LogKaydi.objects.filter(sira=2).delete()
        sonuc = zinciri_dogrula()
        self.assertFalse(sonuc["saglam"])
        self.assertEqual(sonuc["bozuk_sira"], 2)

    def test_log_kaydi_degistirilemez(self):
        services.islem_baslat(self.giris(), "CEKME", "1000")
        k = LogKaydi.objects.first()
        with self.assertRaises(ValidationError):
            k.save()
        with self.assertRaises(ValidationError):
            k.delete()

    def test_log_dogrula_komutu_beklenen_son_hash(self):
        services.islem_baslat(self.giris(), "CEKME", "1000")
        son = zinciri_dogrula()["son_hash"]
        call_command("log_dogrula", "--beklenen-son-hash", son, verbosity=0)
        with self.assertRaises(SystemExit):
            call_command("log_dogrula", "--beklenen-son-hash", "yanlis", verbosity=0)

    def test_hmac_anahtari_degisirse_zincir_dogrulanmaz(self):
        services.islem_baslat(self.giris(), "CEKME", "1000")
        with override_settings(LOG_HMAC_ANAHTARI="baska-anahtar"):
            self.assertFalse(zinciri_dogrula()["saglam"])

    def test_onemli_olaylar_loga_dusuyor(self):
        s = services.islem_baslat(self.giris(), "CEKME", "10000")
        self.onayla(s["talep_id"], AYSE)
        olaylar = set(LogKaydi.objects.values_list("olay_turu", flat=True))
        for beklenen in ["giris", "talep_acildi", "onay_verildi", "talep_onaylandi", "islem_uygulandi"]:
            self.assertIn(beklenen, olaylar)


@override_settings(MESAI_DISI_KURALI=False)
class ApiTestleri(TestCase):
    def setUp(self):
        call_command("seed_demo", stdout=io.StringIO())
        self.c = Client()
        self.baslik = {"X-Kopru-Anahtari": "gelistirme-kopru-anahtari"}

    def post(self, yol, veri, baslik=True):
        return self.c.post(
            yol, data=json.dumps(veri), content_type="application/json",
            headers=self.baslik if baslik else {},
        )

    def test_anahtarsiz_istek_reddedilir(self):
        self.assertEqual(self.post("/api/kart/", {"uid": "a0a0a001"}, baslik=False).status_code, 401)
        self.assertEqual(self.c.get("/api/kart/", headers=self.baslik).status_code, 405)

    def test_tam_akis_http_uzerinden(self):
        self.assertEqual(self.post("/api/kart/", {"uid": "a0a0a001"}).json()["tip"], "MUSTERI")

        kotu = self.post("/api/giris/", {"uid": "a0a0a001", "pin": "9999"}).json()
        self.assertFalse(kotu["ok"])
        self.assertEqual(kotu["kalan_hak"], 2)

        g = self.post("/api/giris/", {"uid": "a0a0a001", "pin": "1234"}).json()
        self.assertTrue(g["ok"])
        token = g["token"]
        self.assertEqual(self.post("/api/bakiye/", {"token": token}).json()["bakiye"], "1000000.00")

        i = self.post("/api/islem/", {"token": token, "tur": "CEKME", "tutar": "20000"}).json()
        self.assertEqual(i["durum"], "onay_bekliyor")

        b = self.post("/api/bekleyen/", {}).json()
        self.assertEqual(b["talep"]["talep_id"], i["talep_id"])

        o = self.post("/api/onay/", {"talep_id": i["talep_id"], "uid": "b0b0b001",
                                      "pin": "1111", "karar": "ONAY"}).json()
        self.assertEqual(o["durum"], "UYGULANDI")
        t = self.post("/api/talep/", {"talep_id": i["talep_id"]}).json()
        self.assertEqual(t["durum"], "UYGULANDI")
        self.assertEqual(self.post("/api/bakiye/", {"token": token}).json()["bakiye"], "980000.00")
        self.assertTrue(self.post("/api/log/dogrula/", {}).json()["saglam"])

    def test_gecersiz_json(self):
        r = self.c.post("/api/kart/", data="{bozuk", content_type="application/json",
                        headers=self.baslik)
        self.assertEqual(r.status_code, 400)


class PanelTesti(TestCase):
    def test_panel_giris_ister(self):
        r = Client().get("/")
        self.assertEqual(r.status_code, 302)
