"""Simülasyon görevlerini çalıştıran Procrastinate worker'ı (ayrı bir terminalde açık kalır)."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Banka operasyon simülasyonu için Procrastinate worker'ını çalıştırır (PostgreSQL gerekir)."

    def add_arguments(self, parser):
        parser.add_argument("--bir-kez", action="store_true", help="Kuyruk boşalınca çık (bekleme)")

    def handle(self, *args, **opts):
        if settings.PROCRASTINATE_BAGLANTI == "memory":
            raise CommandError("Kuyruk bellekte (SQLite ya da test): worker anlamsız. PostgreSQL kullan.")
        import asyncio
        import sys
        import warnings
        if sys.platform == "win32":
            # Windows'un varsayılan Proactor döngüsünü psycopg'un async modu kullanamaz (Procrastinate worker'ı async çalışır)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        from banka.operasyon import gorevler
        if gorevler.sema_kur():
            self.stdout.write("Procrastinate şeması kuruldu.")
        self.stdout.write(f"Worker başladı (kuyruk: {gorevler.KUYRUK}). Durdurmak için Ctrl+C.")
        gorevler.app.run_worker(queues=[gorevler.KUYRUK], wait=not opts["bir_kez"])
