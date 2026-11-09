import datetime
import io
import json
import os
import re
import shutil
import tempfile
import uuid
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from . import (anomali, baglam, bildirim, bloke_butunlugu, kart_butunlugu, ledger, personel_butunlugu,
               politika_degisikligi, rbac, services, sms, yonetim_talepleri)
from .hashlog import kayit_ekle, zinciri_dogrula
from .models import (Bildirim, Hesap, HesapHareketi, Islem, Kart, LogKaydi, Musteri, OnayTalebi, Personel, Politika,
                     PolitikaDegisikligi, PolitikaDegisiklikOyu, Rol, Terminal, TerminalGunlukKaydi, YonetimTalebi)
from .politika_degisikligi import PolitikaHatasi
from .yonetim_talepleri import YonetimHatasi
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
        personel_butunlugu.goruntu_kaydet("test: ayni kartli yetkili")  # dogrudan DB eklemesi onaysiz sayilirdi
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
        ayar = override_settings(SMS_SAGLAYICI="banka.sms.BellekSmsSaglayici")  # PIN'ler bellekteki "telefona" gider
        ayar.enable()
        self.addCleanup(ayar.disable)
        sms.BellekSmsSaglayici.gelen.clear()
        call_command("seed_demo", stdout=io.StringIO())
        self.ali = Hesap.objects.get(hesap_no="100001")
        self.zeynep = Hesap.objects.get(hesap_no="100002")
        self.kasa = Hesap.objects.get(tur="KASA")

    def giris(self, uid="a0a0a001", pin="1234"):
        return services.musteri_giris(uid, pin)["token"]

    def son_pin(self, telefon):
        """Bu telefona giden son SMS'ten PIN'i okur (PIN başka hiçbir yerde görünmez)."""
        mesajlar = [m for t, m in sms.BellekSmsSaglayici.gelen if t == telefon]
        return re.search(r"Yeni PIN'iniz: (\d{4})", mesajlar[-1]).group(1)

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
        t = services.kasa_acma_baslat(*self.CEM)  # baslatan: mudur yardimcisi
        self.assertEqual(t["gereken"], 3)
        for kisi in (self.AYSE, self.BURAK):
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

    def test_gorevli_kasa_acma_baslatamaz(self):
        with self.assertRaises(IslemHatasi) as e:
            services.kasa_acma_baslat(*self.AYSE)  # gorevli: KASA_AC_BASLAT yetkisi yok
        self.assertEqual(e.exception.kod, "rol_yetersiz")
        self.assertTrue(LogKaydi.objects.filter(olay_turu="rbac_reddi").exists())
        self.assertEqual(OnayTalebi.objects.count(), 0)

    def test_baslatan_kendi_talebini_onaylayamaz(self):
        t = services.kasa_acma_baslat(*self.CEM)
        with self.assertRaises(IslemHatasi) as e:
            services.onay_karar(t["talep_id"], *self.CEM, "ONAY")
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
        t = services.kasa_acma_baslat(*self.CEM)
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
        r = self.post("/api/kasa-ac/", {"uid": "b0b0b003", "pin": "3333"}).json()
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
        services.kasa_acma_baslat("b0b0b003", "3333")
        self.assertEqual(len(mail.outbox), 4)
        self.assertNotIn("cem@banka.example", [a for m in mail.outbox for a in m.to])
        self.assertTrue(mail.outbox[0].subject.startswith("Kasa dairesi açma talebi"))

    def test_kasa_acilinca_mudur_yrd_ve_ustune_bilgi(self):
        t = services.kasa_acma_baslat("b0b0b003", "3333")
        for uid, pin in (("b0b0b001", "1111"), ("b0b0b002", "2222"), ("b0b0b004", "4444"), ("b0b0b005", "5555")):
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


# ====================================================================== RBAC

@override_settings(MESAI_DISI_KURALI=False)
class RbacTestleri(Yardimci, TestCase):
    def setUp(self):
        self.hazirla()
        self.U = get_user_model()

    def personel(self, uid):
        return Personel.objects.get(kart_uid=uid)

    def test_rol_yetki_matrisi(self):
        Y = rbac.Yetki
        self.assertEqual(rbac.ROL_YETKILERI[Rol.GOREVLI], {Y.ISLEM_ONAYLA, Y.KASA_AC_ONAYLA})
        self.assertIn(Y.KASA_AC_BASLAT, rbac.ROL_YETKILERI[Rol.MUDUR_YRD])
        self.assertIn(Y.KASA_AC_BASLAT, rbac.ROL_YETKILERI[Rol.MUDUR])
        self.assertFalse(rbac.yetki_var_mi(self.personel("b0b0b001"), Y.KASA_AC_BASLAT))   # gorevli
        self.assertTrue(rbac.yetki_var_mi(self.personel("b0b0b003"), Y.KASA_AC_BASLAT))    # mudur yrd.
        self.assertTrue(rbac.yetki_var_mi(self.personel("b0b0b001"), Y.ISLEM_ONAYLA))

    def test_yetkisi_olmayan_rol_onay_veremez(self):
        s = services.islem_baslat(self.giris(), "CEKME", "10000")
        with mock.patch.dict(rbac.ROL_YETKILERI, {Rol.GOREVLI: set()}):
            with self.assertRaises(IslemHatasi) as e:
                services.onay_karar(s["talep_id"], "b0b0b001", "1111", "ONAY")
        self.assertEqual(e.exception.kod, "rol_yetersiz")
        self.assertTrue(LogKaydi.objects.filter(olay_turu="rbac_reddi").exists())
        self.assertEqual(services.talep_durumu(s["talep_id"])["verilen"], 0)

    def test_gruplar_koddan_kurulur(self):
        for ad, kodlar in rbac.YONETIM_GRUPLARI.items():
            self.assertEqual(set(Group.objects.get(name=ad).permissions.values_list("codename", flat=True)), set(kodlar))
        denetci = Group.objects.get(name="Denetçi")
        self.assertFalse(denetci.permissions.exclude(codename__startswith="view_").exists())

    def test_kur_idempotent_ve_bilinmeyen_izni_reddeder(self):
        once = {g.name: g.permissions.count() for g in Group.objects.all()}
        rbac.kur()
        self.assertEqual({g.name: g.permissions.count() for g in Group.objects.all()}, once)
        with mock.patch.dict(rbac.YONETIM_GRUPLARI, {"Deneme": ["yok_boyle_bir_izin"]}):
            with self.assertRaises(ValueError):
                rbac.kur()

    def test_denetim_temiz(self):
        r = rbac.denetim()
        self.assertTrue(r["saglam"])
        self.assertEqual(r["superuserlar"], [])
        self.assertEqual(r["grupsuz_yonetim_kullanicilari"], [])

    def test_gorev_ayrimi_ihlali_yakalanir(self):
        kullanici = self.U.objects.get(username="yonetim_talep")
        kullanici.groups.add(Group.objects.get(name="Yönetim Onaylayan"))  # hem maker hem checker
        r = rbac.denetim()
        self.assertFalse(r["saglam"])
        self.assertEqual([i["kullanici"] for i in r["gorev_ayrimi_ihlalleri"]], ["yonetim_talep"])

    def test_denetci_yazma_izni_yakalanir(self):
        self.U.objects.get(username="denetci").groups.add(Group.objects.get(name="Yönetim Talep Eden"))
        r = rbac.denetim()
        self.assertFalse(r["saglam"])
        self.assertEqual([i["kullanici"] for i in r["denetci_yazma_izinleri"]], ["denetci"])

    def test_superuser_uyari_olarak_raporlanir(self):
        self.U.objects.create_superuser("root", "root@banka.example", "x")
        r = rbac.denetim()
        self.assertEqual(r["superuserlar"], ["root"])
        self.assertTrue(r["saglam"])  # ihlal degil, uyari

    def test_rbac_denetimi_komutu(self):
        cikti = io.StringIO()
        call_command("rbac_denetimi", stdout=cikti)
        self.assertIn("RBAC denetimi temiz", cikti.getvalue())
        self.U.objects.get(username="yonetim_talep").groups.add(Group.objects.get(name="Yönetim Onaylayan"))
        with self.assertRaises(SystemExit):
            call_command("rbac_denetimi", stdout=io.StringIO(), stderr=io.StringIO())

    def test_denetci_hicbir_sey_degistiremez(self):
        self.client.force_login(self.U.objects.get(username="denetci"))
        self.assertEqual(self.client.get("/admin/banka/logkaydi/").status_code, 200)
        self.assertEqual(self.client.get("/admin/banka/islem/").status_code, 200)
        self.assertEqual(self.client.get("/admin/banka/yonetimtalebi/add/").status_code, 403)
        self.assertEqual(self.client.get("/admin/banka/politikadegisikligi/add/").status_code, 403)
        ayse = self.personel("b0b0b001")
        self.assertEqual(self.client.get(f"/admin/banka/personel/{ayse.id}/change/").status_code, 200)  # salt okunur gorunum
        self.assertEqual(self.client.post(f"/admin/banka/personel/{ayse.id}/change/", {"ad": "x"}).status_code, 403)


