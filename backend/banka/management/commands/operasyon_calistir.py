"""Sandbox veritabanında sanal müşteri yükünü çalıştırır; ilerlemeyi stdout'a JSON satırları olarak yazar.

Bu komut doğrudan ÇALIŞTIRILMAK için değil, yürütücü (operasyon/yurutucu.py) tarafından ALT SÜREÇ olarak başlatılır; ortamında
veritabanı sandbox'a yönlendirilmiştir. Elle denemek için:  python manage.py operasyon_calistir --kendi-veritabanimda ...
(UYARI: bu, geçerli veritabanına sanal müşteri ve işlem yazar; yalnızca boş/deneme veritabanında kullan.)
"""
import json
import sys

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from banka.operasyon import veri
from banka.operasyon.kosucu import Kosucu
from banka.operasyon.parametreler import ParametreHatasi, Parametreler


def _yaz(olay):
    sys.stdout.write(json.dumps(olay, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


class Command(BaseCommand):
    help = "Sanal müşteri yükünü sandbox veritabanında çalıştırır (yürütücü alt süreci)."

    def add_arguments(self, parser):
        parser.add_argument("--parametreler", default="{}", help="JSON sözlüğü")
        parser.add_argument("--kendi-veritabanimda", action="store_true",
                            help="Geçerli veritabanında çalıştır (sandbox'a yönlendirilmemiş olsa da)")

    def handle(self, *args, **opts):
        from banka.operasyon import sandbox
        from banka.models import Musteri
        try:
            p = Parametreler.al(json.loads(opts["parametreler"]))
        except (ValueError, ParametreHatasi) as e:
            raise CommandError(str(e))
        if not opts["kendi_veritabanimda"] and not sandbox.sandbox_ortaminda_mi():
            raise CommandError("Bu komut sandbox'a yönlendirilmiş ortamda çalışmalıdır (ya da --kendi-veritabanimda ver)")
        _yaz({"tur": "asama", "asama": "migrate"})
        call_command("migrate", verbosity=0, interactive=False)
        if Musteri.objects.exists() and not opts["kendi_veritabanimda"]:
            raise CommandError("Sandbox boş değil; yürütücü her koşuda yeni sandbox hazırlamalıdır")
        _yaz({"tur": "asama", "asama": "veri"})
        dunya = veri.kur(p)
        _yaz({"tur": "asama", "asama": "yuk", "musteri": len(dunya.musteriler), "terminal": len(dunya.terminaller)})
        ozet = Kosucu(p, dunya, yayin=_yaz).calistir()
        _yaz({"tur": "bitti", "ozet": ozet})
