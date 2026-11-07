import datetime
import io
import json
import uuid
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from . import anomali, baglam, bildirim, ledger, politika_degisikligi, services
from .hashlog import kayit_ekle, zinciri_dogrula
from .models import (Bildirim, Hesap, HesapHareketi, Islem, Kart, LogKaydi, OnayTalebi, Personel, Politika,
                     PolitikaDegisikligi, PolitikaDegisiklikOyu, Rol, Terminal, TerminalGunlukKaydi)
from .politika_degisikligi import PolitikaHatasi
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


# ====================================================================== yeni ozellikler

class Yardimci:
    """seed_demo verisi ve kisa yollar."""

    def hazirla(self):
        call_command("seed_demo", stdout=io.StringIO())
        self.ali = Hesap.objects.get(hesap_no="100001")
        self.zeynep = Hesap.objects.get(hesap_no="100002")
        self.kasa = Hesap.objects.get(tur="KASA")

    def giris(self, uid="a0a0a001", pin="1234"):
        return services.musteri_giris(uid, pin)["token"]

    def yenile(self, hesap):
        hesap.refresh_from_db()
        return hesap.bakiye


@override_settings(MESAI_DISI_KURALI=False)
class DefterTestleri(Yardimci, TestCase):
    def setUp(self):
        self.hazirla()

    def test_cekme_cift_tarafli_hareket(self):
        s = services.islem_baslat(self.giris(), "CEKME", "1000")
        satirlar = {(h.hesap.hesap_no, h.yon): h.tutar for h in HesapHareketi.objects.filter(islem_id=s["islem_id"])}
        self.assertEqual(satirlar, {("100001", "BORC"): Decimal("1000.00"), ("900000", "ALACAK"): Decimal("1000.00")})
        self.assertEqual(self.yenile(self.ali), Decimal("999000.00"))
        self.assertEqual(self.yenile(self.kasa), Decimal("9999000.00"))
        self.assertTrue(ledger.mutabakat()["saglam"])

    def test_yatirma_hareketi(self):
        s = services.islem_baslat(self.giris(), "YATIRMA", "2500.50")
        satirlar = {(h.hesap.hesap_no, h.yon) for h in HesapHareketi.objects.filter(islem_id=s["islem_id"])}
        self.assertEqual(satirlar, {("900000", "BORC"), ("100001", "ALACAK")})
        self.assertEqual(self.yenile(self.kasa), Decimal("10002500.50"))
        self.assertTrue(ledger.mutabakat()["saglam"])

    def test_havale_hareketi(self):
        s = services.islem_baslat(self.giris(), "HAVALE", "3000", "100002")
        satirlar = {(h.hesap.hesap_no, h.yon) for h in HesapHareketi.objects.filter(islem_id=s["islem_id"])}
        self.assertEqual(satirlar, {("100001", "BORC"), ("100002", "ALACAK")})
        self.assertTrue(ledger.mutabakat()["saglam"])

    def test_onay_gelmeden_hareket_yok(self):
        s = services.islem_baslat(self.giris(), "CEKME", "10000")
        self.assertEqual(HesapHareketi.objects.count(), 0)
        services.onay_karar(s["talep_id"], "b0b0b001", "1111", "ONAY")
        self.assertEqual(HesapHareketi.objects.filter(islem_id=s["islem_id"]).count(), 2)
        self.assertTrue(ledger.mutabakat()["saglam"])

    def test_defter_bakiye_farkini_yakalar(self):
        services.islem_baslat(self.giris(), "CEKME", "1000")
        Hesap.objects.filter(hesap_no="100001").update(bakiye=Decimal("5.00"))
        r = ledger.mutabakat()
        self.assertFalse(r["saglam"])
        self.assertEqual([f["hesap_no"] for f in r["bakiye_farklari"]], ["100001"])

    def test_hareketsiz_islemi_yakalar(self):
        s = services.islem_baslat(self.giris(), "CEKME", "1000")
        HesapHareketi.objects.filter(islem_id=s["islem_id"]).delete()  # queryset silmesi modeli atlar
        r = ledger.mutabakat()
        self.assertFalse(r["saglam"])
        self.assertIn(s["islem_id"], r["hareketsiz_islemler"])

    def test_mutabakat_komutu(self):
        services.islem_baslat(self.giris(), "CEKME", "1000")
        cikti = io.StringIO()
        call_command("gun_sonu_mutabakat", stdout=cikti)
        self.assertIn("Mutabakat tamam", cikti.getvalue())
        self.assertTrue(LogKaydi.objects.filter(olay_turu="mutabakat").exists())
        self.assertTrue(zinciri_dogrula()["saglam"])
        Hesap.objects.filter(hesap_no="100002").update(bakiye=Decimal("1.00"))
        with self.assertRaises(SystemExit):
            call_command("gun_sonu_mutabakat", stdout=io.StringIO(), stderr=io.StringIO())

    def test_hareket_degistirilemez(self):
        services.islem_baslat(self.giris(), "CEKME", "1000")
        h = HesapHareketi.objects.first()
        with self.assertRaises(ValidationError):
            h.save()
        with self.assertRaises(ValidationError):
            h.delete()

    def test_kasada_nakit_yetersiz(self):
        Hesap.objects.filter(tur="KASA").update(bakiye=Decimal("500.00"))
        with self.assertRaises(IslemHatasi) as e:
            services.islem_baslat(self.giris(), "CEKME", "1000")
        self.assertEqual(e.exception.kod, "nakit")

    def test_havale_kasa_hesabina_olmaz(self):
        with self.assertRaises(IslemHatasi) as e:
            services.islem_baslat(self.giris(), "HAVALE", "10", "900000")
        self.assertEqual(e.exception.kod, "hedef")

    def test_gunluk_limit_uygulanmis_islemleri_sayar(self):
        Hesap.objects.filter(hesap_no="100001").update(gunluk_limit=Decimal("5000.00"))
        token = self.giris()
        services.islem_baslat(token, "CEKME", "3000")
        with self.assertRaises(IslemHatasi) as e:
            services.islem_baslat(token, "CEKME", "3000")
        self.assertEqual(e.exception.kod, "limit")
        self.assertEqual(e.exception.ekstra["kalan_limit"], "2000.00")
        services.islem_baslat(token, "CEKME", "2000")  # tam kalan kadar serbest

    def test_gunluk_limit_bekleyen_talepleri_de_sayar(self):
        Hesap.objects.filter(hesap_no="100001").update(gunluk_limit=Decimal("100000.00"))
        token = self.giris()
        services.islem_baslat(token, "CEKME", "60000")  # onay bekliyor
        with self.assertRaises(IslemHatasi) as e:
            services.islem_baslat(token, "CEKME", "50000")
        self.assertEqual(e.exception.kod, "limit")

    def test_yatirma_gunluk_limite_takilmaz(self):
        Hesap.objects.filter(hesap_no="100001").update(gunluk_limit=Decimal("100.00"))
        services.islem_baslat(self.giris(), "YATIRMA", "5000")