# ====================================================================== hassas yonetim islemleri (maker-checker)

GEREKCE = "Operasyonel gerekçe: vardiya planı gereği değişiklik"


@override_settings(MESAI_DISI_KURALI=False)
class YonetimTalepleriTestleri(Yardimci, TestCase):
    def setUp(self):
        self.hazirla()
        self.U = get_user_model()
        self.maker = self.U.objects.get(username="yonetim_talep")
        self.c1, self.c2, self.c3 = (self.U.objects.get(username=f"yonetim_onay{i}") for i in (1, 2, 3))

    def personel(self, uid):
        return Personel.objects.get(kart_uid=uid)

    def talep(self, tur, ham, kullanici=None):
        return yonetim_talepleri.talep_olustur(kullanici or self.maker, tur, ham, GEREKCE)

    def oy(self, t, *kullanicilar, karar="ONAY"):
        for k in kullanicilar:
            t = yonetim_talepleri.oy_ver(t.id, k, karar)
        return t

    def ekle_ham(self, **ek):
        ham = {"ad": "Fatma Demir", "kart_uid": "B0B0B006", "rol": 1, "eposta": "fatma@banka.example",
               "telefon": "+905550000006"}
        ham.update(ek)
        return ham

    # ------------------------------------------------------------ yetkili ekleme

    def test_yetkili_ekleme_akisi_ve_pin_yalniz_smsle_gider(self):
        t = self.talep("PERSONEL_EKLE", self.ekle_ham())
        self.assertEqual(t.gereken_onay, 2)
        self.assertEqual(self.oy(t, self.c1).durum, "BEKLIYOR")
        self.assertFalse(Personel.objects.filter(kart_uid="b0b0b006").exists())  # henuz yok
        with self.assertLogs("banka.bildirim", level="INFO") as kayit:
            t = self.oy(t, self.c2)
        self.assertEqual(t.durum, "UYGULANDI")
        yeni = self.personel("b0b0b006")
        self.assertEqual((yeni.ad, yeni.rol, yeni.aktif), ("Fatma Demir", 1, True))
        pin = self.son_pin("+905550000006")
        self.assertNotIn(pin, "\n".join(kayit.output))  # PIN sunucu log'unda gorunmez
        self.assertEqual(services.personel_dogrula("b0b0b006", pin).id, yeni.id)  # SMS'teki PIN calisiyor
        # PIN talepte, bildirim kaydinda ya da panelde saklanmaz
        self.assertNotIn(f"PIN'iniz: {pin}", YonetimTalebi.objects.get(id=t.id).veri)
        self.assertEqual(set(json.loads(t.veri)), {"ad", "kart_uid", "rol", "eposta", "telefon"})
        sms_kaydi = Bildirim.objects.get(olay="pin_iletildi")
        self.assertNotIn(pin, sms_kaydi.metin)
        self.assertEqual(sms_kaydi.adres, "+905550000006")
        self.assertEqual(personel_butunlugu.durum(), "tutarli")  # onayli degisiklik yeni goruntu aldi
        self.assertTrue(zinciri_dogrula()["saglam"])

    def test_mudur_eklemek_uc_onay_ister(self):
        t = self.talep("PERSONEL_EKLE", self.ekle_ham(rol=3))
        self.assertEqual(t.gereken_onay, 3)
        self.assertEqual(self.oy(t, self.c1, self.c2).durum, "BEKLIYOR")
        self.assertEqual(self.oy(t, self.c3).durum, "UYGULANDI")

    def test_yetkili_ekleme_dogrulamalari(self):
        kotuler = [
            self.ekle_ham(ad="A"), self.ekle_ham(kart_uid="xyz"), self.ekle_ham(kart_uid="b0b0b001"),
            self.ekle_ham(kart_uid="a0a0a001"), self.ekle_ham(rol=9), self.ekle_ham(telefon=""),
            self.ekle_ham(eposta="bozuk"),
        ]
        for ham in kotuler:
            with self.assertRaises(YonetimHatasi, msg=str(ham)):
                self.talep("PERSONEL_EKLE", ham)
        self.assertEqual(YonetimTalebi.objects.count(), 0)

    # ------------------------------------------------------------ rol ve aktiflik

    def test_rol_yukseltmek_iki_onay_ister(self):
        ayse = self.personel("b0b0b001")
        t = self.talep("PERSONEL_ROL", {"personel_id": ayse.id, "yeni_rol": 2})
        self.assertEqual(t.gereken_onay, 2)
        self.assertEqual(self.oy(t, self.c1, self.c2).durum, "UYGULANDI")
        self.assertEqual(self.personel("b0b0b001").rol, 2)
        mudur_yap = self.talep("PERSONEL_ROL", {"personel_id": self.personel("b0b0b002").id, "yeni_rol": 3})
        self.assertEqual(mudur_yap.gereken_onay, 3)

    def test_rol_dusurmek_tek_onay_ister_ve_yetkiyi_kaldirir(self):
        cem = self.personel("b0b0b003")
        t = self.talep("PERSONEL_ROL", {"personel_id": cem.id, "yeni_rol": 1})
        self.assertEqual(t.gereken_onay, 1)
        self.assertEqual(self.oy(t, self.c1).durum, "UYGULANDI")
        with self.assertRaises(IslemHatasi) as e:  # artik gorevli: kasa acma baslatamaz (RBAC)
            services.kasa_acma_baslat("b0b0b003", "3333")
        self.assertEqual(e.exception.kod, "rol_yetersiz")

    def test_son_muduru_dusuremezsin(self):
        elif_ = self.personel("b0b0b005")
        with self.assertRaises(YonetimHatasi) as e:
            self.talep("PERSONEL_ROL", {"personel_id": elif_.id, "yeni_rol": 2})
        self.assertEqual(e.exception.kod, "gecersiz_degisiklik")
        self.assertIn("müdür", e.exception.mesaj)

    def test_en_az_aktif_yetkili_korunur(self):
        t = self.talep("PERSONEL_DURUM", {"personel_id": self.personel("b0b0b001").id, "aktif": "pasif"})
        self.assertEqual(t.gereken_onay, 1)
        self.assertEqual(self.oy(t, self.c1).durum, "UYGULANDI")
        with self.assertRaises(YonetimHatasi) as e:  # 5 -> 4 oldu; bir daha dusurmek asgarinin altina iner
            self.talep("PERSONEL_DURUM", {"personel_id": self.personel("b0b0b002").id, "aktif": "pasif"})
        self.assertEqual(e.exception.kod, "gecersiz_degisiklik")

    def test_pasif_yetkili_giris_yapamaz_ve_aktif_etmek_iki_onay_ister(self):
        ayse = self.personel("b0b0b001")
        self.oy(self.talep("PERSONEL_DURUM", {"personel_id": ayse.id, "aktif": False}), self.c1)
        with self.assertRaises(IslemHatasi) as e:
            services.personel_dogrula("b0b0b001", "1111")
        self.assertEqual(e.exception.kod, "yetkisiz")
        t = self.talep("PERSONEL_DURUM", {"personel_id": ayse.id, "aktif": True})
        self.assertEqual(t.gereken_onay, 2)
        self.assertEqual(self.oy(t, self.c1, self.c2).durum, "UYGULANDI")
        self.assertEqual(services.personel_dogrula("b0b0b001", "1111").id, ayse.id)

    # ------------------------------------------------------------ PIN ve bloke

    def test_pin_sifirlama(self):
        burak = self.personel("b0b0b002")
        t = self.talep("PERSONEL_PIN_SIFIRLA", {"personel_id": burak.id})
        self.assertEqual(t.gereken_onay, 2)
        self.oy(t, self.c1)
        with self.assertLogs("banka.bildirim", level="INFO") as kayit:
            self.assertEqual(self.oy(t, self.c2).durum, "UYGULANDI")
        pin = self.son_pin("+905550000002")
        self.assertNotIn(pin, "\n".join(kayit.output))
        with self.assertRaises(IslemHatasi):
            services.personel_dogrula("b0b0b002", "2222")  # eski PIN artik gecmez
        self.assertEqual(services.personel_dogrula("b0b0b002", pin).id, burak.id)
        self.assertEqual(personel_butunlugu.durum(), "tutarli")

    def test_pin_sifirlamak_icin_telefon_gerekir(self):
        Personel.objects.filter(kart_uid="b0b0b002").update(telefon="")
        with self.assertRaises(YonetimHatasi) as e:
            self.talep("PERSONEL_PIN_SIFIRLA", {"personel_id": self.personel("b0b0b002").id})
        self.assertEqual(e.exception.kod, "telefon")

    def test_musteri_karti_blokesi_tek_onayla_kalkar(self):
        for _ in range(3):
            with self.assertRaises(IslemHatasi):
                services.musteri_giris("a0a0a001", "0000")
        self.assertEqual(services.kart_tipi("a0a0a001"), "BLOKE")
        kart = Kart.objects.get(uid="a0a0a001")
        t = self.talep("BLOKE_KALDIR", {"hedef": "kart", "id": kart.id})
        self.assertEqual(t.gereken_onay, 1)
        self.assertEqual(self.oy(t, self.c1).durum, "UYGULANDI")
        self.assertEqual(services.kart_tipi("a0a0a001"), "MUSTERI")
        self.assertTrue(services.musteri_giris("a0a0a001", "1234")["token"])

    def test_yetkili_blokesi_iki_onayla_kalkar_ve_butunlugu_bozmaz(self):
        for _ in range(3):
            with self.assertRaises(IslemHatasi):
                services.personel_dogrula("b0b0b001", "0000")
        self.assertEqual(services.kart_tipi("b0b0b001"), "BLOKE")
        self.assertEqual(personel_butunlugu.durum(), "tutarli")  # sistemin kendi bloke etmesi sapma sayilmaz
        t = self.talep("BLOKE_KALDIR", {"hedef": "personel", "id": self.personel("b0b0b001").id})
        self.assertEqual(t.gereken_onay, 2)
        self.assertEqual(self.oy(t, self.c1, self.c2).durum, "UYGULANDI")
        self.assertEqual(services.kart_tipi("b0b0b001"), "PERSONEL")

    def test_bloke_degilse_talep_acilamaz(self):
        with self.assertRaises(YonetimHatasi) as e:
            self.talep("BLOKE_KALDIR", {"hedef": "kart", "id": Kart.objects.get(uid="a0a0a001").id})
        self.assertEqual(e.exception.kod, "gecersiz")
        with self.assertRaises(YonetimHatasi):
            self.talep("BLOKE_KALDIR", {"hedef": "baska", "id": 1})

    # ------------------------------------------------------------ limit ve terminal

    def test_gunluk_limit_artirmak_iki_azaltmak_tek_onay(self):
        artir = self.talep("GUNLUK_LIMIT", {"hesap_no": "100001", "yeni_limit": "2000000"})
        self.assertEqual(artir.gereken_onay, 2)
        self.oy(artir, self.c1, self.c2)
        self.assertEqual(Hesap.objects.get(hesap_no="100001").gunluk_limit, Decimal("2000000.00"))
        azalt = self.talep("GUNLUK_LIMIT", {"hesap_no": "100001", "yeni_limit": "500000"})
        self.assertEqual(azalt.gereken_onay, 1)
        self.assertEqual(self.oy(azalt, self.c1).durum, "UYGULANDI")
        self.assertEqual(Hesap.objects.get(hesap_no="100001").gunluk_limit, Decimal("500000.00"))

    def test_gunluk_limit_korkuluklari(self):
        for yeni in ("6000000", "0", "-5", "abc", "1000000"):  # azami asimi, gecersiz, ayni deger
            with self.assertRaises(YonetimHatasi, msg=yeni):
                self.talep("GUNLUK_LIMIT", {"hesap_no": "100001", "yeni_limit": yeni})
        with self.assertRaises(YonetimHatasi):
            self.talep("GUNLUK_LIMIT", {"hesap_no": "900000", "yeni_limit": "100"})  # kasa hesabi musteri degil

    def test_terminal_kapatmak_tek_acmak_iki_onay(self):
        t = self.talep("TERMINAL_DURUM", {"kod": "ATM-02", "aktif": False})
        self.assertEqual(t.gereken_onay, 1)
        self.assertEqual(self.oy(t, self.c1).durum, "UYGULANDI")
        r = Client().post("/api/kart/", data=json.dumps({"uid": "a0a0a001"}), content_type="application/json",
                          headers={"X-Kopru-Anahtari": "gelistirme-kopru-anahtari", "X-Terminal-Id": "ATM-02"})
        self.assertEqual(r.status_code, 403)
        ac = self.talep("TERMINAL_DURUM", {"kod": "ATM-02", "aktif": True})
        self.assertEqual(ac.gereken_onay, 2)
        self.assertEqual(self.oy(ac, self.c1, self.c2).durum, "UYGULANDI")
        self.assertTrue(Terminal.objects.get(kod="ATM-02").aktif)

    # ------------------------------------------------------------ maker-checker kurallari

    def test_talep_eden_kendi_talebini_onaylayamaz(self):
        root = self.U.objects.create_superuser("root", "root@banka.example", "x")
        t = self.talep("GUNLUK_LIMIT", {"hesap_no": "100001", "yeni_limit": "500000"}, kullanici=root)
        with self.assertRaises(YonetimHatasi) as e:
            yonetim_talepleri.oy_ver(t.id, root, "ONAY")
        self.assertEqual(e.exception.kod, "kendi_talebi")
        self.assertTrue(LogKaydi.objects.filter(olay_turu="yonetim_onay_reddi_kendi_talebi").exists())

    def test_ayni_onaylayan_iki_kez_sayilmaz(self):
        t = self.talep("PERSONEL_EKLE", self.ekle_ham())
        self.oy(t, self.c1)
        with self.assertRaises(YonetimHatasi) as e:
            self.oy(t, self.c1)
        self.assertEqual(e.exception.kod, "tekrar")
        self.assertEqual(YonetimTalebi.objects.get(id=t.id).oylar.count(), 1)

    def test_yetkisiz_kullanici(self):
        t = self.talep("GUNLUK_LIMIT", {"hesap_no": "100001", "yeni_limit": "500000"})
        dis = self.U.objects.create_user("dis", is_staff=True)
        politika_onaylayan = self.U.objects.get(username="politika_onay1")  # baska alanin onaylayicisi
        for kullanici in (self.maker, dis, politika_onaylayan):
            with self.assertRaises(YonetimHatasi) as e:
                yonetim_talepleri.oy_ver(t.id, kullanici, "ONAY")
            self.assertEqual(e.exception.kod, "yetki")
        for kullanici in (dis, politika_onaylayan, self.c1):  # onaylayicilar talep de acamaz
            with self.assertRaises(YonetimHatasi) as e:
                self.talep("GUNLUK_LIMIT", {"hesap_no": "100002", "yeni_limit": "100"}, kullanici=kullanici)
            self.assertEqual(e.exception.kod, "yetki")

    def test_red_sure_ve_iptal(self):
        t = self.talep("GUNLUK_LIMIT", {"hesap_no": "100001", "yeni_limit": "500000"})
        self.assertEqual(self.oy(t, self.c1, karar="RED").durum, "REDDEDILDI")
        self.assertEqual(self.oy(t, self.c2).durum, "REDDEDILDI")  # kapali talep
        self.assertEqual(Hesap.objects.get(hesap_no="100001").gunluk_limit, Decimal("1000000.00"))

        t2 = self.talep("GUNLUK_LIMIT", {"hesap_no": "100001", "yeni_limit": "500000"})
        YonetimTalebi.objects.filter(id=t2.id).update(son_tarih=timezone.now() - datetime.timedelta(seconds=1))
        self.assertEqual(self.oy(t2, self.c1).durum, "SURESI_DOLDU")

        t3 = self.talep("GUNLUK_LIMIT", {"hesap_no": "100001", "yeni_limit": "400000"})
        with self.assertRaises(YonetimHatasi) as e:
            yonetim_talepleri.iptal(t3.id, self.c1)
        self.assertEqual(e.exception.kod, "yetki")
        self.assertEqual(yonetim_talepleri.iptal(t3.id, self.maker).durum, "IPTAL")

    def test_ayni_hedef_icin_cakisan_talep(self):
        ayse = self.personel("b0b0b001")
        self.talep("PERSONEL_ROL", {"personel_id": ayse.id, "yeni_rol": 2})
        with self.assertRaises(YonetimHatasi) as e:  # farkli tur ama ayni hedef
            self.talep("PERSONEL_PIN_SIFIRLA", {"personel_id": ayse.id})
        self.assertEqual(e.exception.kod, "cakisan")

    def test_gerekce_ve_tur_zorunlu(self):
        with self.assertRaises(YonetimHatasi) as e:
            yonetim_talepleri.talep_olustur(self.maker, "GUNLUK_LIMIT", {"hesap_no": "100001", "yeni_limit": "1"}, "kısa")
        self.assertEqual(e.exception.kod, "gerekce")
        with self.assertRaises(YonetimHatasi) as e:
            self.talep("YOK", {})
        self.assertEqual(e.exception.kod, "tur")

    def test_talep_acilinca_onaylayicilara_eposta_gider(self):
        self.talep("GUNLUK_LIMIT", {"hesap_no": "100001", "yeni_limit": "500000"})
        alicilar = sorted(a for m in mail.outbox if m.subject.startswith("Yönetim talebi") for a in m.to)
        self.assertEqual(alicilar, ["yonetim_onay1@banka.example", "yonetim_onay2@banka.example",
                                    "yonetim_onay3@banka.example"])

    def test_olaylar_zincire_yazilir(self):
        self.oy(self.talep("PERSONEL_EKLE", self.ekle_ham()), self.c1, self.c2)
        olaylar = list(LogKaydi.objects.values_list("olay_turu", flat=True))
        for beklenen in ("yonetim_talebi", "yonetim_onayi", "yonetim_talebi_uygulandi"):
            self.assertIn(beklenen, olaylar)
        self.assertEqual(olaylar.count("personel_goruntusu"), 2)  # seed + bu degisiklik
        self.assertTrue(zinciri_dogrula()["saglam"])


