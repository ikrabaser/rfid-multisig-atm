"""Terminal heartbeat'lerini tarar; süresi dolan terminal için arıza vakası açar (izleme.py).

Tek seferlik:  python manage.py terminal_izle
Sürekli:       python manage.py terminal_izle --surekli --aralik 10   (üretimde sistem zamanlayıcısı/servis olarak çalıştırın)
"""
import time

from django.core.management.base import BaseCommand

from banka import izleme
from banka.models import ArizaVakasi, Terminal


class Command(BaseCommand):
    help = "Çevrimdışı terminalleri tarar ve arıza vakası açar"

    def add_arguments(self, parser):
        parser.add_argument("--surekli", action="store_true", help="Ctrl+C'ye kadar tekrarla")
        parser.add_argument("--aralik", type=int, default=10, help="Sürekli modda tarama aralığı (sn)")

    def handle(self, *args, **o):
        while True:
            for v in izleme.tara():
                self.stdout.write(self.style.WARNING(
                    f"{v.terminal.kod} çevrimdışı: vaka #{v.id}, atanan: {v.atanan.ad if v.atanan else '-'}"))
            if not o["surekli"]:
                durumlar = [(t.kod, izleme.baglanti_durumu(t)["durum"]) for t in Terminal.objects.filter(aktif=True)]
                self.stdout.write("Terminaller: " + ", ".join(f"{k}={d}" for k, d in durumlar))
                self.stdout.write(f"Açık vaka: {ArizaVakasi.objects.filter(durum=ArizaVakasi.Durum.ACIK).count()}")
                return
            time.sleep(max(1, o["aralik"]))
