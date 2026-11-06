import sys

from django.core.management.base import BaseCommand

from banka import anomali


class Command(BaseCommand):
    help = "Denetim logunu anomali kurallarıyla tarar. Yüksek ciddiyette bulgu varsa çıkış kodu 1 olur."

    def add_arguments(self, parser):
        parser.add_argument("--saat", type=int, default=24, help="Kaç saat geriye bakılsın (varsayılan 24)")

    def handle(self, *args, **opts):
        bulgular = anomali.tara(saat=opts["saat"])
        if not bulgular:
            self.stdout.write(self.style.SUCCESS(f"Son {opts['saat']} saatte anomali bulunamadı."))
            return
        for b in bulgular:
            satir = f"[{b['ciddiyet'].upper():6}] {b['kural']}: {b['aciklama']}"
            stil = self.style.ERROR if b["ciddiyet"] == "yuksek" else self.style.WARNING
            self.stdout.write(stil(satir))
        if any(b["ciddiyet"] == "yuksek" for b in bulgular):
            sys.exit(1)
