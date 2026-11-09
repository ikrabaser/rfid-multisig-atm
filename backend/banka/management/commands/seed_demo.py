import secrets
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction

from banka import personel_butunlugu, politika_degisikligi, rbac
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


# Yonetim (admin) kullanicilari: rol -> grup eslemesi rbac.py'deki tanimlardan gelir.
# Maker-checker icin talep edenler ve onaylayanlar FARKLI kisilerdir (gorev ayrimi); 3 onay gerektiren
# islemler (ornegin mudur ekleme) icin 3 onaylayan tanimlidir.
YONETIM_KULLANICILARI = [
    ("politika_talep", "Politika Talep Eden"),
    ("politika_onay1", "Politika Onaylayan"),
    ("politika_onay2", "Politika Onaylayan"),
    ("yonetim_talep", "Yönetim Talep Eden"),
    ("yonetim_onay1", "Yönetim Onaylayan"),
    ("yonetim_onay2", "Yönetim Onaylayan"),
    ("yonetim_onay3", "Yönetim Onaylayan"),
    ("denetci", "Denetçi"),
]


class Command(BaseCommand):
    help = "Demo müşteri, personel ve politika verisini oluşturur (varsa dokunmaz)."

    def _yonetim_kullanicilari(self):
        rbac.kur()
        Kullanici = get_user_model()
        for ad, grup_adi in YONETIM_KULLANICILARI:
            if Kullanici.objects.filter(username=ad).exists():
                continue
            sifre = secrets.token_urlsafe(9)
            kullanici = Kullanici.objects.create_user(ad, email=f"{ad}@banka.example", password=sifre, is_staff=True)
            kullanici.groups.add(Group.objects.get(name=grup_adi))
            self.stdout.write(f"Yönetim kullanıcısı: {ad}  şifre {sifre}  ({grup_adi})")

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

        if politika_degisikligi.goruntu_tutarli_mi() is None:
            politika_degisikligi.goruntu_kaydet("seed_demo")
            self.stdout.write("Politika onaylı başlangıç görüntüsü kaydedildi.")
        if personel_butunlugu.tutarli_mi() is None:
            personel_butunlugu.goruntu_kaydet("seed_demo")
            self.stdout.write("Yetkili kayıtları için onaylı başlangıç görüntüsü kaydedildi.")
        self._yonetim_kullanicilari()

        self.stdout.write(self.style.SUCCESS("Demo verisi hazır. PIN'ler demo içindir, gerçek kullanımda değiştir."))
