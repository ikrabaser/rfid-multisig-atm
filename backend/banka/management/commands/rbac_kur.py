from django.core.management.base import BaseCommand

from banka import rbac


class Command(BaseCommand):
    help = "rbac.py'deki rol (grup) tanımlarını veritabanına uygular (idempotent). Grup izinleri koddan gelir."

    def handle(self, *args, **opts):
        for ad in rbac.kur():
            self.stdout.write(f"Grup güncellendi: {ad}")
        self.stdout.write(self.style.SUCCESS("RBAC grupları uygulandı."))