@override_settings(MESAI_DISI_KURALI=False)
class IdempotencyTestleri(Yardimci, TestCase):
    def setUp(self):
        self.hazirla()

    def test_ayni_anahtar_tek_islem(self):
        token = self.giris()
        r1 = services.islem_baslat(token, "CEKME", "1000", anahtar="abc123")
        r2 = services.islem_baslat(token, "CEKME", "1000", anahtar="abc123")
        self.assertFalse(r1["tekrar"])
        self.assertTrue(r2["tekrar"])
        self.assertEqual(r1["islem_id"], r2["islem_id"])
        self.assertEqual(Islem.objects.filter(anahtar="abc123").count(), 1)
        self.assertEqual(self.yenile(self.ali), Decimal("999000.00"))  # bir kez dustu
        self.assertEqual(HesapHareketi.objects.count(), 2)
        self.assertTrue(LogKaydi.objects.filter(olay_turu="tekrar_istek").exists())

    def test_ayni_anahtar_farkli_islemle_kullanilamaz(self):
        token = self.giris()
        services.islem_baslat(token, "CEKME", "1000", anahtar="abc123")
        with self.assertRaises(IslemHatasi) as e:
            services.islem_baslat(token, "CEKME", "2000", anahtar="abc123")
        self.assertEqual(e.exception.kod, "anahtar")
        self.assertEqual(self.yenile(self.ali), Decimal("999000.00"))

    def test_bekleyen_talep_tekrari_ayni_talebi_dondurur(self):
        token = self.giris()
        r1 = services.islem_baslat(token, "CEKME", "10000", anahtar="k-1")
        r2 = services.islem_baslat(token, "CEKME", "10000", anahtar="k-1")
        self.assertEqual(r1["talep_id"], r2["talep_id"])
        self.assertTrue(r2["tekrar"])
        self.assertEqual(OnayTalebi.objects.count(), 1)

    def test_onaylanmis_islemin_tekrari_sonucu_dondurur(self):
        token = self.giris()
        r1 = services.islem_baslat(token, "CEKME", "10000", anahtar="k-2")
        services.onay_karar(r1["talep_id"], "b0b0b001", "1111", "ONAY")
        r2 = services.islem_baslat(token, "CEKME", "10000", anahtar="k-2")
        self.assertEqual(r2["durum"], "uygulandi")
        self.assertTrue(r2["basarili"])
        self.assertEqual(self.yenile(self.ali), Decimal("990000.00"))

    def test_gecersiz_anahtar(self):
        with self.assertRaises(IslemHatasi):
            services.islem_baslat(self.giris(), "CEKME", "10", anahtar="bozuk anahtar!")

    def test_farkli_hesaplar_ayni_anahtari_kullanabilir(self):
        services.islem_baslat(self.giris(), "YATIRMA", "10", anahtar="ortak")
        services.islem_baslat(self.giris("a0a0a002", "4321"), "YATIRMA", "10", anahtar="ortak")
        self.assertEqual(Islem.objects.filter(anahtar="ortak").count(), 2)

    def test_anahtarsiz_islemler_tekrar_edilebilir(self):
        token = self.giris()
        services.islem_baslat(token, "CEKME", "100")
        services.islem_baslat(token, "CEKME", "100")
        self.assertEqual(self.yenile(self.ali), Decimal("999800.00"))


@override_settings(MESAI_DISI_KURALI=False)
class KasaAcmaTestleri(Yardimci, TestCase):
    AYSE = ("b0b0b001", "1111")
    BURAK = ("b0b0b002", "2222")
    CEM = ("b0b0b003", "3333")
    DENIZ = ("b0b0b004", "4444")
    ELIF = ("b0b0b005", "5555")

    def setUp(self):
        self.hazirla()

    def test_uc_onay_ve_mudur_sarti(self):
        t = services.kasa_acma_baslat(*self.AYSE)
        self.assertEqual(t["gereken"], 3)
        for kisi in (self.BURAK, self.CEM):
            services.onay_karar(t["talep_id"], kisi[0], kisi[1], "ONAY")
        sonuc = services.onay_karar(t["talep_id"], *self.DENIZ, "ONAY")  # 3 onay, mudur yok
        self.assertEqual(sonuc["durum"], "BEKLIYOR")
        self.assertTrue(sonuc["ust_yetkili_gerekli"])
        sonuc = services.onay_karar(t["talep_id"], *self.ELIF, "ONAY")
        self.assertEqual(sonuc["durum"], "UYGULANDI")
        self.assertEqual(sonuc["tur"], "KASA_ACMA")
        self.assertTrue(LogKaydi.objects.filter(olay_turu="kasa_acildi").exists())
        self.assertEqual(HesapHareketi.objects.count(), 0)  # para hareketi yok
        self.assertTrue(zinciri_dogrula()["saglam"])

    def test_baslatan_kendi_talebini_onaylayamaz(self):
        t = services.kasa_acma_baslat(*self.AYSE)
        with self.assertRaises(IslemHatasi) as e:
            services.onay_karar(t["talep_id"], *self.AYSE, "ONAY")
        self.assertEqual(e.exception.kod, "kendi_islemi")

    def test_baslatan_tek_mudurse_baslatamaz(self):
        with self.assertRaises(IslemHatasi) as e:
            services.kasa_acma_baslat(*self.ELIF)  # baska mudur yok: talep sonsuza kadar takilirdi
        self.assertEqual(e.exception.kod, "yetkili_yok")

    def test_musteri_baslatamaz(self):
        with self.assertRaises(IslemHatasi) as e:
            services.kasa_acma_baslat("a0a0a001", "1234")
        self.assertEqual(e.exception.kod, "yetkisiz")

    def test_musteri_islem_turu_olarak_kasa_acma_gonderemez(self):
        with self.assertRaises(IslemHatasi) as e:
            services.islem_baslat(self.giris(), "KASA_ACMA", "1")
        self.assertEqual(e.exception.kod, "tur")

    def test_sure_dolarsa_kasa_acilmaz(self):
        t = services.kasa_acma_baslat(*self.AYSE)
        OnayTalebi.objects.filter(id=t["talep_id"]).update(
            son_tarih=timezone.now() - datetime.timedelta(seconds=1))
        sonuc = services.onay_karar(t["talep_id"], *self.BURAK, "ONAY")
        self.assertEqual(sonuc["durum"], "SURESI_DOLDU")
        self.assertFalse(LogKaydi.objects.filter(olay_turu="kasa_acildi").exists())


@override_settings(MESAI_DISI_KURALI=False)
class AnomaliTestleri(Yardimci, TestCase):
    def setUp(self):
        self.hazirla()

    def kurallar(self, **kw):
        return {b["kural"] for b in anomali.tara(**kw)}

    def yanlis_pin(self, uid, n):
        for _ in range(n):
            with self.assertRaises(IslemHatasi):
                services.musteri_giris(uid, "0000")

    def test_normal_kullanimda_bulgu_yok(self):
        token = self.giris()
        services.islem_baslat(token, "CEKME", "1000")
        services.islem_baslat(token, "YATIRMA", "500")
        self.assertEqual(anomali.tara(), [])

    def test_pin_yagmuru(self):
        self.yanlis_pin("a0a0a001", 3)
        self.assertIn("pin_yagmuru", self.kurallar())

    def test_iki_yanlis_pin_alarm_vermez(self):
        self.yanlis_pin("a0a0a001", 2)
        self.assertNotIn("pin_yagmuru", self.kurallar())

    def test_dagitik_pin(self):
        self.yanlis_pin("a0a0a001", 2)
        self.yanlis_pin("a0a0a002", 2)
        with self.assertRaises(IslemHatasi):
            services.personel_dogrula("b0b0b001", "0000")
        self.assertIn("dagitik_pin", self.kurallar())

    def test_bilinmeyen_kart_tekrar(self):
        for _ in range(3):
            services.kart_tipi("ffffffff")
        self.assertIn("bilinmeyen_kart_tekrar", self.kurallar())

    def test_bloke_kart_israri(self):
        self.yanlis_pin("a0a0a001", 3)  # bloke oldu
        for _ in range(2):
            with self.assertRaises(IslemHatasi):
                services.musteri_giris("a0a0a001", "1234")
        self.assertIn("bloke_kart_denemesi", self.kurallar())

    def test_hizli_talep(self):
        token = self.giris()
        for _ in range(3):
            services.islem_baslat(token, "CEKME", "10000")
        self.assertIn("hizli_talep", self.kurallar())

    def test_gece_yetkili_islemi(self):
        istanbul = ZoneInfo("Europe/Istanbul")
        gece = datetime.datetime(2026, 11, 4, 3, 0, tzinfo=istanbul)
        gunduz = datetime.datetime(2026, 11, 4, 12, 0, tzinfo=istanbul)
        k = kayit_ekle("onay_verildi", {"talep_id": 1, "personel_id": 1})
        LogKaydi.objects.filter(id=k.id).update(zaman=gece)
        self.assertIn("gece_yetkili_islemi", self.kurallar(saat=24, an=gunduz))
        LogKaydi.objects.filter(id=k.id).update(zaman=gunduz - datetime.timedelta(minutes=5))
        self.assertNotIn("gece_yetkili_islemi", self.kurallar(saat=24, an=gunduz))

    def test_komut_yuksek_bulguda_hata_kodu_verir(self):
        cikti = io.StringIO()
        call_command("anomali_tara", stdout=cikti)
        self.assertIn("anomali bulunamadı", cikti.getvalue())
        self.yanlis_pin("a0a0a001", 3)
        with self.assertRaises(SystemExit):
            call_command("anomali_tara", stdout=io.StringIO())


