from django.core.management.base import BaseCommand

from banka import sms


class Command(BaseCommand):
    help = ("Simülasyon SMS gelen kutusunu gösterir: yetkililere ve müşterilere giden PIN mesajları burada görünür "
            "(sunucu log'unda, veritabanında ve panelde görünmez). Gerçek SMS sağlayıcısında bu komut gerekmez.")

    def add_arguments(self, parser):
        parser.add_argument("--telefon", help="Yalnızca bu numaraya giden mesajlar")
        parser.add_argument("--temizle", action="store_true", help="Gösterilen mesajları okunduktan sonra sil")

    def handle(self, *args, **opts):
        mesajlar = sms.kutuyu_oku(opts.get("telefon"), temizle=opts["temizle"])
        if not mesajlar:
            self.stdout.write("Gelen kutusu boş.")
            return
        for m in mesajlar:
            self.stdout.write(f"[{m['zaman']}] {m['telefon']}: {m['metin']}")
        if opts["temizle"]:
            self.stdout.write(f"{len(mesajlar)} mesaj silindi.")
