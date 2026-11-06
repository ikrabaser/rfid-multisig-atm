import datetime
import sys

from django.core.management.base import BaseCommand

from banka.hashlog import zinciri_dogrula


class Command(BaseCommand):
    help = "Denetim zincirini baştan sona doğrular. Zincir bozuksa çıkış kodu 1 olur."

    def add_arguments(self, parser):
        parser.add_argument(
            "--beklenen-son-hash",
            help="Daha önce dışarı yazdığın son hash. Sondaki kayıtların silinip silinmediğini yakalar.",
        )
        parser.add_argument(
            "--demir",
            metavar="DOSYA",
            help="Doğrulama başarılıysa 'zaman, kayıt sayısı, son hash' satırını bu dosyaya ekler.",
        )

    def handle(self, *args, **opts):
        sonuc = zinciri_dogrula()
        if not sonuc["saglam"]:
            self.stderr.write(self.style.ERROR(
                f"ZİNCİR BOZUK: {sonuc['bozuk_sira']}. kayıtta. {sonuc['sebep']}"
            ))
            sys.exit(1)

        self.stdout.write(self.style.SUCCESS(
            f"Zincir sağlam. {sonuc['kayit_sayisi']} kayıt. Son hash: {sonuc['son_hash']}"
        ))

        beklenen = opts.get("beklenen_son_hash")
        if beklenen and beklenen.strip() != sonuc["son_hash"]:
            self.stderr.write(self.style.ERROR(
                "Son hash beklenenle uyuşmuyor: sondaki kayıtlar silinmiş ya da değiştirilmiş olabilir."
            ))
            sys.exit(1)

        if opts.get("demir"):
            an = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
            with open(opts["demir"], "a", encoding="utf-8") as f:
                f.write(f"{an},{sonuc['kayit_sayisi']},{sonuc['son_hash']}\n")
            self.stdout.write(f"Son hash {opts['demir']} dosyasına eklendi.")
