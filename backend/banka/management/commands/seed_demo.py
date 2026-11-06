from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from banka.models import Hesap, Kart, Musteri, Personel, Politika, Rol, Terminal

# DEMO verisi. UID'ler sahte; admin panelinden kendi kartlarının UID'si ile değiştir.
MUSTERILER = [
    # ad, hesap_no, bakiye, kart uid, pin
    ("Ali Yılmaz", "100001", "1000000.00", "a0a0a001", "1234"),
    ("Zeynep Kaya", "100002", "250000.00", "a0a0a002", "4321"),
]
PERSONEL = [
    # ad, uid, rol, pin, e-posta, telefon  (iletisim bilgileri sahte; .example adresleri gercek degildir)
    ("Ayşe (görevli)", "b0b0b001", Rol.GOREVLI, "1111", "ayse@banka.example", "+905550000001"),
    ("Burak (görevli)", "b0b0b002", Rol.GOREVLI, "2222", "burak@banka.example", "+905550000002"),
    ("Cem (müdür yrd.)", "b0b0b003", Rol.MUDUR_YRD, "3333", "cem@banka.example", "+905550000003"),
    ("Deniz (müdür yrd.)", "b0b0b004", Rol.MUDUR_YRD, "4444", "deniz@banka.example", "+905550000004"),
    ("Elif (müdür)", "b0b0b005", Rol.MUDUR, "5555", "elif@banka.example", "+905550000005"),
]
TERMINALLER = [
    # kod, ad, enlem, boylam  (Ankara ile Istanbul arasi ~350 km: imkansiz seyahat demosu icin)
    ("ATM-01", "Merkez Şube", 39.9334, 32.8597),
    ("ATM-02", "İstanbul Şubesi", 41.0082, 28.9784),
]
KASA_HESAP_NO = "900000"
KASA_NAKIT = "10000000.00"
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
        if not Hesap.objects.filter(tur=Hesap.Tur.KASA).exists():
            banka = Musteri.objects.create(ad="Banka Nakit Kasası")
            Hesap.objects.create(
                musteri=banka, hesap_no=KASA_HESAP_NO, tur=Hesap.Tur.KASA,
                acilis_bakiyesi=Decimal(KASA_NAKIT), bakiye=Decimal(KASA_NAKIT),
            )
            self.stdout.write(f"Nakit kasa hesabı: {KASA_HESAP_NO}  bakiye {KASA_NAKIT}")

        for ad, hesap_no, bakiye, uid, pin in MUSTERILER:
            if Kart.objects.filter(uid=uid).exists() or Hesap.objects.filter(hesap_no=hesap_no).exists():
                continue
            musteri = Musteri.objects.create(ad=ad)
            hesap = Hesap.objects.create(
                musteri=musteri, hesap_no=hesap_no, acilis_bakiyesi=Decimal(bakiye), bakiye=Decimal(bakiye)
            )
            kart = Kart(uid=uid, hesap=hesap)
            kart.pin_ayarla(pin)
            kart.save()
            self.stdout.write(f"Müşteri: {ad}  hesap {hesap_no}  kart {uid}  PIN {pin}")

        for kod, ad, enlem, boylam in TERMINALLER:
            Terminal.objects.get_or_create(kod=kod, defaults={"ad": ad, "enlem": enlem, "boylam": boylam})

        for ad, uid, rol, pin, eposta, telefon in PERSONEL:
            mevcut = Personel.objects.filter(kart_uid=uid)
            if mevcut.exists():
                mevcut.filter(eposta="", telefon="").update(eposta=eposta, telefon=telefon)
                continue
            p = Personel(ad=ad, kart_uid=uid, rol=rol, eposta=eposta, telefon=telefon)
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