@override_settings(MESAI_DISI_KURALI=False)
class YeniApiTestleri(Yardimci, TestCase):
    def setUp(self):
        self.hazirla()
        self.c = Client()
        self.baslik = {"X-Kopru-Anahtari": "gelistirme-kopru-anahtari"}

    def post(self, yol, veri, **ek):
        return self.c.post(yol, data=json.dumps(veri), content_type="application/json",
                           headers=self.baslik, **ek)

    def test_idempotency_http_uzerinden(self):
        token = self.post("/api/giris/", {"uid": "a0a0a001", "pin": "1234"}).json()["token"]
        veri = {"token": token, "tur": "CEKME", "tutar": "1000", "anahtar": "http-1"}
        r1 = self.post("/api/islem/", veri).json()
        r2 = self.post("/api/islem/", veri).json()
        self.assertEqual(r1["islem_id"], r2["islem_id"])
        self.assertTrue(r2["tekrar"])
        self.assertEqual(Islem.objects.filter(anahtar="http-1").count(), 1)

    def test_kasa_ac_ucu(self):
        r = self.post("/api/kasa-ac/", {"uid": "b0b0b001", "pin": "1111"}).json()
        self.assertTrue(r["ok"])
        self.assertEqual((r["durum"], r["gereken"]), ("onay_bekliyor", 3))
        t = self.post("/api/talep/", {"talep_id": r["talep_id"]}).json()
        self.assertEqual(t["tur"], "KASA_ACMA")
        kotu = self.post("/api/kasa-ac/", {"uid": "a0a0a001", "pin": "1234"})
        self.assertEqual(kotu.status_code, 400)
        self.assertEqual(kotu.json()["kod"], "yetkisiz")

    def test_https_zorunlu_ise_duz_http_reddedilir(self):
        with override_settings(API_SADECE_HTTPS=True):
            r = self.post("/api/kart/", {"uid": "a0a0a001"})
            self.assertEqual(r.status_code, 403)
            self.assertEqual(r.json()["kod"], "https")
            ok = self.post("/api/kart/", {"uid": "a0a0a001"}, secure=True)
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.json()["tip"], "MUSTERI")


# ====================================================================== terminal, kurus, cevrimdisi gunluk, bildirim

class ApiYardimci(Yardimci):
    def api_hazirla(self):
        self.hazirla()
        self.c = Client()
        self.baslik = {"X-Kopru-Anahtari": "gelistirme-kopru-anahtari"}

    def api(self, yol, veri, terminal=None, anahtar=True):
        baslik = dict(self.baslik) if anahtar else {}
        if terminal:
            baslik["X-Terminal-Id"] = terminal
        return self.c.post(yol, data=json.dumps(veri), content_type="application/json", headers=baslik)


@override_settings(MESAI_DISI_KURALI=False)
class TerminalTestleri(ApiYardimci, TestCase):
    def setUp(self):
        self.api_hazirla()

    def kurallar(self):
        return {b["kural"] for b in anomali.tara()}

    def test_bilinmeyen_terminal_reddedilir(self):
        r = self.api("/api/kart/", {"uid": "a0a0a001"}, terminal="ATM-99")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["kod"], "terminal")

    def test_kapali_terminal_reddedilir(self):
        Terminal.objects.filter(kod="ATM-02").update(aktif=False)
        self.assertEqual(self.api("/api/kart/", {"uid": "a0a0a001"}, terminal="ATM-02").status_code, 403)

    def test_baslik_yoksa_varsayilan_terminal(self):
        self.assertEqual(self.api("/api/kart/", {"uid": "a0a0a001"}).status_code, 200)

    def test_terminal_log_ve_islemde_kayitli(self):
        g = self.api("/api/giris/", {"uid": "a0a0a001", "pin": "1234"}, terminal="ATM-02").json()
        giris = LogKaydi.objects.filter(olay_turu="giris").first()
        self.assertEqual(json.loads(giris.veri)["terminal"], "ATM-02")
        r = self.api("/api/islem/", {"token": g["token"], "tur": "YATIRMA", "tutar": "10"}, terminal="ATM-02").json()
        self.assertEqual(Islem.objects.get(id=r["islem_id"]).terminal, "ATM-02")
        self.assertTrue(zinciri_dogrula()["saglam"])  # terminal alani da hash'e girdi

    def test_imkansiz_seyahat(self):
        with baglam.terminal_baglami("ATM-01"):
            services.musteri_giris("a0a0a001", "1234")
        with baglam.terminal_baglami("ATM-02"):  # Ankara -> Istanbul, birkac saniye sonra
            services.musteri_giris("a0a0a001", "1234")
        self.assertIn("imkansiz_seyahat", self.kurallar())

    def test_ayni_terminalde_tekrar_giris_sorun_degil(self):
        for _ in range(2):
            with baglam.terminal_baglami("ATM-01"):
                services.musteri_giris("a0a0a001", "1234")
        self.assertFalse(self.kurallar() & {"imkansiz_seyahat", "farkli_terminal_kisa_surede"})

    def test_yakin_terminaller_alarm_vermez(self):
        Terminal.objects.filter(kod="ATM-02").update(enlem=39.9340, boylam=32.8600)  # ayni sube gibi
        with baglam.terminal_baglami("ATM-01"):
            services.musteri_giris("a0a0a001", "1234")
        with baglam.terminal_baglami("ATM-02"):
            services.musteri_giris("a0a0a001", "1234")
        self.assertFalse(self.kurallar() & {"imkansiz_seyahat", "farkli_terminal_kisa_surede"})

    def test_konum_bilinmiyorsa_kisa_surede_farkli_terminal(self):
        Terminal.objects.filter(kod="ATM-02").update(enlem=None, boylam=None)
        with baglam.terminal_baglami("ATM-01"):
            services.musteri_giris("a0a0a001", "1234")
        with baglam.terminal_baglami("ATM-02"):
            services.musteri_giris("a0a0a001", "1234")
        kurallar = self.kurallar()
        self.assertIn("farkli_terminal_kisa_surede", kurallar)
        self.assertNotIn("imkansiz_seyahat", kurallar)

    def test_farkli_kimlikler_farkli_terminallerde_sorun_degil(self):
        with baglam.terminal_baglami("ATM-01"):
            services.musteri_giris("a0a0a001", "1234")
        with baglam.terminal_baglami("ATM-02"):
            services.musteri_giris("a0a0a002", "4321")
        self.assertNotIn("imkansiz_seyahat", self.kurallar())

    def test_yetkili_icin_imkansiz_seyahat(self):
        token = self.giris()
        t1 = services.islem_baslat(token, "CEKME", "10000")["talep_id"]
        t2 = services.islem_baslat(token, "CEKME", "10000")["talep_id"]
        with baglam.terminal_baglami("ATM-01"):
            services.onay_karar(t1, "b0b0b001", "1111", "ONAY")
        with baglam.terminal_baglami("ATM-02"):
            services.onay_karar(t2, "b0b0b001", "1111", "ONAY")
        self.assertIn("imkansiz_seyahat", self.kurallar())


