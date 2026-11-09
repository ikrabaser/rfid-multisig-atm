from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from banka import services
from banka.services import IslemHatasi


class Command(BaseCommand):
    help = ("Sıkışma/eksik verme sonrası kapanan para dağıtıcıyı, teknik servis kontrolünden sonra yeniden açar. "
            "Önce kasetleri sayın (kaset_doldur).")

    def add_arguments(self, parser):
        parser.add_argument("--terminal", default=settings.VARSAYILAN_TERMINAL)
        parser.add_argument("--yapan", required=True)
        parser.add_argument("--neden", required=True, help="Servis notu (en az 5 karakter)")

    def handle(self, *args, **o):
        try:
            r = services.dagitici_ac(o["terminal"], o["yapan"], o["neden"])
        except IslemHatasi as e:
            raise CommandError(e.mesaj)
        self.stdout.write(self.style.SUCCESS(f"{r['terminal']} para dağıtıcısı açıldı."))