# ====================================================================== yetkili (personel) butunlugu

@override_settings(MESAI_DISI_KURALI=False)
class PersonelButunluguTestleri(Yardimci, TestCase):
    def setUp(self):
        self.hazirla()
        self.U = get_user_model()

    def bekleyen(self, tutar="10000"):
        return services.islem_baslat(self.giris(), "CEKME", tutar)

    def test_baslangic_tutarli(self):
        self.assertEqual(personel_butunlugu.durum(), "tutarli")

    def test_sahte_yetkili_eklenirse_onaylar_ve_kasa_acma_kapanir(self):
        s = self.bekleyen()
        sahte = Personel(ad="Sahte", kart_uid="c0c0c001", rol=Rol.MUDUR)
        sahte.pin_ayarla("9999")
        sahte.save()  # dogrudan veritabani mudahalesi
        self.assertEqual(personel_butunlugu.durum(), "tutarsiz")
        with self.assertRaises(IslemHatasi) as e:
            services.onay_karar(s["talep_id"], "b0b0b001", "1111", "ONAY")
        self.assertEqual(e.exception.kod, "butunluk")
        with self.assertRaises(IslemHatasi) as e:
            services.kasa_acma_baslat("b0b0b003", "3333")
        self.assertEqual(e.exception.kod, "butunluk")
        self.assertEqual(LogKaydi.objects.filter(olay_turu="personel_butunluk_ihlali").count(), 1)  # saatte bir
        rapor = ledger.mutabakat()
        self.assertFalse(rapor["saglam"])
        self.assertEqual(rapor["personel"], "tutarsiz")
        self.assertTrue(zinciri_dogrula()["saglam"])  # mudahale gorunur kaldi, log bozulmadi

    def test_rol_pin_ve_aktiflik_degisiklikleri_yakalanir(self):
        degisiklikler = [
            lambda: Personel.objects.filter(kart_uid="b0b0b001").update(rol=3),
            lambda: Personel.objects.filter(kart_uid="b0b0b001").update(aktif=False),
            lambda: Personel.objects.filter(kart_uid="b0b0b001").update(pin_hash="uydurma-hash"),
            lambda: Personel.objects.filter(kart_uid="b0b0b001").update(kart_uid="d0d0d001"),
        ]
        for degistir in degisiklikler:
            with transaction.atomic():
                degistir()
                self.assertEqual(personel_butunlugu.durum(), "tutarsiz")
                transaction.set_rollback(True)
        self.assertEqual(personel_butunlugu.durum(), "tutarli")

    def test_sistemin_kendi_guncellemeleri_ve_iletisim_bilgisi_bozmaz(self):
        Personel.objects.filter(kart_uid="b0b0b001").update(bloke=True, hatali_deneme=2)
        Personel.objects.filter(kart_uid="b0b0b002").update(eposta="yeni@banka.example", telefon="+905559999999", ad="Yeni Ad")
        self.assertEqual(personel_butunlugu.durum(), "tutarli")

    def test_tutarsizken_personel_degisikligi_uygulanmaz_ama_digerleri_uygulanir(self):
        Personel.objects.filter(kart_uid="b0b0b001").update(rol=3)  # onaysiz mudahale
        maker = self.U.objects.get(username="yonetim_talep")
        c1, c2 = self.U.objects.get(username="yonetim_onay1"), self.U.objects.get(username="yonetim_onay2")
        burak = Personel.objects.get(kart_uid="b0b0b002")
        t = yonetim_talepleri.talep_olustur(maker, "PERSONEL_ROL", {"personel_id": burak.id, "yeni_rol": 2}, GEREKCE)
        yonetim_talepleri.oy_ver(t.id, c1, "ONAY")
        self.assertEqual(yonetim_talepleri.oy_ver(t.id, c2, "ONAY").durum, "BASARISIZ")
        self.assertEqual(Personel.objects.get(kart_uid="b0b0b002").rol, 1)
        self.assertTrue(LogKaydi.objects.filter(olay_turu="yonetim_talebi_uygulanamadi").exists())
        # yetkili kumesini ilgilendirmeyen bir talep (limit) yine uygulanir
        limit = yonetim_talepleri.talep_olustur(maker, "GUNLUK_LIMIT", {"hesap_no": "100001", "yeni_limit": "500000"}, GEREKCE)
        self.assertEqual(yonetim_talepleri.oy_ver(limit.id, c1, "ONAY").durum, "UYGULANDI")

    def test_goruntu_al_komutu(self):
        cikti = io.StringIO()
        call_command("personel_goruntu_al", stdout=cikti)
        self.assertIn("zaten", cikti.getvalue())
        Personel.objects.filter(kart_uid="b0b0b001").update(rol=3)
        with self.assertRaises(SystemExit):  # onaysiz degisikligi sessizce mesrulastirmaz
            call_command("personel_goruntu_al", stdout=io.StringIO(), stderr=io.StringIO())
        call_command("personel_goruntu_al", "--zorla", stdout=io.StringIO())
        self.assertEqual(personel_butunlugu.durum(), "tutarli")
        self.assertTrue(LogKaydi.objects.filter(olay_turu="personel_goruntusu", veri__contains="--zorla").exists())

    def test_onayli_goruntu_yoksa_zorlama_yapilmaz(self):
        LogKaydi.objects.filter(olay_turu="personel_goruntusu").delete()
        self.assertEqual(personel_butunlugu.durum(), "baslangic_yok")
        s = self.bekleyen()
        self.assertEqual(services.onay_karar(s["talep_id"], "b0b0b001", "1111", "ONAY")["durum"], "UYGULANDI")
        self.assertEqual(ledger.mutabakat()["personel"], "baslangic_yok")


