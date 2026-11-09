from django.core.management.base import BaseCommand, CommandError

from banka import services
from banka.services import IslemHatasi


class Command(BaseCommand):
    help = ("Teslimi BELİRSİZ kalan nakit çekmeyi, kasetler sayıldıktan sonra sonuçlandırır. Para otomatik iade "
            "edilmez (müşteri parayı almış olabilir). Örnek: python manage.py nakit_coz --islem 12 --sonuc VERILMEDI "
            "--yapan \"Ali Teknisyen\" --neden \"Kaset sayimi: banknot yerinde\"")

    def add_arguments(self, parser):
        parser.add_argument("--islem", type=int, required=True)
        parser.add_argument("--sonuc", required=True, choices=["VERILDI", "VERILMEDI", "KISMI"],
                            help="VERILDI: para müşteriye gitti | VERILMEDI: hiç dağıtılmadı (iade) | KISMI: eksik verildi (fark iade)")
        parser.add_argument("--verilen", default="", help="KISMI için fiilen verilen banknotlar, örn. 200x8")
        parser.add_argument("--yapan", required=True)
        parser.add_argument("--neden", required=True, help="Sayım sonucu/gerekçe (en az 5 karakter)")

    def handle(self, *args, **o):
        try:
            r = services.nakit_coz(o["islem"], o["sonuc"], o["yapan"], o["neden"], o["verilen"])
        except IslemHatasi as e:
            raise CommandError(e.mesaj)
        self.stdout.write(self.style.SUCCESS(
            f"İşlem #{r['islem_id']} sonuçlandı: {r['teslim']} (iade edilen: {r['iade']} TL)."))
