from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from banka.models import Hesap, Kart, Musteri, Personel, Politika, Rol

# DEMO verisi. UID'ler sahte; admin panelinden kendi kartlarının UID'si ile değiştir.
MUSTERILER = [
    # ad, hesap_no, bakiye, kart uid, pin
    ("Ali Yılmaz", "100001", "1000000.00", "a0a0a001", "1234"),
    ("Zeynep Kaya", "100002", "250000.00", "a0a0a002", "4321"),
]
PERSONEL = [
    # ad, uid, rol, pin
    ("Ayşe (görevli)", "b0b0b001", Rol.GOREVLI, "1111"),
    ("Burak (görevli)", "b0b0b002", Rol.GOREVLI, "2222"),
    ("Cem (müdür yrd.)", "b0b0b003", Rol.MUDUR_YRD, "3333"),
    ("Deniz (müdür yrd.)", "b0b0b004", Rol.MUDUR_YRD, "4444"),
    ("Elif (müdür)", "b0b0b005", Rol.MUDUR, "5555"),
]
POLITIKALAR = [
    # alt, ust, onay, en az rol, aciklama
    ("0", "5000", 0, Rol.GOREVLI, "Küçük işlem: onaysız"),
    ("5000", "50000", 1, Rol.GOREVLI, "1 yetkili"),
    ("50000", "500000", 2, Rol.MUDUR_YRD, "2 yetkili, en az biri müdür yardımcısı"),
    ("500000", None, 3, Rol.MUDUR, "3 yetkili, en az biri müdür"),
]


class Command(BaseCommand):
    help = "Demo müşteri, personel ve politika verisini oluşturur (varsa dokunmaz)."

    @transaction.atomic
    def handle(self, *args, **opts):
        for ad, hesap_no, bakiye, uid, pin in MUSTERILER:
            if Kart.objects.filter(uid=uid).exists() or Hesap.objects.filter(hesap_no=hesap_no).exists():
                continue
            musteri = Musteri.objects.create(ad=ad)
            hesap = Hesap.objects.create(musteri=musteri, hesap_no=hesap_no, bakiye=Decimal(bakiye))
            kart = Kart(uid=uid, hesap=hesap)
            kart.pin_ayarla(pin)
            kart.save()
            self.stdout.write(f"Müşteri: {ad}  hesap {hesap_no}  kart {uid}  PIN {pin}")

        for ad, uid, rol, pin in PERSONEL:
            if Personel.objects.filter(kart_uid=uid).exists():
                continue
            p = Personel(ad=ad, kart_uid=uid, rol=rol)
            p.pin_ayarla(pin)
            p.save()
            self.stdout.write(f"Yetkili: {ad}  kart {uid}  PIN {pin}")

        if not Politika.objects.exists():
            for alt, ust, onay, rol, aciklama in POLITIKALAR:
                Politika.objects.create(
                    alt_limit=Decimal(alt),
                    ust_limit=None if ust is None else Decimal(ust),
                    gereken_onay=onay, en_az_rol=rol, aciklama=aciklama,
                )
            self.stdout.write("Politikalar oluşturuldu.")

        self.stdout.write(self.style.SUCCESS("Demo verisi hazır. PIN'ler demo içindir, gerçek kullanımda değiştir."))
