import datetime
import io
import json
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from . import anomali, ledger, services
from .hashlog import kayit_ekle, zinciri_dogrula
from .models import Hesap, HesapHareketi, Islem, Kart, LogKaydi, OnayTalebi, Personel, Rol
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