# ====================================================================== admin: dogrudan duzenleme kapali

@override_settings(MESAI_DISI_KURALI=False)
class YonetimAdminTestleri(Yardimci, TestCase):
    def setUp(self):
        self.hazirla()
        self.U = get_user_model()
        self.root = self.U.objects.create_superuser("root", "root@banka.example", "x")

    def test_yetkili_ekleme_rol_ve_silme_admin_den_yapilamaz(self):
        self.client.force_login(self.root)
        self.assertEqual(self.client.get("/admin/banka/personel/add/").status_code, 403)
        ayse = Personel.objects.get(kart_uid="b0b0b001")
        self.assertEqual(self.client.post(f"/admin/banka/personel/{ayse.id}/delete/", {"post": "yes"}).status_code, 403)
        r = self.client.post(f"/admin/banka/personel/{ayse.id}/change/", {
            "ad": "Ayşe Yeni", "eposta": "ayse@banka.example", "telefon": "+905550000001",
            "rol": "3", "aktif": "", "bloke": "on", "kart_uid": "ffffffff",   # salt okunur alanlar yok sayilir
        })
        self.assertEqual(r.status_code, 302)
        ayse.refresh_from_db()
        self.assertEqual((ayse.ad, ayse.rol, ayse.aktif, ayse.bloke, ayse.kart_uid), ("Ayşe Yeni", 1, True, False, "b0b0b001"))
        self.assertEqual(personel_butunlugu.durum(), "tutarli")

    def test_bakiye_ve_limit_admin_den_degismez(self):
        self.client.force_login(self.root)
        hesap = Hesap.objects.get(hesap_no="100001")
        r = self.client.post(f"/admin/banka/hesap/{hesap.id}/change/", {
            "musteri": hesap.musteri_id, "hesap_no": "100001", "bakiye": "99999999", "gunluk_limit": "99999999",
            "acilis_bakiyesi": "99999999", "tur": "KASA",
        })
        self.assertEqual(r.status_code, 302)
        hesap.refresh_from_db()
        self.assertEqual((hesap.bakiye, hesap.gunluk_limit, hesap.tur), (Decimal("1000000.00"), Decimal("1000000.00"), "MUSTERI"))

    def test_terminal_durumu_ve_kart_blokesi_admin_den_degismez(self):
        self.client.force_login(self.root)
        t = Terminal.objects.get(kod="ATM-02")
        self.client.post(f"/admin/banka/terminal/{t.id}/change/", {"kod": "ATM-02", "ad": "Yeni", "enlem": "41.0", "boylam": "28.9"})
        t.refresh_from_db()
        self.assertTrue(t.aktif)  # aktif alani formda yok: kapatilamaz
        kart = Kart.objects.get(uid="a0a0a001")
        Kart.objects.filter(id=kart.id).update(bloke=True, hatali_deneme=3)
        r = self.client.post(f"/admin/banka/kart/{kart.id}/change/", {"uid": "a0a0a001", "hesap": kart.hesap_id, "bloke": ""})
        self.assertEqual(r.status_code, 403)  # kart admin'de tamamen salt okunur
        kart.refresh_from_db()
        self.assertTrue(kart.bloke)  # bloke yalnizca talep-onay ile kalkar

    def test_yonetim_talebi_formu(self):
        self.client.force_login(self.U.objects.get(username="yonetim_talep"))
        self.assertEqual(self.client.get("/admin/banka/yonetimtalebi/add/").status_code, 200)
        veri = {"tur": "PERSONEL_EKLE", "gerekce": "Yeni vardiya yetkilisi eklenmesi", "ad": "Fatma Demir",
                "kart_uid": "b0b0b006", "deger": "1", "eposta": "fatma@banka.example", "telefon": "+905550000006",
                "hedef_no": "", "hedef_turu": "", "_save": "Kaydet"}
        r = self.client.post("/admin/banka/yonetimtalebi/add/", veri)
        self.assertEqual(r.status_code, 302)
        t = YonetimTalebi.objects.get()
        self.assertEqual((t.tur, t.durum, t.gereken_onay), ("PERSONEL_EKLE", "BEKLIYOR", 2))
        self.assertFalse(Personel.objects.filter(kart_uid="b0b0b006").exists())  # henuz uygulanmadi
        kotu = self.client.post("/admin/banka/yonetimtalebi/add/", {**veri, "kart_uid": "b0b0b007", "deger": "9"})
        self.assertEqual(kotu.status_code, 200)
        self.assertContains(kotu, "Rol 1")
        self.assertEqual(YonetimTalebi.objects.count(), 1)

    def test_onay_admin_aksiyonuyla_verilir_ve_talep_eden_aksiyonu_gormez(self):
        maker = self.U.objects.get(username="yonetim_talep")
        t = yonetim_talepleri.talep_olustur(maker, "GUNLUK_LIMIT", {"hesap_no": "100001", "yeni_limit": "500000"}, GEREKCE)
        self.client.force_login(maker)
        self.assertNotContains(self.client.get("/admin/banka/yonetimtalebi/"), 'value="onayla"')
        c1 = self.U.objects.get(username="yonetim_onay1")
        self.client.force_login(c1)
        self.assertContains(self.client.get("/admin/banka/yonetimtalebi/"), 'value="onayla"')
        r = self.client.post("/admin/banka/yonetimtalebi/", {"action": "onayla", "_selected_action": [t.id], "index": "0"})
        self.assertEqual(r.status_code, 302)
        t.refresh_from_db()
        self.assertEqual(t.durum, "UYGULANDI")
        self.assertEqual(Hesap.objects.get(hesap_no="100001").gunluk_limit, Decimal("500000.00"))

    def test_panel_yetkili_butunlugu_bolumu(self):
        self.client.force_login(self.root)
        r = self.client.get("/")
        self.assertContains(r, "Yetkili bütünlüğü")
        self.assertContains(r, "Yetkili kayıtları son onaylı sürümle aynı")