@override_settings(MESAI_DISI_KURALI=False)
class KurusTestleri(Yardimci, TestCase):
    def setUp(self):
        self.hazirla()

    def test_kurus_tutari(self):
        services.islem_baslat(self.giris(), "CEKME", "100.50")
        self.assertEqual(self.yenile(self.ali), Decimal("999899.50"))
        self.assertEqual(self.yenile(self.kasa), Decimal("9999899.50"))

    def test_virgul_ondalik_ayraci(self):
        services.islem_baslat(self.giris(), "YATIRMA", "100,5")
        self.assertEqual(self.yenile(self.ali), Decimal("1000100.50"))

    def test_tek_hane_kurus(self):
        services.islem_baslat(self.giris(), "CEKME", "5.5")
        self.assertEqual(self.yenile(self.ali), Decimal("999994.50"))

    def test_uc_ondalik_hane_reddedilir(self):
        with self.assertRaises(IslemHatasi) as e:
            services.islem_baslat(self.giris(), "CEKME", "10.123")
        self.assertEqual(e.exception.kod, "tutar")
        self.assertEqual(self.yenile(self.ali), Decimal("1000000.00"))

    def test_belirsiz_binlik_ayraci_reddedilir(self):
        with self.assertRaises(IslemHatasi):
            services.islem_baslat(self.giris(), "CEKME", "1,000.50")

    def test_kurusla_defter_dengeli_kalir(self):
        token = self.giris()
        services.islem_baslat(token, "YATIRMA", "0.01")
        services.islem_baslat(token, "CEKME", "0.01")
        services.islem_baslat(token, "HAVALE", "12.34", "100002")
        self.assertTrue(ledger.mutabakat()["saglam"])
        self.assertEqual(self.yenile(self.zeynep), Decimal("250012.34"))


@override_settings(MESAI_DISI_KURALI=False)
class CevrimdisiGunlukTestleri(ApiYardimci, TestCase):
    def setUp(self):
        self.api_hazirla()

    def girdi(self, olay="cevrimdisi_istek", veri=None, zaman=None, kimlik=None):
        return {
            "id": kimlik or uuid.uuid4().hex,
            "zaman": (zaman or datetime.datetime.now(datetime.timezone.utc)).isoformat(),
            "olay": olay,
            "veri": veri if veri is not None else {"komut": "KART", "uid": "a0a0a001"},
        }

    def yukle(self, girdiler, terminal="ATM-02"):
        return self.api("/api/terminal-gunlugu/", {"girdiler": girdiler}, terminal=terminal)

    def test_kabul_edilir_ve_zincire_girer(self):
        r = self.yukle([self.girdi(), self.girdi("cevrimdisi_basladi", {})]).json()
        self.assertEqual((r["kabul"], r["tekrar"], r["red"]), (2, 0, 0))
        self.assertEqual(TerminalGunlukKaydi.objects.filter(terminal="ATM-02").count(), 2)
        log = LogKaydi.objects.filter(olay_turu="terminal_gunlugu").first()
        veri = json.loads(log.veri)
        self.assertEqual(veri["terminal"], "ATM-02")
        self.assertIn("terminal_zamani", veri)
        self.assertTrue(zinciri_dogrula()["saglam"])

    def test_ayni_kayit_ikinci_kez_eklenmez(self):
        g = [self.girdi()]
        self.assertEqual(self.yukle(g).json()["kabul"], 1)
        r = self.yukle(g).json()
        self.assertEqual((r["kabul"], r["tekrar"]), (0, 1))
        self.assertEqual(TerminalGunlukKaydi.objects.count(), 1)
        self.assertEqual(LogKaydi.objects.filter(olay_turu="terminal_gunlugu").count(), 1)

    def test_gecersiz_girdiler_reddedilir(self):
        kotu = [
            self.girdi(kimlik="kisa"),
            self.girdi(olay="para_cek"),
            {**self.girdi(), "zaman": "bozuk"},
            {**self.girdi(), "veri": "metin"},
            "bir sey",
        ]
        r = self.yukle(kotu).json()
        self.assertEqual((r["kabul"], r["red"]), (0, 5))
        self.assertEqual(TerminalGunlukKaydi.objects.count(), 0)

    def test_eski_ve_gelecek_zaman_guvenilir_degil(self):
        simdi = datetime.datetime.now(datetime.timezone.utc)
        self.yukle([self.girdi(zaman=simdi - datetime.timedelta(days=30)),
                    self.girdi(zaman=simdi + datetime.timedelta(days=1)),
                    self.girdi(zaman=simdi)])
        self.assertEqual(TerminalGunlukKaydi.objects.filter(guvenilir_zaman=False).count(), 2)
        self.assertEqual(TerminalGunlukKaydi.objects.filter(guvenilir_zaman=True).count(), 1)
        guvenilmez = [json.loads(l.veri)["zaman_guvenilir"] for l in LogKaydi.objects.filter(olay_turu="terminal_gunlugu")]
        self.assertEqual(sorted(guvenilmez), [False, False, True])

    def test_cok_fazla_girdi_reddedilir(self):
        r = self.yukle([self.girdi() for _ in range(201)])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["kod"], "gunluk")

    def test_girdiler_liste_degilse_reddedilir(self):
        r = self.api("/api/terminal-gunlugu/", {"girdiler": "x"}, terminal="ATM-02")
        self.assertEqual(r.status_code, 400)

    def test_anahtar_ister(self):
        r = self.api("/api/terminal-gunlugu/", {"girdiler": []}, terminal="ATM-02", anahtar=False)
        self.assertEqual(r.status_code, 401)

    def test_uzun_cevrimdisi_kurali(self):
        self.yukle([self.girdi("cevrimdisi_bitti", {"sure_sn": 60})])
        self.assertNotIn("uzun_cevrimdisi", {b["kural"] for b in anomali.tara()})
        self.yukle([self.girdi("cevrimdisi_bitti", {"sure_sn": 3600})])
        self.assertIn("uzun_cevrimdisi", {b["kural"] for b in anomali.tara()})

    def test_gunluk_para_islemi_yetkilendirmez(self):
        self.yukle([self.girdi("cevrimdisi_istek", {"komut": "ISLEM"})])
        self.assertEqual(Islem.objects.count(), 0)
        self.assertEqual(HesapHareketi.objects.count(), 0)


