"""wokwi_kartlari komutu: Wokwi'nin sabit UID'li hazır kartları demo yetkili olur; onay akışı gömülü yazılımla çalışır."""
import io
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings

from . import personel_butunlugu
from .models import OnayTalebi, Personel, Rol
from .test_gomulu_uctan_uca import SIM_HATASI, DjangoKopru, cerceve, yt
from .tests import Yardimci


def komut(*args):
    cikti = io.StringIO()
    call_command("wokwi_kartlari", *args, stdout=cikti)
    return cikti.getvalue()


@override_settings(MESAI_DISI_KURALI=False)
class WokwiKartlariTestleri(Yardimci, TestCase):
    def setUp(self):
        self.hazirla()

    def test_debug_kapaliyken_zorla_olmadan_calismaz(self):
        with override_settings(DEBUG=False), self.assertRaises(CommandError):
            komut()
        self.assertFalse(Personel.objects.filter(kart_uid="11223344").exists())

    def test_dort_yetkili_dogru_rol_ve_pin_ile_eklenir(self):
        komut("--zorla")
        beklenen = {"11223344": (Rol.GOREVLI, "1010"), "55667788": (Rol.GOREVLI, "2020"),
                    "aabbccdd": (Rol.MUDUR_YRD, "3030"), "c0ffee99": (Rol.MUDUR, "4040")}
        for uid, (rol, pin) in beklenen.items():
            p = Personel.objects.get(kart_uid=uid)
            self.assertEqual(p.rol, rol)
            self.assertTrue(p.pin_dogru_mu(pin))
        self.assertFalse(Personel.objects.filter(kart_uid="04112233445566").exists())   # NFC Tag bilerek kayıtsız

    def test_tekrar_calistirmak_kayit_ve_onayli_goruntu_eklemez(self):
        komut("--zorla")
        adet = Personel.objects.count()
        onceki = personel_butunlugu.son_onayli_ozet()
        self.assertIn("zaten kayıtlı", komut("--zorla"))
        self.assertEqual(Personel.objects.count(), adet)
        self.assertEqual(personel_butunlugu.son_onayli_ozet(), onceki)

    def test_yetkili_butunlugu_komuttan_sonra_tutarli(self):
        self.assertTrue(personel_butunlugu.tutarli_mi())
        komut("--zorla")
        self.assertTrue(personel_butunlugu.tutarli_mi())    # onaysız değişiklik alarmı çıkmamalı


@unittest.skipIf(SIM_HATASI, SIM_HATASI)
@override_settings(MESAI_DISI_KURALI=False)
class WokwiKartlariFirmwareTestleri(Yardimci, TestCase):
    """Gerçek firmware çekirdeği + gerçek köprü + Django: Wokwi kartlarıyla onay."""

    def setUp(self):
        self.hazirla()
        komut("--zorla")
        gecici = tempfile.TemporaryDirectory()
        self.addCleanup(gecici.cleanup)
        kopru = DjangoKopru(Client(), Path(gecici.name) / "kuyruk.jsonl")
        self.oturum = cerceve.Oturum(kopru.isle, kati=True)
        self.t = yt.YazilimTerminali(self.oturum.satir_isle)
        self.addCleanup(self.t.kapat)
        self.t.ilerle(2500)

    def kart_pin(self, uid, pin, karar=None):
        t = self.t
        t.kart_okut(uid)
        if karar:
            self.assertTrue(t.durum_bekle("KARAR_AL", 12000), t.lcd)
            t.tikla(karar)
        self.assertTrue(t.durum_bekle("PIN_AL", 8000), t.lcd)
        t.yaz(pin)

    def test_yesil_kart_onayiyla_10000_tl_cekim(self):
        t = self.t
        self.kart_pin("a0a0a001", "1234")                      # Blue Card = Ali
        self.assertTrue(t.durum_bekle("MENU", 20000), t.lcd)
        t.tikla("B")
        t.yaz("10000")
        t.tikla("#")
        self.assertTrue(t.durum_bekle("ONAY_IZLE", 12000), t.lcd)
        t.ilerle(2500)
        self.kart_pin("11223344", "1010", karar="A")           # Green Card = Wokwi Yeşil (görevli)
        self.assertTrue(t.durum_bekle("PARA_BEKLE", 15000), t.lcd)
        t.tikla("A")
        self.assertTrue(t.ekran_bekle("Islem onaylandi", 12000), t.lcd)
        self.assertEqual(self.yenile(self.ali), Decimal("990000.00"))
        self.assertEqual(OnayTalebi.objects.get().durum, "UYGULANDI")

    def test_nfc_tag_7_baytlik_uid_tanimlanmayan_kart_olarak_reddedilir(self):
        t = self.t
        t.kart_okut("04112233445566")
        self.assertFalse(t.durum_bekle("PIN_AL", 6000), t.lcd)   # kayıtsız kart PIN ekranına ilerlemez
        self.assertEqual(t.istatistik_al()["ihlal"], 0)
