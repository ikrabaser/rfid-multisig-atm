from django.core.management.base import BaseCommand

from banka import nakit
from banka.models import Islem, Terminal


class Command(BaseCommand):
    help = "Terminallerin banknot stoğunu, para dağıtıcı durumunu ve teslimi belirsiz nakit çekmeleri gösterir."

    def handle(self, *args, **opts):
        w = self.stdout.write
        stok = nakit.stok_ozeti()
        for t in Terminal.objects.order_by("kod"):
            durum = "AÇIK" if t.nakit_dagitimi else self.style.ERROR("KAPALI (arıza; dagitici_ac ile açılır)")
            w(f"{t.kod} ({t.ad}) - para dağıtıcı: {durum}")
            toplam = 0
            for k in stok.get(t.kod, []):
                toplam += k["kullanilabilir"] * k["nominal"]
                w(f"   {k['nominal']:>4} TL: kasette {k['adet']:>6}  kullanılabilir {k['kullanilabilir']:>6}  "
                  f"rezerve {k['rezerve']:>4}  iade kutusu {k['iade_kutusu']:>4}")
            w(f"   kullanılabilir nakit: {toplam:,} TL")
        belirsiz = Islem.objects.filter(tur=Islem.Tur.CEKME, durum=Islem.Durum.UYGULANDI, teslim=Islem.Teslim.BELIRSIZ)
        if belirsiz:
            w(self.style.WARNING("Teslimi BELİRSİZ nakit çekmeler (kasetleri sayıp 'nakit_coz' ile sonuçlandırın):"))
            for i in belirsiz:
                w(f"   işlem #{i.id}  {i.tutar} TL  terminal {i.terminal or '-'}  plan {i.dagitim_plani or '-'}")
