from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from banka import services, yonetim_talepleri
from banka.services import IslemHatasi
from banka.yonetim_talepleri import YonetimHatasi


class Command(BaseCommand):
    help = ("Teslimi BELİRSİZ kalan nakit çekmeyi sonuçlandırmak için DÖRT GÖZ talebi açar (kasetler sayıldıktan sonra). "
            "Talebi başka yetkili kullanıcılar yönetim panelinden (/admin/banka/yonetimtalebi/) onaylar; son onayda uygulanır. "
            "Para otomatik iade edilmez (müşteri parayı almış olabilir). Örnek: python manage.py nakit_coz --islem 12 "
            "--sonuc VERILMEDI --talep-eden yonetim_talep --gerekce \"Kaset sayimi: banknot yerinde\"")

    def add_arguments(self, parser):
        parser.add_argument("--islem", type=int, required=True)
        parser.add_argument("--sonuc", required=True, choices=["VERILDI", "VERILMEDI", "KISMI"],
                            help="VERILDI: para müşteriye gitti | VERILMEDI: hiç dağıtılmadı (iade) | KISMI: eksik verildi (fark iade)")
        parser.add_argument("--verilen", default="", help="KISMI için fiilen verilen banknotlar, örn. 200x8")
        parser.add_argument("--talep-eden", dest="talep_eden", default="",
                            help="Talebi açan yönetim kullanıcısı (yonetim_talep_et izni olmalı)")
        parser.add_argument("--gerekce", default="", help="Sayım sonucu/gerekçe (en az 10 karakter)")
        parser.add_argument("--dogrudan", action="store_true",
                            help="TEK kişilik yol (yalnızca NAKIT_COZ_TEK_KISI açıkken; geliştirme/acil durum). --yapan ve --neden ister")
        parser.add_argument("--yapan", default="")
        parser.add_argument("--neden", default="")

    def handle(self, *args, **o):
        if o["dogrudan"]:
            return self._dogrudan(o)
        if not o["talep_eden"]:
            raise CommandError("--talep-eden gerekli (dört göz: talebi başka biri onaylayacak)")
        kullanici = get_user_model().objects.filter(username=o["talep_eden"], is_active=True).first()
        if kullanici is None:
            raise CommandError("Talep eden kullanıcı bulunamadı")
        try:
            t = yonetim_talepleri.talep_olustur(kullanici, "NAKIT_COZ", {
                "islem_id": o["islem"], "sonuc": o["sonuc"], "verilen": o["verilen"]}, o["gerekce"])
        except YonetimHatasi as e:
            raise CommandError(e.mesaj)
        self.stdout.write(self.style.SUCCESS(
            f"Talep #{t.id} açıldı: {t.ozet}. {t.gereken_onay} onay gerekli "
            f"(yönetim paneli: /admin/banka/yonetimtalebi/). Henüz hiçbir işlem yapılmadı."))

    def _dogrudan(self, o):
        if not settings.NAKIT_COZ_TEK_KISI:
            raise CommandError("Tek kişilik çözüm kapalı (NAKIT_COZ_TEK_KISI). --talep-eden ile dört göz talebi açın.")
        try:
            r = services.nakit_coz(o["islem"], o["sonuc"], o["yapan"], o["neden"], o["verilen"])
        except IslemHatasi as e:
            raise CommandError(e.mesaj)
        self.stdout.write(self.style.SUCCESS(
            f"İşlem #{r['islem_id']} sonuçlandı: {r['teslim']} (iade edilen: {r['iade']} TL)."))