@override_settings(MESAI_DISI_KURALI=False)
class BildirimTestleri(Yardimci, TestCase):
    def setUp(self):
        self.hazirla()

    def konulu(self, konu):
        return [m for m in mail.outbox if m.subject == konu]

    def test_talep_acilinca_tum_yetkililere_bildirilir(self):
        services.islem_baslat(self.giris(), "CEKME", "10000")
        self.assertEqual(len(mail.outbox), 5)
        self.assertTrue(mail.outbox[0].subject.startswith("Onay bekleyen işlem"))
        self.assertIn("10.000,00 TL", mail.outbox[0].body)
        self.assertIn("100001", mail.outbox[0].body)
        self.assertEqual(Bildirim.objects.filter(kanal="EPOSTA", durum="GONDERILDI").count(), 5)
        self.assertEqual(Bildirim.objects.filter(kanal="SMS", durum="SIMULASYON").count(), 5)

    def test_tutar_bicimi(self):
        self.assertEqual(bildirim.tl(Decimal("60000")), "60.000,00 TL")
        self.assertEqual(bildirim.tl("100.5"), "100,50 TL")
        self.assertEqual(bildirim.tl("1234567.89"), "1.234.567,89 TL")

    def test_onaysiz_islemde_bildirim_yok(self):
        services.islem_baslat(self.giris(), "CEKME", "1000")
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(Bildirim.objects.count(), 0)

    def test_kasa_acma_talebinde_baslatan_haric(self):
        services.kasa_acma_baslat("b0b0b001", "1111")
        self.assertEqual(len(mail.outbox), 4)
        self.assertNotIn("ayse@banka.example", [a for m in mail.outbox for a in m.to])
        self.assertTrue(mail.outbox[0].subject.startswith("Kasa dairesi açma talebi"))

    def test_kasa_acilinca_mudur_yrd_ve_ustune_bilgi(self):
        t = services.kasa_acma_baslat("b0b0b001", "1111")
        for uid, pin in (("b0b0b002", "2222"), ("b0b0b003", "3333"), ("b0b0b004", "4444"), ("b0b0b005", "5555")):
            services.onay_karar(t["talep_id"], uid, pin, "ONAY")
        acilis = self.konulu("Kasa dairesi açıldı")
        self.assertEqual(sorted(a for m in acilis for a in m.to),
                         ["cem@banka.example", "deniz@banka.example", "elif@banka.example"])

    def test_eposta_hatasi_islemi_bozmaz(self):
        with mock.patch("banka.bildirim.send_mail", side_effect=Exception("smtp yok")):
            s = services.islem_baslat(self.giris(), "CEKME", "10000")
        self.assertEqual(s["durum"], "onay_bekliyor")
        self.assertEqual(OnayTalebi.objects.filter(id=s["talep_id"], durum="BEKLIYOR").count(), 1)
        self.assertEqual(Bildirim.objects.filter(durum="BASARISIZ").count(), 5)
        self.assertEqual(Bildirim.objects.filter(kanal="SMS").count(), 5)
        self.assertIn("smtp yok", Bildirim.objects.filter(durum="BASARISIZ").first().hata)

    def test_sadece_telefonu_olan_yetkiliye_sms_gider(self):
        Personel.objects.filter(kart_uid="b0b0b002").update(eposta="")
        services.islem_baslat(self.giris(), "CEKME", "10000")
        self.assertEqual(len(mail.outbox), 4)
        self.assertEqual(Bildirim.objects.filter(kanal="SMS").count(), 5)

    def test_bloke_ve_pasif_yetkiliye_gitmez(self):
        Personel.objects.filter(kart_uid="b0b0b004").update(bloke=True)
        Personel.objects.filter(kart_uid="b0b0b005").update(aktif=False)
        services.islem_baslat(self.giris(), "CEKME", "10000")
        self.assertEqual(len(mail.outbox), 3)

    def yanlis_pin(self, uid, n):
        for _ in range(n):
            with self.assertRaises(IslemHatasi):
                services.musteri_giris(uid, "0000")

    def test_anomali_bildirimi_tekrarlanmaz(self):
        self.yanlis_pin("a0a0a001", 3)
        for _ in range(2):
            with self.assertRaises(SystemExit):  # yuksek bulgu: cikis kodu 1
                call_command("anomali_tara", "--bildir", stdout=io.StringIO())
        bildirimler = self.konulu("Anomali: pin_yagmuru")
        self.assertEqual(sorted(a for m in bildirimler for a in m.to),
                         ["cem@banka.example", "deniz@banka.example", "elif@banka.example"])
        self.assertEqual(LogKaydi.objects.filter(olay_turu="anomali_bildirimi").count(), 1)
        self.assertTrue(zinciri_dogrula()["saglam"])

    def test_dusuk_ve_orta_ciddiyet_bildirilmez(self):
        for _ in range(3):
            services.kart_tipi("ffffffff")  # bilinmeyen_kart_tekrar: orta
        bulgular = anomali.tara()
        self.assertIn("bilinmeyen_kart_tekrar", {b["kural"] for b in bulgular})
        self.assertEqual(bildirim.anomali_bildir(bulgular), 0)
        self.assertEqual(len(mail.outbox), 0)


# ====================================================================== ters kayit (teslim)

@override_settings(MESAI_DISI_KURALI=False)
class TersKayitTestleri(Yardimci, TestCase):
    def setUp(self):
        self.hazirla()

    def cek(self, tutar="1000", **ek):
        return services.islem_baslat(self.giris(), "CEKME", tutar, **ek)

    def test_nakit_cekme_teslim_bekler(self):
        r = self.cek()
        self.assertEqual(Islem.objects.get(id=r["islem_id"]).teslim, "BEKLIYOR")
        self.assertEqual(Islem.objects.filter(tur="YATIRMA").count(), 0)

    def test_alinmayan_para_ters_kaydedilir(self):
        r = self.cek()
        sonuc = services.teslim_bildir(r["islem_id"], "ALINMADI")
        self.assertEqual(sonuc["teslim"], "GERI")
        self.assertEqual(self.yenile(self.ali), Decimal("1000000.00"))
        self.assertEqual(self.yenile(self.kasa), Decimal("10000000.00"))
        orijinal = Islem.objects.get(id=r["islem_id"])
        self.assertEqual(orijinal.teslim, "GERI_ALINDI")
        ters = Islem.objects.get(ters_islem_id=r["islem_id"])
        self.assertEqual((ters.tur, ters.ters_neden, ters.durum), ("TERS_KAYIT", "MUSTERI_ALMADI", "UYGULANDI"))
        satirlar = {(h.hesap.hesap_no, h.yon) for h in HesapHareketi.objects.filter(islem=ters)}
        self.assertEqual(satirlar, {("100001", "ALACAK"), ("900000", "BORC")})
        # orijinal satirlara dokunulmadi, ters satirlar eklendi
        self.assertEqual(HesapHareketi.objects.filter(islem=orijinal).count(), 2)
        self.assertEqual(HesapHareketi.objects.count(), 4)
        self.assertTrue(ledger.mutabakat()["saglam"])
        self.assertTrue(zinciri_dogrula()["saglam"])

    def test_alinan_para_onaylanir_ve_celisen_bildirim_reddedilir(self):
        r = self.cek()
        self.assertEqual(services.teslim_bildir(r["islem_id"], "ALINDI")["teslim"], "ALINDI")
        with self.assertRaises(IslemHatasi) as e:
            services.teslim_bildir(r["islem_id"], "ALINMADI")
        self.assertEqual(e.exception.kod, "celiski")
        self.assertEqual(self.yenile(self.ali), Decimal("999000.00"))
        self.assertEqual(Islem.objects.filter(tur="TERS_KAYIT").count(), 0)

    def test_ayni_bildirim_tekrar_edilebilir(self):
        r = self.cek()
        for _ in range(2):
            self.assertEqual(services.teslim_bildir(r["islem_id"], "ALINMADI")["teslim"], "GERI")
        self.assertEqual(Islem.objects.filter(tur="TERS_KAYIT").count(), 1)
        self.assertEqual(self.yenile(self.ali), Decimal("1000000.00"))
        r2 = self.cek()
        for _ in range(2):
            self.assertEqual(services.teslim_bildir(r2["islem_id"], "ALINDI")["teslim"], "ALINDI")

    def test_dagitici_arizasi_ters_kaydeder(self):
        r = self.cek()
        services.teslim_bildir(r["islem_id"], "ARIZA")
        self.assertEqual(Islem.objects.get(ters_islem_id=r["islem_id"]).ters_neden, "PARA_VERILMEDI")

    def test_baska_terminal_teslim_bildiremez(self):
        with baglam.terminal_baglami("ATM-01"):
            r = self.cek()
        with baglam.terminal_baglami("ATM-02"):
            with self.assertRaises(IslemHatasi) as e:
                services.teslim_bildir(r["islem_id"], "ALINMADI")
        self.assertEqual(e.exception.kod, "terminal")
        self.assertTrue(LogKaydi.objects.filter(olay_turu="teslim_baska_terminal").exists())
        self.assertEqual(Islem.objects.get(id=r["islem_id"]).teslim, "BEKLIYOR")

    def test_sure_asimindan_sonra_bildirim_reddedilir(self):
        r = self.cek()
        Islem.objects.filter(id=r["islem_id"]).update(tamamlanma=timezone.now() - datetime.timedelta(minutes=10))
        with self.assertRaises(IslemHatasi) as e:
            services.teslim_bildir(r["islem_id"], "ALINMADI")
        self.assertEqual(e.exception.kod, "sure")
        self.assertEqual(self.yenile(self.ali), Decimal("999000.00"))
        self.assertTrue(LogKaydi.objects.filter(olay_turu="teslim_sure_asimi").exists())

    def test_onayli_cekme_de_ters_kaydedilir(self):
        r = self.cek("10000")
        services.onay_karar(r["talep_id"], "b0b0b001", "1111", "ONAY")
        self.assertEqual(self.yenile(self.ali), Decimal("990000.00"))
        services.teslim_bildir(r["islem_id"], "ALINMADI")
        self.assertEqual(self.yenile(self.ali), Decimal("1000000.00"))
        self.assertTrue(ledger.mutabakat()["saglam"])

    def test_yatirma_ve_havale_ters_kaydedilemez(self):
        token = self.giris()
        for tur, hedef in (("YATIRMA", None), ("HAVALE", "100002")):
            r = services.islem_baslat(token, tur, "100", hedef)
            with self.assertRaises(IslemHatasi) as e:
                services.teslim_bildir(r["islem_id"], "ALINMADI")
            self.assertEqual(e.exception.kod, "yok")

    def test_gecersiz_durum_ve_islem(self):
        r = self.cek()
        with self.assertRaises(IslemHatasi) as e:
            services.teslim_bildir(r["islem_id"], "belki")
        self.assertEqual(e.exception.kod, "teslim")
        with self.assertRaises(IslemHatasi):
            services.teslim_bildir("abc", "ALINDI")
        with self.assertRaises(IslemHatasi):
            services.teslim_bildir(99999, "ALINDI")

    def test_ters_kayit_gunluk_limiti_serbest_birakir(self):
        Hesap.objects.filter(hesap_no="100001").update(gunluk_limit=Decimal("5000.00"))
        r = self.cek("4000")
        with self.assertRaises(IslemHatasi):
            self.cek("4000")
        services.teslim_bildir(r["islem_id"], "ALINMADI")
        self.cek("4000")  # limit serbest kaldi

    def test_ters_kaydedilen_islemin_tekrar_istegi_basari_donmez(self):
        r = self.cek(anahtar="t-1")
        services.teslim_bildir(r["islem_id"], "ALINMADI")
        with self.assertRaises(IslemHatasi) as e:
            self.cek(anahtar="t-1")
        self.assertEqual(e.exception.kod, "ters_kayit")

    def test_ayni_islem_veritabaninda_iki_kez_ters_kaydedilemez(self):
        r = self.cek()
        services.teslim_bildir(r["islem_id"], "ALINMADI")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Islem.objects.create(hesap=self.ali, tur="TERS_KAYIT", tutar=Decimal("1000.00"),
                                 baslatan_uid="x", ters_islem_id=r["islem_id"])

    def test_mutabakat_orijinaliyle_uyusmayan_ters_kaydi_yakalar(self):
        r = self.cek()
        services.teslim_bildir(r["islem_id"], "ALINMADI")
        Islem.objects.filter(id=r["islem_id"]).update(teslim="ALINDI")
        rapor = ledger.mutabakat()
        self.assertFalse(rapor["saglam"])
        self.assertEqual(len(rapor["tutarsiz_ters_kayitlar"]), 1)

    def test_mutabakat_tersi_olmayan_geri_alinani_yakalar(self):
        r = self.cek()
        Islem.objects.filter(id=r["islem_id"]).update(teslim="GERI_ALINDI")
        rapor = ledger.mutabakat()
        self.assertFalse(rapor["saglam"])
        self.assertEqual(rapor["tersi_olmayan_geri_alinanlar"], [r["islem_id"]])

    def test_belirsiz_teslim_uyarisi(self):
        r = self.cek()
        Islem.objects.filter(id=r["islem_id"]).update(tamamlanma=timezone.now() - datetime.timedelta(minutes=10))
        self.assertIn("teslim_belirsiz", {b["kural"] for b in anomali.tara()})
        rapor = ledger.mutabakat()
        self.assertEqual(rapor["belirsiz_teslimler"], [r["islem_id"]])
        self.assertTrue(rapor["saglam"])  # uyari, hata degil
        Islem.objects.filter(id=r["islem_id"]).update(teslim="ALINDI")
        self.assertNotIn("teslim_belirsiz", {b["kural"] for b in anomali.tara()})

    def test_taze_teslim_beklemesi_alarm_vermez(self):
        self.cek()
        self.assertNotIn("teslim_belirsiz", {b["kural"] for b in anomali.tara()})

    def test_cok_ters_kayit_kurali(self):
        for _ in range(3):
            services.teslim_bildir(self.cek()["islem_id"], "ALINMADI")
        self.assertIn("cok_ters_kayit", {b["kural"] for b in anomali.tara()})

    def test_ters_kayit_yoneticilere_bildirilir(self):
        r = self.cek()
        services.teslim_bildir(r["islem_id"], "ALINMADI")
        konu = f"Ters kayıt: işlem #{r['islem_id']}"
        alicilar = sorted(a for m in mail.outbox if m.subject == konu for a in m.to)
        self.assertEqual(alicilar, ["cem@banka.example", "deniz@banka.example", "elif@banka.example"])

    def test_mutabakat_komutu_ters_kayitla_gecer(self):
        services.teslim_bildir(self.cek()["islem_id"], "ALINMADI")
        cikti = io.StringIO()
        call_command("gun_sonu_mutabakat", stdout=cikti)
        self.assertIn("Mutabakat tamam", cikti.getvalue())
        self.assertIn("TERS_KAYIT", cikti.getvalue())