# ====================================================================== musteri karti ve PIN'i (maker-checker)

@override_settings(MESAI_DISI_KURALI=False)
class MusteriKartTalepleriTestleri(Yardimci, TestCase):
    def setUp(self):
        self.hazirla()
        self.U = get_user_model()
        self.maker = self.U.objects.get(username="yonetim_talep")
        self.c1, self.c2 = (self.U.objects.get(username=f"yonetim_onay{i}") for i in (1, 2))

    def talep(self, tur, ham):
        return yonetim_talepleri.talep_olustur(self.maker, tur, ham, GEREKCE)

    def oy(self, t, *kullanicilar):
        for k in kullanicilar:
            t = yonetim_talepleri.oy_ver(t.id, k, "ONAY")
        return t

    def test_demo_musterilerinin_telefonu_var(self):
        self.assertEqual(Musteri.objects.get(hesaplar__hesap_no="100001").telefon, "+905551000001")

    def test_kart_tanimlama_akisi_pin_musterinin_telefonuna_gider(self):
        t = self.talep("KART_TANIMLA", {"hesap_no": "100002", "kart_uid": "A0A0A003"})
        self.assertEqual(t.gereken_onay, 2)
        self.assertIn("+905*******02", t.ozet)         # onaylayici numarayi maskeli gorur
        self.assertNotIn("+905551000002", t.ozet)
        self.oy(t, self.c1)
        self.assertFalse(Kart.objects.filter(uid="a0a0a003").exists())  # henuz yok
        with self.assertLogs("banka.bildirim", level="INFO") as kayit:
            self.assertEqual(self.oy(t, self.c2).durum, "UYGULANDI")
        kart = Kart.objects.get(uid="a0a0a003")
        self.assertEqual(kart.hesap.hesap_no, "100002")
        pin = self.son_pin("+905551000002")
        self.assertNotIn(pin, "\n".join(kayit.output))  # PIN sunucu log'unda gorunmez
        self.assertTrue(services.musteri_giris("a0a0a003", pin)["token"])  # SMS'teki PIN calisiyor
        sms_kaydi = Bildirim.objects.get(olay="pin_iletildi")
        self.assertEqual(sms_kaydi.adres, "+905551000002")
        self.assertNotIn(pin, sms_kaydi.metin)
        self.assertEqual(set(json.loads(t.veri)), {"hesap_no", "kart_uid", "telefon_ozeti"})  # PIN talepte yok
        self.assertEqual(kart_butunlugu.durum(), "tutarli")
        self.assertEqual(services.kart_tipi("a0a0a003"), "MUSTERI")
        self.assertTrue(zinciri_dogrula()["saglam"])

    def test_kart_tanimlama_dogrulamalari(self):
        kotuler = [
            {"hesap_no": "999999", "kart_uid": "a0a0a003"},      # hesap yok
            {"hesap_no": "900000", "kart_uid": "a0a0a003"},      # kasa hesabi musteri degil
            {"hesap_no": "100002", "kart_uid": "xyz"},           # gecersiz uid
            {"hesap_no": "100002", "kart_uid": "a0a0a001"},      # baska musterinin karti
            {"hesap_no": "100002", "kart_uid": "b0b0b001"},      # yetkili karti
        ]
        for ham in kotuler:
            with self.assertRaises(YonetimHatasi, msg=str(ham)):
                self.talep("KART_TANIMLA", ham)
        Musteri.objects.filter(hesaplar__hesap_no="100002").update(telefon="")
        with self.assertRaises(YonetimHatasi) as e:
            self.talep("KART_TANIMLA", {"hesap_no": "100002", "kart_uid": "a0a0a003"})
        self.assertEqual(e.exception.kod, "telefon")
        self.assertEqual(YonetimTalebi.objects.count(), 0)

    def test_hesap_basina_kart_siniri(self):
        zeynep = Hesap.objects.get(hesap_no="100002")
        for uid in ("a0a0a010", "a0a0a011"):  # zaten 1 karti var: toplam 3
            kart = Kart(uid=uid, hesap=zeynep)
            kart.pin_ayarla("1234")
            kart.save()
        with self.assertRaises(YonetimHatasi) as e:
            self.talep("KART_TANIMLA", {"hesap_no": "100002", "kart_uid": "a0a0a012"})
        self.assertEqual(e.exception.kod, "limit")

    def test_musteri_pin_sifirlama(self):
        kart = Kart.objects.get(uid="a0a0a001")
        t = self.talep("KART_PIN_SIFIRLA", {"kart_id": kart.id})
        self.assertEqual(t.gereken_onay, 2)
        self.oy(t, self.c1)
        with self.assertLogs("banka.bildirim", level="INFO") as kayit:
            self.assertEqual(self.oy(t, self.c2).durum, "UYGULANDI")
        pin = self.son_pin("+905551000001")
        self.assertNotIn(pin, "\n".join(kayit.output))
        with self.assertRaises(IslemHatasi):
            services.musteri_giris("a0a0a001", "1234")      # eski PIN artik gecmez
        self.assertTrue(services.musteri_giris("a0a0a001", pin)["token"])
        self.assertEqual(kart_butunlugu.durum(), "tutarli")
        self.assertEqual(services.kart_tipi("a0a0a001"), "MUSTERI")

    def test_bekleyen_pin_talebinde_telefon_degisirse_uygulanmaz(self):
        kart = Kart.objects.get(uid="a0a0a001")
        t = self.talep("KART_PIN_SIFIRLA", {"kart_id": kart.id})
        Musteri.objects.filter(hesaplar__hesap_no="100001").update(telefon="+905559998877")  # numara degisti
        self.oy(t, self.c1)
        self.assertEqual(self.oy(t, self.c2).durum, "BASARISIZ")
        self.assertTrue(services.musteri_giris("a0a0a001", "1234")["token"])  # PIN degismedi
        self.assertEqual(Bildirim.objects.filter(olay="pin_iletildi").count(), 0)  # yeni numaraya SMS gitmedi
        self.assertTrue(LogKaydi.objects.filter(olay_turu="yonetim_talebi_uygulanamadi").exists())

    def test_telefon_degistirme(self):
        musteri = Musteri.objects.get(hesaplar__hesap_no="100001")
        t = self.talep("MUSTERI_TELEFON", {"musteri_id": musteri.id, "yeni_telefon": "+905559998877"})
        self.assertEqual(t.gereken_onay, 2)
        self.assertIn("+905559998877", t.ozet)           # yeni numara tam gorunur (musteriyle dogrulamak icin)
        self.assertNotIn("+905551000001", t.ozet)        # eski numara maskeli
        self.assertEqual(self.oy(t, self.c1, self.c2).durum, "UYGULANDI")
        musteri.refresh_from_db()
        self.assertEqual(musteri.telefon, "+905559998877")
        for ham in ({"yeni_telefon": "123"}, {"yeni_telefon": "+905559998877"}, {"yeni_telefon": ""}):
            with self.assertRaises(YonetimHatasi, msg=str(ham)):
                self.talep("MUSTERI_TELEFON", {"musteri_id": musteri.id, **ham})

    def test_yeni_pin_onayli_degisiklikten_sonra_yeni_numaraya_gider(self):
        musteri = Musteri.objects.get(hesaplar__hesap_no="100001")
        self.oy(self.talep("MUSTERI_TELEFON", {"musteri_id": musteri.id, "yeni_telefon": "+905559998877"}), self.c1, self.c2)
        t = self.talep("KART_PIN_SIFIRLA", {"kart_id": Kart.objects.get(uid="a0a0a001").id})
        with self.assertLogs("banka.bildirim", level="INFO") as kayit:
            self.assertEqual(self.oy(t, self.c1, self.c2).durum, "UYGULANDI")
        self.assertIn("+905559998877", "\n".join(kayit.output))   # log numarayi gosterir, icerigi degil
        self.assertTrue(self.son_pin("+905559998877"))          # PIN yeni numaranin "telefonuna" gitti

    def test_cakisan_talepler(self):
        kart = Kart.objects.get(uid="a0a0a001")
        self.talep("KART_PIN_SIFIRLA", {"kart_id": kart.id})
        with self.assertRaises(YonetimHatasi) as e:
            self.talep("KART_PIN_SIFIRLA", {"kart_id": kart.id})
        self.assertEqual(e.exception.kod, "cakisan")
        self.talep("KART_TANIMLA", {"hesap_no": "100002", "kart_uid": "a0a0a003"})
        with self.assertRaises(YonetimHatasi) as e:
            self.talep("KART_TANIMLA", {"hesap_no": "100001", "kart_uid": "a0a0a003"})
        self.assertEqual(e.exception.kod, "cakisan")

    def test_olaylar_zincire_yazilir(self):
        self.oy(self.talep("KART_TANIMLA", {"hesap_no": "100002", "kart_uid": "a0a0a003"}), self.c1, self.c2)
        olaylar = list(LogKaydi.objects.values_list("olay_turu", flat=True))
        self.assertEqual(olaylar.count("kart_goruntusu"), 2)  # seed + bu degisiklik
        self.assertIn("yonetim_talebi_uygulandi", olaylar)
        self.assertTrue(zinciri_dogrula()["saglam"])


