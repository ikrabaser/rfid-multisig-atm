import sys

from django.core.management.base import BaseCommand

from banka import personel_butunlugu


class Command(BaseCommand):
    help = ("Yetkili (Personel) kümesinin şu anki halini ONAYLI sürüm olarak hash zincirine yazar (ilk kurulum için). "
            "Küme son onaylı sürümden farklıysa, bunu meşrulaştırmamak için --zorla ister.")

    def add_arguments(self, parser):
        parser.add_argument("--zorla", action="store_true",
                            help="Küme son onaylı sürümden FARKLI olsa bile yeni görüntü yaz (denetim kaydına işlenir)")

    def handle(self, *args, **opts):
        durum = personel_butunlugu.tutarli_mi()
        if durum is True:
            self.stdout.write("Yetkili kayıtları zaten son onaylı sürümle aynı; yeni görüntü yazılmadı.")
            return
        if durum is False and not opts["zorla"]:
            self.stderr.write(self.style.ERROR(
                "Yetkili kayıtları son onaylı sürümden FARKLI (onaysız değişiklik olabilir). "
                "İncelemeden önce üzerine yazma. Bilerek yapacaksan --zorla ver."))
            sys.exit(1)
        personel_butunlugu.goruntu_kaydet("personel_goruntu_al" + (" --zorla" if durum is False else ""))
        self.stdout.write(self.style.SUCCESS("Yetkili görüntüsü onaylı sürüm olarak kaydedildi."))