@override_settings(MESAI_DISI_KURALI=False)
class TeslimApiTestleri(ApiYardimci, TestCase):
    def setUp(self):
        self.api_hazirla()

    def cekim(self, terminal=None):
        token = self.api("/api/giris/", {"uid": "a0a0a001", "pin": "1234"}, terminal=terminal).json()["token"]
        return self.api("/api/islem/", {"token": token, "tur": "CEKME", "tutar": "1000"}, terminal=terminal).json()

    def test_teslim_ucu_ters_kaydeder(self):
        r = self.cekim()
        t = self.api("/api/teslim/", {"islem_id": r["islem_id"], "durum": "ALINMADI"}).json()
        self.assertEqual((t["ok"], t["teslim"]), (True, "GERI"))
        self.assertEqual(self.api("/api/teslim/", {"islem_id": r["islem_id"], "durum": "ALINMADI"}).json()["teslim"], "GERI")
        self.assertEqual(self.yenile(self.ali), Decimal("1000000.00"))

    def test_baska_terminalden_teslim_reddedilir(self):
        r = self.cekim()  # varsayilan terminal ATM-01
        t = self.api("/api/teslim/", {"islem_id": r["islem_id"], "durum": "ALINMADI"}, terminal="ATM-02")
        self.assertEqual(t.status_code, 400)
        self.assertEqual(t.json()["kod"], "terminal")

    def test_durum_cevabinda_islem_no_var(self):
        token = self.api("/api/giris/", {"uid": "a0a0a001", "pin": "1234"}).json()["token"]
        r = self.api("/api/islem/", {"token": token, "tur": "CEKME", "tutar": "10000"}).json()
        d = self.api("/api/talep/", {"talep_id": r["talep_id"]}).json()
        self.assertEqual(d["islem_id"], r["islem_id"])


# ====================================================================== maker-checker (politika degisikligi)