# ====================================================================== kart butunlugu (kart basina)

@override_settings(MESAI_DISI_KURALI=False)
class KartButunluguTestleri(Yardimci, TestCase):
    def setUp(self):
        self.hazirla()
        self.U = get_user_model()

    def sahte_kart(self, uid="e0e0e001", pin="1111", hesap_no="100002"):
        kart = Kart(uid=uid, hesap=Hesap.objects.get(hesap_no=hesap_no))
        kart.pin_ayarla(pin)
        kart.save()  # dogrudan veritabani mudahalesi
        return kart

    def test_baslangic_tutarli(self):
        self.assertEqual(kart_butunlugu.durum(), "tutarli")
        self.assertTrue(kart_butunlugu.kart_dogrulandi_mi(Kart.objects.select_related("hesap").get(uid="a0a0a001")))

    def test_sahte_kart_yalniz_kendisi_kapanir(self):
        self.sahte_kart()
        self.assertEqual(services.kart_tipi("e0e0e001"), "BLOKE")
        with self.assertRaises(IslemHatasi) as e:
            services.musteri_giris("e0e0e001", "1111")
        self.assertEqual(e.exception.kod, "kart_butunluk")
        # diger musteriler etkilenmez (hizmet reddi yok)
        self.assertEqual(services.kart_tipi("a0a0a001"), "MUSTERI")
        self.assertTrue(services.musteri_giris("a0a0a001", "1234")["token"])
        self.assertTrue(services.musteri_giris("a0a0a002", "4321")["token"])
        self.assertEqual(LogKaydi.objects.filter(olay_turu="kart_butunluk_ihlali").count(), 1)  # kart basina saatte bir
        rapor = ledger.mutabakat()
        self.assertFalse(rapor["saglam"])
        self.assertEqual(rapor["kart"], "tutarsiz")
        self.assertTrue(zinciri_dogrula()["saglam"])
        self.assertIn("butunluk_ihlali", {b["kural"] for b in anomali.tara()})

    def test_pin_degisikligi_ve_hesap_tasima_yakalanir(self):
        zeynep = Hesap.objects.get(hesap_no="100002")

        def pin_degistir():
            kart = Kart.objects.get(uid="a0a0a001")
            kart.pin_ayarla("9999")
            kart.save()

        degisiklikler = [pin_degistir, lambda: Kart.objects.filter(uid="a0a0a001").update(hesap=zeynep)]
        for degistir in degisiklikler:
            with transaction.atomic():
                degistir()
                self.assertEqual(services.kart_tipi("a0a0a001"), "BLOKE")
                transaction.set_rollback(True)
        self.assertEqual(services.kart_tipi("a0a0a001"), "MUSTERI")

    def test_sistemin_kendi_guncellemeleri_bozmaz(self):
        Kart.objects.filter(uid="a0a0a001").update(bloke=True, hatali_deneme=2)
        self.assertEqual(kart_butunlugu.durum(), "tutarli")
        self.assertEqual(services.kart_tipi("a0a0a001"), "BLOKE")  # bloke oldugu icin, butunluk yuzunden degil
        self.assertFalse(LogKaydi.objects.filter(olay_turu="kart_butunluk_ihlali").exists())

    def test_tutarsizken_kart_talepleri_uygulanmaz_ama_digerleri_uygulanir(self):
        self.sahte_kart()
        maker = self.U.objects.get(username="yonetim_talep")
        c1, c2 = (self.U.objects.get(username=f"yonetim_onay{i}") for i in (1, 2))
        kart = Kart.objects.get(uid="a0a0a001")
        t = yonetim_talepleri.talep_olustur(maker, "KART_PIN_SIFIRLA", {"kart_id": kart.id}, GEREKCE)
        yonetim_talepleri.oy_ver(t.id, c1, "ONAY")
        self.assertEqual(yonetim_talepleri.oy_ver(t.id, c2, "ONAY").durum, "BASARISIZ")
        self.assertTrue(services.musteri_giris("a0a0a001", "1234")["token"])  # PIN degismedi
        limit = yonetim_talepleri.talep_olustur(maker, "GUNLUK_LIMIT", {"hesap_no": "100001", "yeni_limit": "500000"}, GEREKCE)
        self.assertEqual(yonetim_talepleri.oy_ver(limit.id, c1, "ONAY").durum, "UYGULANDI")

    def test_onayli_goruntu_yoksa_zorlama_yapilmaz(self):
        LogKaydi.objects.filter(olay_turu="kart_goruntusu").delete()
        self.sahte_kart()
        self.assertEqual(kart_butunlugu.durum(), "baslangic_yok")
        self.assertEqual(services.kart_tipi("e0e0e001"), "MUSTERI")
        self.assertTrue(services.musteri_giris("e0e0e001", "1111")["token"])
        self.assertEqual(ledger.mutabakat()["kart"], "baslangic_yok")

    def test_goruntu_al_komutu(self):
        cikti = io.StringIO()
        call_command("kart_goruntu_al", stdout=cikti)
        self.assertIn("zaten", cikti.getvalue())
        self.sahte_kart()
        with self.assertRaises(SystemExit):  # onaysiz karti sessizce mesrulastirmaz
            call_command("kart_goruntu_al", stdout=io.StringIO(), stderr=io.StringIO())
        call_command("kart_goruntu_al", "--zorla", stdout=io.StringIO())
        self.assertEqual(kart_butunlugu.durum(), "tutarli")
        self.assertEqual(services.kart_tipi("e0e0e001"), "MUSTERI")
        self.assertTrue(LogKaydi.objects.filter(olay_turu="kart_goruntusu", veri__contains="--zorla").exists())

    def test_bloke_kaldirma_kart_butunlugunu_bozmaz(self):
        for _ in range(3):
            with self.assertRaises(IslemHatasi):
                services.musteri_giris("a0a0a001", "0000")
        maker = self.U.objects.get(username="yonetim_talep")
        c1 = self.U.objects.get(username="yonetim_onay1")
        t = yonetim_talepleri.talep_olustur(maker, "BLOKE_KALDIR", {"hedef": "kart", "id": Kart.objects.get(uid="a0a0a001").id}, GEREKCE)
        self.assertEqual(yonetim_talepleri.oy_ver(t.id, c1, "ONAY").durum, "UYGULANDI")
        self.assertEqual(kart_butunlugu.durum(), "tutarli")
        self.assertEqual(services.kart_tipi("a0a0a001"), "MUSTERI")


