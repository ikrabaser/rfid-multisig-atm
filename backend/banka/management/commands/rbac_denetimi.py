import sys

from django.core.management.base import BaseCommand

from banka import rbac


class Command(BaseCommand):
    help = ("Yönetim kullanıcılarının yetkilerini görev ayrımı (SoD) kurallarına göre denetler: aynı kişi hem "
            "talep eden hem onaylayan olamaz, denetçi hiçbir şeyi değiştiremez. İhlalde çıkış kodu 1.")

    def handle(self, *args, **opts):
        r = rbac.denetim()
        w = self.stdout.write
        w("Yönetim kullanıcıları:")
        for k in r["kullanicilar"]:
            etiket = "superuser" if k["superuser"] else ", ".join(k["gruplar"]) or "(grupsuz)"
            w(f"  {k['kullanici']:18} {etiket}")

        sorunlar = []
        for i in r["gorev_ayrimi_ihlalleri"]:
            sorunlar.append(f"Görev ayrımı ihlali: {i['kullanici']} hem {i['izinler'][0]} hem {i['izinler'][1]} iznine sahip")
        for i in r["denetci_yazma_izinleri"]:
            sorunlar.append(f"Denetçi değiştirme iznine sahip: {i['kullanici']} {i['izinler']}")
        if r["eksik_gruplar"]:
            sorunlar.append(f"Eksik gruplar: {r['eksik_gruplar']} (python manage.py rbac_kur)")

        if r["superuserlar"]:
            self.stdout.write(self.style.WARNING(
                f"UYARI: {len(r['superuserlar'])} superuser var ({', '.join(r['superuserlar'])}). Superuser her izne "
                "sahiptir, yani maker ve checker olabilir; günlük işlerde kullanma."))
        if r["grupsuz_yonetim_kullanicilari"]:
            self.stdout.write(self.style.WARNING(
                f"UYARI: Hiçbir gruba bağlı olmayan yönetim kullanıcıları: {r['grupsuz_yonetim_kullanicilari']}"))

        if sorunlar:
            for s in sorunlar:
                self.stderr.write(self.style.ERROR("İHLAL: " + s))
            sys.exit(1)
        self.stdout.write(self.style.SUCCESS("RBAC denetimi temiz: görev ayrımı korunuyor."))
