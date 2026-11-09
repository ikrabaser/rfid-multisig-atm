import sys

from django.core.management.base import BaseCommand

from banka import kart_butunlugu


class Command(BaseCommand):
    help = ("Tüm müşteri kartlarını mühürler ve şu anki halini ONAYLI sürüm olarak hash zincirine yazar (ilk kurulum için). "
            "Kartlar son onaylı sürümden farklıysa, bunu meşrulaştırmamak için --zorla ister.")

    def add_arguments(self, parser):
        parser.add_argument("--zorla", action="store_true",
                            help="Kartlar son onaylı sürümden FARKLI olsa bile yeni görüntü yaz (denetim kaydına işlenir)")

    def handle(self, *args, **opts):
        durum = kart_butunlugu.tutarli_mi()
        if durum is True:
            self.stdout.write("Kart kayıtları zaten son onaylı sürümle aynı; yeni görüntü yazılmadı.")
            return
        if durum is False and not opts["zorla"]:
            self.stderr.write(self.style.ERROR(
                "Kart kayıtları son onaylı sürümden FARKLI (onaysız kart/PIN değişikliği olabilir). "
                "İncelemeden önce üzerine yazma. Bilerek yapacaksan --zorla ver."))
            sys.exit(1)
        kart_butunlugu.baslangic("kart_goruntu_al" + (" --zorla" if durum is False else ""))
        self.stdout.write(self.style.SUCCESS("Kartlar mühürlendi, görüntü onaylı sürüm olarak kaydedildi."))