@override_settings(MESAI_DISI_KURALI=False)
class KartAdminTestleri(Yardimci, TestCase):
    def setUp(self):
        self.hazirla()
        self.U = get_user_model()
        self.root = self.U.objects.create_superuser("root", "root@banka.example", "x")

    def test_kart_admin_den_eklenemez_degistirilemez_silinemez(self):
        self.client.force_login(self.root)
        kart = Kart.objects.get(uid="a0a0a001")
        self.assertEqual(self.client.get("/admin/banka/kart/").status_code, 200)
        self.assertEqual(self.client.get("/admin/banka/kart/add/").status_code, 403)
        self.assertEqual(self.client.get(f"/admin/banka/kart/{kart.id}/change/").status_code, 200)  # salt okunur gorunum
        self.assertEqual(self.client.post(f"/admin/banka/kart/{kart.id}/change/", {"uid": "ffffffff"}).status_code, 403)
        self.assertEqual(self.client.post(f"/admin/banka/kart/{kart.id}/delete/", {"post": "yes"}).status_code, 403)
        kart.refresh_from_db()
        self.assertEqual(kart.uid, "a0a0a001")
        html = self.client.get(f"/admin/banka/kart/{kart.id}/change/").content.decode()
        self.assertNotIn(kart.pin_hash, html)  # PIN hash'i bile gorunmez

    def test_musteri_telefonu_admin_den_degismez(self):
        self.client.force_login(self.root)
        musteri = Musteri.objects.get(hesaplar__hesap_no="100001")
        r = self.client.post(f"/admin/banka/musteri/{musteri.id}/change/", {"ad": "Ali Y.", "telefon": "+905550000000"})
        self.assertEqual(r.status_code, 302)
        musteri.refresh_from_db()
        self.assertEqual((musteri.ad, musteri.telefon), ("Ali Y.", "+905551000001"))  # telefon salt okunur

    def test_kart_ve_telefon_talep_formlari(self):
        self.client.force_login(self.U.objects.get(username="yonetim_talep"))
        musteri = Musteri.objects.get(hesaplar__hesap_no="100001")
        bos = {"deger": "", "ad": "", "kart_uid": "", "eposta": "", "telefon": "", "hedef_no": "", "hedef_turu": "", "_save": "Kaydet"}
        r = self.client.post("/admin/banka/yonetimtalebi/add/", {
            **bos, "tur": "KART_TANIMLA", "gerekce": "Müşteri yeni kart talep etti", "hedef_no": "100002", "kart_uid": "a0a0a003"})
        self.assertEqual(r.status_code, 302)
        r = self.client.post("/admin/banka/yonetimtalebi/add/", {
            **bos, "tur": "MUSTERI_TELEFON", "gerekce": "Müşteri numarasını değiştirdi", "hedef_no": str(musteri.id),
            "telefon": "+905559998877"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(sorted(YonetimTalebi.objects.values_list("tur", flat=True)), ["KART_TANIMLA", "MUSTERI_TELEFON"])
        self.assertFalse(Kart.objects.filter(uid="a0a0a003").exists())  # henuz uygulanmadi
        kotu = self.client.post("/admin/banka/yonetimtalebi/add/", {
            **bos, "tur": "KART_TANIMLA", "gerekce": "Müşteri yeni kart talep etti", "hedef_no": "100002", "kart_uid": "xyz"})
        self.assertEqual(kotu.status_code, 200)
        self.assertContains(kotu, "Kart UID")


# ====================================================================== PIN gizliligi (SMS saglayici)

@override_settings(MESAI_DISI_KURALI=False)
class SmsGizlilikTestleri(Yardimci, TestCase):
    def setUp(self):
        self.hazirla()
        self.U = get_user_model()

    def test_pin_log_veritabani_panel_ve_epostada_gorunmez(self):
        maker = self.U.objects.get(username="yonetim_talep")
        c1, c2 = (self.U.objects.get(username=f"yonetim_onay{i}") for i in (1, 2))
        t = yonetim_talepleri.talep_olustur(
            maker, "KART_PIN_SIFIRLA", {"kart_id": Kart.objects.get(uid="a0a0a001").id}, GEREKCE)
        yonetim_talepleri.oy_ver(t.id, c1, "ONAY")
        mail.outbox.clear()
        with self.assertLogs("banka", level="DEBUG") as kayit:
            yonetim_talepleri.oy_ver(t.id, c2, "ONAY")
        pin = self.son_pin("+905551000001")
        iz = f"PIN'iniz: {pin}"
        self.assertNotIn(iz, "\n".join(kayit.output))                                   # sunucu log'u
        self.assertFalse(any(iz in b.metin for b in Bildirim.objects.all()))             # bildirim tablosu / panel
        self.assertFalse(any(iz in l.veri for l in LogKaydi.objects.all()))              # denetim zinciri
        self.assertFalse(any(iz in m.body for m in mail.outbox))                         # e-posta
        self.assertNotIn(iz, YonetimTalebi.objects.get(id=t.id).veri)                    # talep
        self.assertIn(iz, [m for tel, m in sms.BellekSmsSaglayici.gelen if tel == "+905551000001"][-1])  # yalniz SMS'te

    def test_dosya_saglayici_ve_sms_kutusu_komutu(self):
        klasor = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, klasor, ignore_errors=True)
        yol = os.path.join(klasor, "kutu.jsonl")
        with override_settings(SMS_SAGLAYICI="banka.sms.DosyaSmsSaglayici", SMS_KUTUSU_DOSYASI=yol):
            sms.gonder("+905550000001", "Merhaba. Yeni PIN'iniz: 4821")
            sms.gonder("+905550000002", "Selam. Yeni PIN'iniz: 7305")
            self.assertEqual([m["telefon"] for m in sms.kutuyu_oku()], ["+905550000001", "+905550000002"])
            cikti = io.StringIO()
            call_command("sms_kutusu", "--telefon", "+905550000002", stdout=cikti)
            self.assertIn("7305", cikti.getvalue())
            self.assertNotIn("4821", cikti.getvalue())
            call_command("sms_kutusu", "--telefon", "+905550000001", "--temizle", stdout=io.StringIO())
            self.assertEqual([m["telefon"] for m in sms.kutuyu_oku()], ["+905550000002"])  # yalniz okunan silindi
            bos = io.StringIO()
            call_command("sms_kutusu", "--temizle", stdout=bos)
            call_command("sms_kutusu", stdout=bos)
            self.assertIn("Gelen kutusu boş", bos.getvalue())

    def test_bos_gelen_kutusu(self):
        with override_settings(SMS_KUTUSU_DOSYASI=os.path.join(tempfile.mkdtemp(), "yok.jsonl")):
            self.assertEqual(sms.kutuyu_oku(), [])


# ====================================================================== bloke butunlugu (log tabanli)

@override_settings(MESAI_DISI_KURALI=False)
class BlokeButunluguTestleri(Yardimci, TestCase):
    def setUp(self):
        self.hazirla()
        self.U = get_user_model()

    def blokele(self, uid="a0a0a001"):
        for _ in range(3):
            with self.assertRaises(IslemHatasi):
                services.musteri_giris(uid, "0000")

    def kart(self, uid="a0a0a001"):
        return Kart.objects.get(uid=uid)

    def kaldir_talebi(self, uid="a0a0a001"):
        maker = self.U.objects.get(username="yonetim_talep")
        return yonetim_talepleri.talep_olustur(maker, "BLOKE_KALDIR", {"hedef": "kart", "id": self.kart(uid).id}, GEREKCE)

    def test_sistem_blokesi_beklenen_durumdur_ve_sapma_yoktur(self):
        self.blokele()
        k = self.kart()
        self.assertTrue(bloke_butunlugu.beklenen_bloke_mu("kart", k.id, k.uid))
        self.assertFalse(bloke_butunlugu.beklenen_bloke_mu("kart", self.kart("a0a0a002").id, "a0a0a002"))
        self.assertEqual(bloke_butunlugu.sapmalar(), [])
        self.assertTrue(ledger.mutabakat()["saglam"])

    def test_veritabanindan_bloke_kaldirma_kapali_kalir_ve_yakalanir(self):
        self.blokele()
        Kart.objects.filter(uid="a0a0a001").update(bloke=False, hatali_deneme=0)  # dogrudan mudahale
        self.assertEqual(services.kart_tipi("a0a0a001"), "BLOKE")                # log bloke diyor: kapali kal
        with self.assertRaises(IslemHatasi) as e:
            services.musteri_giris("a0a0a001", "1234")                           # dogru PIN bile gecmez
        self.assertEqual(e.exception.kod, "bloke")
        self.assertEqual(LogKaydi.objects.filter(olay_turu="bloke_butunluk_ihlali").count(), 1)  # saatte bir
        rapor = ledger.mutabakat()
        self.assertFalse(rapor["saglam"])
        self.assertEqual([(b["uid"], b["veritabani"], b["beklenen"]) for b in rapor["bloke_sapmalari"]],
                         [("a0a0a001", False, True)])
        self.assertIn("butunluk_ihlali", {b["kural"] for b in anomali.tara()})
        self.assertTrue(zinciri_dogrula()["saglam"])
        self.assertTrue(services.musteri_giris("a0a0a002", "4321")["token"])     # baska musteri etkilenmez

    def test_yetkili_blokesi_icin_de_gecerli(self):
        for _ in range(3):
            with self.assertRaises(IslemHatasi):
                services.personel_dogrula("b0b0b001", "0000")
        Personel.objects.filter(kart_uid="b0b0b001").update(bloke=False, hatali_deneme=0)
        self.assertEqual(services.kart_tipi("b0b0b001"), "BLOKE")
        with self.assertRaises(IslemHatasi) as e:
            services.personel_dogrula("b0b0b001", "1111")
        self.assertEqual(e.exception.kod, "bloke")

    def test_onayli_talep_beklenen_durumu_gunceller(self):
        self.blokele()
        t = self.kaldir_talebi()
        c1 = self.U.objects.get(username="yonetim_onay1")
        self.assertEqual(yonetim_talepleri.oy_ver(t.id, c1, "ONAY").durum, "UYGULANDI")
        k = self.kart()
        self.assertFalse(bloke_butunlugu.beklenen_bloke_mu("kart", k.id, k.uid))
        self.assertEqual(bloke_butunlugu.sapmalar(), [])
        self.assertTrue(services.musteri_giris("a0a0a001", "1234")["token"])

    def test_onaysiz_kaldirildiktan_sonra_talep_acilabilir_kilitlenme_olmaz(self):
        self.blokele()
        Kart.objects.filter(uid="a0a0a001").update(bloke=False, hatali_deneme=0)  # veritabani "bloke degil" diyor
        t = self.kaldir_talebi()                                                   # ama log bloke diyor: talep acilabilmeli
        c1 = self.U.objects.get(username="yonetim_onay1")
        self.assertEqual(yonetim_talepleri.oy_ver(t.id, c1, "ONAY").durum, "UYGULANDI")
        self.assertEqual(bloke_butunlugu.sapmalar(), [])
        self.assertTrue(services.musteri_giris("a0a0a001", "1234")["token"])

    def test_veritabaninda_onaysiz_bloke_raporlanir(self):
        Kart.objects.filter(uid="a0a0a002").update(bloke=True)  # logda bloke olayi yok
        rapor = ledger.mutabakat()
        self.assertFalse(rapor["saglam"])
        self.assertEqual([(b["uid"], b["veritabani"], b["beklenen"]) for b in rapor["bloke_sapmalari"]],
                         [("a0a0a002", True, False)])

    def test_yeniden_bloke_ve_kimlikler_birbirini_etkilemez(self):
        self.blokele("a0a0a001")
        self.blokele("a0a0a002")
        c1 = self.U.objects.get(username="yonetim_onay1")
        yonetim_talepleri.oy_ver(self.kaldir_talebi("a0a0a001").id, c1, "ONAY")  # yalniz Ali'nin blokesi kalkar
        z = self.kart("a0a0a002")
        self.assertTrue(bloke_butunlugu.beklenen_bloke_mu("kart", z.id, z.uid))
        self.assertEqual(services.kart_tipi("a0a0a001"), "MUSTERI")
        self.assertEqual(services.kart_tipi("a0a0a002"), "BLOKE")
        self.blokele("a0a0a001")                                                  # Ali tekrar bloke olur
        a = self.kart("a0a0a001")
        self.assertTrue(bloke_butunlugu.beklenen_bloke_mu("kart", a.id, a.uid))   # son olay "bloke"
        self.assertEqual(bloke_butunlugu.sapmalar(), [])


# ====================================================================== kart muhru (olceklenebilir dogrulama)

@override_settings(MESAI_DISI_KURALI=False)
class KartMuhruTestleri(Yardimci, TestCase):
    def setUp(self):
        self.hazirla()
        self.U = get_user_model()

    def test_kartlar_muhurlu(self):
        kart = Kart.objects.select_related("hesap").get(uid="a0a0a001")
        self.assertRegex(kart.mac, r"^[0-9a-f]{64}$")
        self.assertTrue(kart_butunlugu.kart_dogrulandi_mi(kart))
        self.assertEqual(kart_butunlugu.dogrulanamayan_kartlar(), [])

    def test_log_boyutu_kart_sayisiyla_buyumez(self):
        ilk = len(LogKaydi.objects.filter(olay_turu="kart_goruntusu").last().veri)
        zeynep = Hesap.objects.get(hesap_no="100002")
        for i in range(40):
            kart = Kart(uid=f"c1c1{i:04x}", hesap=zeynep)
            kart.pin_ayarla("1234")
            kart.save()
        kart_butunlugu.baslangic("olcek testi")
        son = LogKaydi.objects.filter(olay_turu="kart_goruntusu").last()
        self.assertLess(len(son.veri) - ilk, 20)          # yalnizca 'adet' hanesi buyur
        self.assertNotIn("satirlar", son.veri)             # satir listesi loga yazilmaz
        self.assertEqual(json.loads(son.veri)["adet"], 42)

    def test_mac_anahtari_veritabaninda_degil(self):
        with override_settings(KART_MAC_ANAHTARI="baska-anahtar"):
            self.assertEqual(services.kart_tipi("a0a0a001"), "BLOKE")  # anahtari bilmeyen gecerli muhur uretemez
            self.assertEqual(kart_butunlugu.dogrulanamayan_kartlar(), ["a0a0a001", "a0a0a002"])
            self.assertFalse(ledger.mutabakat()["saglam"])

    def test_baska_kartin_pin_ve_muhrunu_kopyalamak_ise_yaramaz(self):
        ali = Kart.objects.get(uid="a0a0a001")
        kopya = Kart(uid="e0e0e002", hesap=ali.hesap, pin_hash=ali.pin_hash, mac=ali.mac)
        kopya.save()
        self.assertEqual(services.kart_tipi("e0e0e002"), "BLOKE")  # muhur uid'ye bagli
        self.assertEqual(kart_butunlugu.dogrulanamayan_kartlar(), ["e0e0e002"])

    def test_geri_alma_saldirisi_terminalde_degil_mutabakatta_yakalanir(self):
        eski = Kart.objects.get(uid="a0a0a001")
        eski_pin, eski_mac = eski.pin_hash, eski.mac
        maker = self.U.objects.get(username="yonetim_talep")
        c1, c2 = (self.U.objects.get(username=f"yonetim_onay{i}") for i in (1, 2))
        t = yonetim_talepleri.talep_olustur(maker, "KART_PIN_SIFIRLA", {"kart_id": eski.id}, GEREKCE)
        yonetim_talepleri.oy_ver(t.id, c1, "ONAY")
        yonetim_talepleri.oy_ver(t.id, c2, "ONAY")
        Kart.objects.filter(id=eski.id).update(pin_hash=eski_pin, mac=eski_mac)  # eski PIN + eski gecerli muhur
        self.assertEqual(services.kart_tipi("a0a0a001"), "MUSTERI")              # belgelenmis sinir: terminalde anlik engel yok
        rapor = ledger.mutabakat()
        self.assertEqual(rapor["kart"], "tutarsiz")                              # ama ozet farki yakalar
        self.assertFalse(rapor["saglam"])

    def test_eski_bicimli_goruntu_baslangic_sayilmaz_ve_kart_goruntu_al_muhurler(self):
        LogKaydi.objects.filter(olay_turu="kart_goruntusu").delete()
        kayit_ekle("kart_goruntusu", {"ozet": "eski", "satirlar": []})            # yukseltme oncesi bicim
        Kart.objects.update(mac="")                                                # henuz muhurlenmemis kartlar
        self.assertEqual(kart_butunlugu.durum(), "baslangic_yok")
        self.assertEqual(services.kart_tipi("a0a0a001"), "MUSTERI")               # yukseltmede musteriler kapanmaz
        call_command("kart_goruntu_al", stdout=io.StringIO())
        self.assertEqual(kart_butunlugu.durum(), "tutarli")
        self.assertFalse(Kart.objects.filter(mac="").exists())
        self.assertEqual(services.kart_tipi("a0a0a001"), "MUSTERI")

    def test_baslangic_olmadan_kart_talebi_uygulanmaz(self):
        LogKaydi.objects.filter(olay_turu="kart_goruntusu").delete()
        maker = self.U.objects.get(username="yonetim_talep")
        c1, c2 = (self.U.objects.get(username=f"yonetim_onay{i}") for i in (1, 2))
        t = yonetim_talepleri.talep_olustur(
            maker, "KART_PIN_SIFIRLA", {"kart_id": Kart.objects.get(uid="a0a0a001").id}, GEREKCE)
        yonetim_talepleri.oy_ver(t.id, c1, "ONAY")
        self.assertEqual(yonetim_talepleri.oy_ver(t.id, c2, "ONAY").durum, "BASARISIZ")
        self.assertTrue(services.musteri_giris("a0a0a001", "1234")["token"])      # PIN degismedi
        self.assertEqual(Bildirim.objects.filter(olay="pin_iletildi").count(), 0)