@override_settings(MESAI_DISI_KURALI=False)
class MakerCheckerTestleri(Yardimci, TestCase):
    def setUp(self):
        self.hazirla()
        self.U = get_user_model()
        self.maker = self.U.objects.get(username="politika_talep")
        self.c1 = self.U.objects.get(username="politika_onay1")
        self.c2 = self.U.objects.get(username="politika_onay2")
        self.p3 = Politika.objects.get(alt_limit=Decimal("50000"))  # 50.000-500.000: 2 onay, mudur yrd.

    GEREKCE = "Dolandırıcılık riski nedeniyle onay sayısı artırıldı"

    def talep(self, onay=3, kullanici=None):
        return politika_degisikligi.talep_olustur(
            kullanici or self.maker, "GUNCELLE", self.p3.id,
            {"alt": "50000", "onay": onay, "rol": 2, "aciklama": "daha sıkı"}, self.GEREKCE)

    def r(self, alt, ust, onay, rol):
        return {"alt": alt, "ust": ust, "onay": onay, "rol": rol}

    def test_baslangic_goruntusu_tutarli(self):
        self.assertEqual(politika_degisikligi.butunluk_durumu(), "tutarli")

    def test_iki_onayla_uygulanir(self):
        t = self.talep()
        self.assertEqual(t.durum, "BEKLIYOR")
        politika_degisikligi.oy_ver(t.id, self.c1, "ONAY")
        self.assertEqual(services.gerekli_onay(Decimal("60000")), (2, Rol.MUDUR_YRD))  # henuz degismedi
        t = politika_degisikligi.oy_ver(t.id, self.c2, "ONAY")
        self.assertEqual(t.durum, "UYGULANDI")
        self.assertEqual(Politika.objects.get(id=self.p3.id).gereken_onay, 3)
        self.assertEqual(services.gerekli_onay(Decimal("60000")), (3, Rol.MUDUR_YRD))
        self.assertEqual(politika_degisikligi.butunluk_durumu(), "tutarli")
        olaylar = list(LogKaydi.objects.values_list("olay_turu", flat=True))
        for beklenen in ("politika_degisikligi_talebi", "politika_degisikligi_onayi", "politika_degisikligi_uygulandi"):
            self.assertIn(beklenen, olaylar)
        self.assertEqual(olaylar.count("politika_goruntusu"), 2)  # seed + bu degisiklik
        self.assertTrue(zinciri_dogrula()["saglam"])

    def test_talep_acilinca_onaylayicilara_eposta(self):
        self.talep()
        alicilar = sorted(a for m in mail.outbox if m.subject.startswith("Politika değişikliği talebi") for a in m.to)
        self.assertEqual(alicilar, ["politika_onay1@banka.example", "politika_onay2@banka.example"])

    def test_talep_eden_kendi_talebini_onaylayamaz(self):
        root = self.U.objects.create_superuser("root", "root@banka.example", "x")  # hem maker hem checker yetkisi
        t = self.talep(kullanici=root)
        with self.assertRaises(PolitikaHatasi) as e:
            politika_degisikligi.oy_ver(t.id, root, "ONAY")
        self.assertEqual(e.exception.kod, "kendi_talebi")
        self.assertTrue(LogKaydi.objects.filter(olay_turu="politika_onay_reddi_kendi_talebi").exists())
        self.assertEqual(PolitikaDegisikligi.objects.get(id=t.id).oylar.count(), 0)

    def test_ayni_onaylayan_iki_kez_sayilmaz(self):
        t = self.talep()
        politika_degisikligi.oy_ver(t.id, self.c1, "ONAY")
        with self.assertRaises(PolitikaHatasi) as e:
            politika_degisikligi.oy_ver(t.id, self.c1, "ONAY")
        self.assertEqual(e.exception.kod, "tekrar")
        t.refresh_from_db()
        self.assertEqual((t.durum, t.oylar.count()), ("BEKLIYOR", 1))

    def test_yetkisiz_kullanici(self):
        t = self.talep()
        dis = self.U.objects.create_user("dis", is_staff=True)
        for kullanici in (self.maker, dis):  # talep eden grubunda onay yetkisi yok
            with self.assertRaises(PolitikaHatasi) as e:
                politika_degisikligi.oy_ver(t.id, kullanici, "ONAY")
            self.assertEqual(e.exception.kod, "yetki")
        with self.assertRaises(PolitikaHatasi) as e:
            self.talep(kullanici=dis)
        self.assertEqual(e.exception.kod, "yetki")
        with self.assertRaises(PolitikaHatasi) as e:
            politika_degisikligi.oy_ver(99999, self.c1, "ONAY")
        self.assertEqual(e.exception.kod, "yok")

    def test_red_talebi_kapatir(self):
        t = self.talep()
        self.assertEqual(politika_degisikligi.oy_ver(t.id, self.c1, "RED").durum, "REDDEDILDI")
        self.assertEqual(politika_degisikligi.oy_ver(t.id, self.c2, "ONAY").durum, "REDDEDILDI")  # kapali talep
        self.assertEqual(Politika.objects.get(id=self.p3.id).gereken_onay, 2)

    def test_sure_dolumu(self):
        t = self.talep()
        PolitikaDegisikligi.objects.filter(id=t.id).update(son_tarih=timezone.now() - datetime.timedelta(seconds=1))
        self.assertEqual(politika_degisikligi.oy_ver(t.id, self.c1, "ONAY").durum, "SURESI_DOLDU")
        self.assertEqual(Politika.objects.get(id=self.p3.id).gereken_onay, 2)

    def test_iptal_yalniz_talep_sahibine_aittir(self):
        t = self.talep()
        with self.assertRaises(PolitikaHatasi) as e:
            politika_degisikligi.iptal(t.id, self.c1)
        self.assertEqual(e.exception.kod, "yetki")
        self.assertEqual(politika_degisikligi.iptal(t.id, self.maker).durum, "IPTAL")
        with self.assertRaises(PolitikaHatasi):
            politika_degisikligi.iptal(t.id, self.maker)  # artik beklemiyor

    def test_ayni_hedef_icin_cakisan_talep(self):
        self.talep()
        with self.assertRaises(PolitikaHatasi) as e:
            self.talep(onay=2)
        self.assertEqual(e.exception.kod, "cakisan")

    def test_gerekce_zorunlu(self):
        with self.assertRaises(PolitikaHatasi) as e:
            politika_degisikligi.talep_olustur(
                self.maker, "GUNCELLE", self.p3.id, {"alt": "50000", "onay": 3, "rol": 2}, "kısa")
        self.assertEqual(e.exception.kod, "gerekce")

    def test_onaysiz_aralik_korkulugu(self):
        with self.assertRaises(PolitikaHatasi) as e:
            self.talep(onay=0)  # 50.000-500.000 araligi onaysiz olamaz
        self.assertEqual(e.exception.kod, "gecersiz_politika")
        self.assertEqual(PolitikaDegisikligi.objects.count(), 0)

    def test_kume_dogrula_kurallari(self):
        r = self.r
        politika_degisikligi.kume_dogrula([r("0.00", "5000.00", 0, 1), r("5000.00", None, 3, 3)])  # gecerli
        politika_degisikligi.kume_dogrula(politika_degisikligi.mevcut_kume())
        kotular = [
            ("bitişik", [r("0.00", "5000.00", 0, 1), r("6000.00", None, 3, 3)]),
            ("bitişik", [r("0.00", "5000.00", 0, 1), r("4000.00", None, 3, 3)]),
            ("Son aralığın", [r("0.00", "5000.00", 0, 1), r("5000.00", "9000.00", 3, 3)]),
            ("azalamaz", [r("0.00", "5000.00", 0, 1), r("5000.00", "50000.00", 3, 3), r("50000.00", None, 2, 3)]),
            ("onaysız aralık", [r("0.00", None, 0, 1)]),
            ("0'dan", [r("1000.00", None, 3, 3)]),
            ("en az 2 onay", [r("0.00", "5000.00", 0, 1), r("5000.00", None, 1, 1)]),
            ("Aynı alt limit", [r("0.00", "5000.00", 0, 1), r("5000.00", "5000.00", 1, 1), r("5000.00", None, 3, 3)]),
        ]
        for beklenen, kume in kotular:
            with self.assertRaises(PolitikaHatasi, msg=beklenen) as e:
                politika_degisikligi.kume_dogrula(kume)
            self.assertIn(beklenen, e.exception.mesaj)

    def test_yeni_esik_eklemek_komsu_araligi_baglar(self):
        t = politika_degisikligi.talep_olustur(
            self.maker, "EKLE", None, {"alt": "20000", "onay": 1, "rol": 1, "aciklama": "orta kademe"}, self.GEREKCE)
        politika_degisikligi.oy_ver(t.id, self.c1, "ONAY")
        self.assertEqual(politika_degisikligi.oy_ver(t.id, self.c2, "ONAY").durum, "UYGULANDI")
        self.assertEqual(Politika.objects.count(), 5)
        self.assertEqual(Politika.objects.get(alt_limit=Decimal("5000")).ust_limit, Decimal("20000.00"))
        self.assertEqual(Politika.objects.get(alt_limit=Decimal("20000")).ust_limit, Decimal("50000.00"))
        self.assertEqual(services.gerekli_onay(Decimal("30000")), (1, Rol.GOREVLI))
        self.assertEqual(politika_degisikligi.butunluk_durumu(), "tutarli")

    def test_aralik_silmek_oncekini_uzatir(self):
        t = politika_degisikligi.talep_olustur(self.maker, "SIL", self.p3.id, gerekce=self.GEREKCE)
        politika_degisikligi.oy_ver(t.id, self.c1, "ONAY")
        politika_degisikligi.oy_ver(t.id, self.c2, "ONAY")
        self.assertEqual(Politika.objects.count(), 3)
        self.assertEqual(Politika.objects.get(alt_limit=Decimal("5000")).ust_limit, Decimal("500000.00"))
        self.assertEqual(services.gerekli_onay(Decimal("60000")), (1, Rol.GOREVLI))

    def test_silme_korkuluga_takilir(self):
        ikinci = Politika.objects.get(alt_limit=Decimal("5000"))
        for hedef in (ikinci.id, Politika.objects.get(alt_limit=Decimal("0")).id):
            with self.assertRaises(PolitikaHatasi) as e:
                politika_degisikligi.talep_olustur(self.maker, "SIL", hedef, gerekce=self.GEREKCE)
            self.assertEqual(e.exception.kod, "gecersiz_politika")

    def test_onaysiz_veritabani_degisikligi_sistemi_kapatir(self):
        token = self.giris()
        Politika.objects.filter(alt_limit=Decimal("5000")).update(gereken_onay=0)  # dogrudan mudahale
        self.assertEqual(politika_degisikligi.butunluk_durumu(), "tutarsiz")
        self.assertEqual(services.gerekli_onay(Decimal("100")), (3, Rol.MUDUR))
        self.assertEqual(services.islem_baslat(token, "CEKME", "100")["durum"], "onay_bekliyor")
        self.assertEqual(LogKaydi.objects.filter(olay_turu="politika_butunluk_ihlali").count(), 1)  # saatte bir
        self.assertFalse(ledger.mutabakat()["saglam"])
        self.assertTrue(zinciri_dogrula()["saglam"])  # log zinciri bozulmadi: mudahale gorunur kaldi

    def test_tablo_tutarsizken_degisiklik_uygulanmaz(self):
        t = self.talep()
        Politika.objects.filter(alt_limit=Decimal("5000")).update(gereken_onay=2)  # gecerli ama onaysiz degisiklik
        politika_degisikligi.oy_ver(t.id, self.c1, "ONAY")
        sonuc = politika_degisikligi.oy_ver(t.id, self.c2, "ONAY")
        self.assertEqual(sonuc.durum, "BASARISIZ")
        self.assertEqual(Politika.objects.get(id=self.p3.id).gereken_onay, 2)
        self.assertTrue(LogKaydi.objects.filter(olay_turu="politika_degisikligi_uygulanamadi").exists())

    def test_onayli_goruntu_yoksa_zorlama_yapilmaz(self):
        LogKaydi.objects.filter(olay_turu="politika_goruntusu").delete()
        self.assertEqual(politika_degisikligi.butunluk_durumu(), "baslangic_yok")
        self.assertEqual(services.gerekli_onay(Decimal("100")), (0, Rol.GOREVLI))
        self.assertEqual(ledger.mutabakat()["politika"], "baslangic_yok")

    def test_goruntu_al_komutu(self):
        cikti = io.StringIO()
        call_command("politika_goruntu_al", stdout=cikti)
        self.assertIn("zaten", cikti.getvalue())
        Politika.objects.filter(alt_limit=Decimal("5000")).update(gereken_onay=2)
        with self.assertRaises(SystemExit):  # onaysiz degisikligi sessizce mesrulastirmaz
            call_command("politika_goruntu_al", stdout=io.StringIO(), stderr=io.StringIO())
        call_command("politika_goruntu_al", "--zorla", stdout=io.StringIO())
        self.assertEqual(politika_degisikligi.butunluk_durumu(), "tutarli")
        self.assertTrue(LogKaydi.objects.filter(olay_turu="politika_goruntusu", veri__contains="--zorla").exists())


