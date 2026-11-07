import sys

from django.core.management.base import BaseCommand

from banka import politika_degisikligi


class Command(BaseCommand):
    help = ("Politika tablosunun şu anki halini ONAYLI sürüm olarak hash zincirine yazar (ilk kurulum için). "
            "Tablo son onaylı sürümden farklıysa, bunu meşrulaştırmamak için --zorla ister.")

    def add_arguments(self, parser):
        parser.add_argument("--zorla", action="store_true",
                            help="Tablo son onaylı sürümden FARKLI olsa bile yeni görüntü yaz (denetim kaydına işlenir)")

    def handle(self, *args, **opts):
        durum = politika_degisikligi.goruntu_tutarli_mi()
        if durum is True:
            self.stdout.write("Politika tablosu zaten son onaylı sürümle aynı; yeni görüntü yazılmadı.")
            return
        if durum is False and not opts["zorla"]:
            self.stderr.write(self.style.ERROR(
                "Politika tablosu son onaylı sürümden FARKLI (onaysız değişiklik olabilir). "
                "İncelemeden önce üzerine yazma. Bilerek yapacaksan --zorla ver."))
            sys.exit(1)
        neden = "politika_goruntu_al" + (" --zorla" if durum is False else "")
        politika_degisikligi.goruntu_kaydet(neden)
        self.stdout.write(self.style.SUCCESS("Politika görüntüsü onaylı sürüm olarak kaydedildi."))
