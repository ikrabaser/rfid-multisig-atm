"""Wokwi'nin hazır MFRC522 kartlarını demo yetkililer olarak kaydeder.

Wokwi'deki RC522 parçasında yalnızca Blue Card'ın UID'si değiştirilebilir (diagram.json: a0:a0:a0:01 = demo müşteri Ali).
Diğer hazır kartların UID'leri sabittir; bu komut onları demo yetkililere bağlar, böylece onay akışı Wokwi'de de denenir:

    Green Card   11:22:33:44   Wokwi Yeşil (görevli)             PIN 1010
    Yellow Card  55:66:77:88   Wokwi Sarı (görevli)              PIN 2020
    Red Card     AA:BB:CC:DD   Wokwi Kırmızı (müdür yrd.)        PIN 3030
    Key Fob      C0:FF:EE:99   Wokwi Anahtarlık (müdür)          PIN 4040
    NFC Tag      04:11:22:33:44:55:66  KAYITSIZ (tanınmayan, 7 baytlık UID testi için bilerek bırakıldı)

Yalnızca demo içindir: yetkili eklemek normalde maker-checker ister; bu komut onu atlar ve onaylı yetkili görüntüsünü yeniler.
DEBUG kapalıyken --zorla gerekir.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from banka import personel_butunlugu
from banka.models import Personel, Rol

WOKWI_YETKILILER = [
    # ad, uid, rol, pin
    ("Wokwi Yeşil (görevli)", "11223344", Rol.GOREVLI, "1010"),
    ("Wokwi Sarı (görevli)", "55667788", Rol.GOREVLI, "2020"),
    ("Wokwi Kırmızı (müdür yrd.)", "aabbccdd", Rol.MUDUR_YRD, "3030"),
    ("Wokwi Anahtarlık (müdür)", "c0ffee99", Rol.MUDUR, "4040"),
]


class Command(BaseCommand):
    help = "Wokwi'nin hazır RC522 kartlarını demo yetkili olarak kaydeder (yalnızca demo)."

    def add_arguments(self, parser):
        parser.add_argument("--zorla", action="store_true", help="DEBUG kapalıyken de çalıştır")

    @transaction.atomic
    def handle(self, *args, **opts):
        if not settings.DEBUG and not opts["zorla"]:
            raise CommandError("Bu komut yalnızca demo içindir (maker-checker'ı atlar). DEBUG kapalı; bilerek çalıştıracaksan --zorla ver.")
        eklenen = 0
        for ad, uid, rol, pin in WOKWI_YETKILILER:
            if Personel.objects.filter(kart_uid=uid).exists():
                continue
            p = Personel(ad=ad, kart_uid=uid, rol=rol)
            p.pin_ayarla(pin)
            p.save()
            eklenen += 1
            self.stdout.write(f"Yetkili: {ad}  kart {uid}  PIN {pin}")
        if eklenen:
            personel_butunlugu.goruntu_kaydet("wokwi_kartlari")
            self.stdout.write("Yetkili kayıtları için onaylı görüntü yenilendi.")
        else:
            self.stdout.write("Wokwi kartları zaten kayıtlı.")