@override_settings(MESAI_DISI_KURALI=False)
class PolitikaAdminTestleri(Yardimci, TestCase):
    def setUp(self):
        self.hazirla()
        self.U = get_user_model()
        self.maker = self.U.objects.get(username="politika_talep")
        self.c1 = self.U.objects.get(username="politika_onay1")
        self.c2 = self.U.objects.get(username="politika_onay2")
        self.p3 = Politika.objects.get(alt_limit=Decimal("50000"))
        self.veri = {
            "islem": "GUNCELLE", "hedef_id": self.p3.id, "yeni_alt_limit": "50000", "yeni_gereken_onay": "3",
            "yeni_en_az_rol": "2", "yeni_aciklama": "daha sıkı",
            "gerekce": "Dolandırıcılık riski nedeniyle onay sayısı artırıldı", "_save": "Kaydet",
        }

    def test_politika_dogrudan_degistirilemez(self):
        root = self.U.objects.create_superuser("root", "root@banka.example", "x")
        self.client.force_login(root)
        self.assertEqual(self.client.get("/admin/banka/politika/").status_code, 200)
        self.assertEqual(self.client.get("/admin/banka/politika/add/").status_code, 403)
        self.assertEqual(self.client.post(f"/admin/banka/politika/{self.p3.id}/change/",
                                          {"alt_limit": "50000", "gereken_onay": "0"}).status_code, 403)
        self.assertEqual(self.client.post(f"/admin/banka/politika/{self.p3.id}/delete/", {"post": "yes"}).status_code, 403)
        self.assertEqual(Politika.objects.get(id=self.p3.id).gereken_onay, 2)

    def test_talep_formu(self):
        self.client.force_login(self.maker)
        self.assertEqual(self.client.get("/admin/banka/politikadegisikligi/add/").status_code, 200)
        r = self.client.post("/admin/banka/politikadegisikligi/add/", self.veri)
        self.assertEqual(r.status_code, 302)
        t = PolitikaDegisikligi.objects.get()
        self.assertEqual((t.talep_eden, t.durum, t.gereken_onay), (self.maker, "BEKLIYOR", 2))
        self.assertEqual(Politika.objects.get(id=self.p3.id).gereken_onay, 2)  # henuz uygulanmadi

    def test_formda_korkuluk_hatasi_gosterilir(self):
        self.client.force_login(self.maker)
        kotu = {**self.veri, "yeni_gereken_onay": "0"}
        r = self.client.post("/admin/banka/politikadegisikligi/add/", kotu)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "onaysız aralık")
        self.assertEqual(PolitikaDegisikligi.objects.count(), 0)

    def test_onay_admin_aksiyonuyla_verilir(self):
        t = politika_degisikligi.talep_olustur(
            self.maker, "GUNCELLE", self.p3.id,
            {"alt": "50000", "onay": 3, "rol": 2, "aciklama": "x"}, "Dolandırıcılık riski nedeniyle artırıldı")
        for kullanici in (self.c1, self.c2):
            self.client.force_login(kullanici)
            r = self.client.post("/admin/banka/politikadegisikligi/",
                                 {"action": "onayla", "_selected_action": [t.id], "index": "0"})
            self.assertEqual(r.status_code, 302)
        t.refresh_from_db()
        self.assertEqual(t.durum, "UYGULANDI")
        self.assertEqual(Politika.objects.get(id=self.p3.id).gereken_onay, 3)

    def test_talep_eden_onay_aksiyonunu_gormez(self):
        politika_degisikligi.talep_olustur(
            self.maker, "GUNCELLE", self.p3.id, {"alt": "50000", "onay": 3, "rol": 2}, "Dolandırıcılık riski nedeniyle artırıldı")
        self.client.force_login(self.maker)
        self.assertNotContains(self.client.get("/admin/banka/politikadegisikligi/"), 'value="onayla"')
        self.client.force_login(self.c1)
        self.assertContains(self.client.get("/admin/banka/politikadegisikligi/"), 'value="onayla"')

    def test_panel_politika_bolumu(self):
        root = self.U.objects.create_superuser("root", "root@banka.example", "x")
        self.client.force_login(root)
        r = self.client.get("/")
        self.assertContains(r, "Politika bütünlüğü")
        self.assertContains(r, "son onaylı sürümle aynı")
