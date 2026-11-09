from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from banka import services
from banka.services import IslemHatasi


class Command(BaseCommand):
    help = ("Kasetteki banknot sayısını fiziksel sayım/dolum sonrasına ayarlar. Kim yaptığı hash zincirine yazılır. "
            "Örnek: python manage.py kaset_doldur --terminal ATM-01 --nominal 200 --adet 3000 --yapan \"Ali Teknisyen\"")

    def add_arguments(self, parser):
        parser.add_argument("--terminal", default=settings.VARSAYILAN_TERMINAL)
        parser.add_argument("--nominal", type=int, required=True, help="Banknot değeri (TL)")
        parser.add_argument("--adet", type=int, required=True, help="Sayım sonrası kasetteki banknot sayısı")
        parser.add_argument("--yapan", required=True, help="Sayımı/dolumu yapan kişi")
        parser.add_argument("--iade-kutusunu-bosalt", action="store_true",
                            help="Geri çekilen/sıkışan banknot kutusu boşaltıldı (sayacı sıfırlar)")

    def handle(self, *args, **o):
        try:
            r = services.kaset_doldur(o["terminal"], o["nominal"], o["adet"], o["yapan"], o["iade_kutusunu_bosalt"])
        except IslemHatasi as e:
            raise CommandError(e.mesaj)
        self.stdout.write(self.style.SUCCESS(
            f"{r['terminal']} {r['nominal']} TL kaseti: {r['eski_adet']} -> {r['yeni_adet']} banknot."))
