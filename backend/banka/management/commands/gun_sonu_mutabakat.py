import datetime
import sys

from django.core.management.base import BaseCommand, CommandError

from banka import ledger
from banka.hashlog import kayit_ekle


class Command(BaseCommand):
    help = ("Gün sonu mutabakatı: defteri (çift taraflı hareketler), kayıtlı bakiyeleri, işlemleri ve "
            "hash zincirini karşılaştırır. Fark varsa çıkış kodu 1 olur. Sonuç denetim loguna yazılır.")

    def add_arguments(self, parser):
        parser.add_argument("--tarih", help="YYYY-MM-DD (varsayılan: bugün)")

    def handle(self, *args, **opts):
        tarih = None
        if opts.get("tarih"):
            try:
                tarih = datetime.date.fromisoformat(opts["tarih"])
            except ValueError:
                raise CommandError("Tarih YYYY-MM-DD biçiminde olmalı.")

        r = ledger.mutabakat(tarih)
        w = self.stdout.write
        w(f"Mutabakat tarihi: {r['tarih']}")
        w("Uygulanan işlemler:")
        if r["uygulananlar"]:
            for u in r["uygulananlar"]:
                w(f"  {u['tur']:10} {u['adet']:4} adet   toplam {u['toplam']}")
        else:
            w("  (yok)")
        w(f"İşlem durumları: {r['durumlar'] or '(yok)'}")

        sorunlar = []
        for f in r["bakiye_farklari"]:
            sorunlar.append(f"Bakiye farkı hesap {f['hesap_no']}: kayıtlı {f['kayitli']}, defterden {f['hesaplanan']}")
        if r["dengesiz_islemler"]:
            sorunlar.append(f"Borç/alacak dengesiz işlemler: {r['dengesiz_islemler']}")
        if r["hareketsiz_islemler"]:
            sorunlar.append(f"Uygulanmış ama defter hareketi olmayan işlemler: {r['hareketsiz_islemler']}")
        if r["yetim_hareketler"]:
            sorunlar.append(f"Uygulanmamış işleme ait defter hareketleri: {r['yetim_hareketler']}")
        if r["tutarsiz_ters_kayitlar"]:
            sorunlar.append(f"Orijinaliyle uyuşmayan ters kayıtlar: {r['tutarsiz_ters_kayitlar']}")
        if r["tersi_olmayan_geri_alinanlar"]:
            sorunlar.append(f"Geri alındı işaretli ama ters kaydı olmayan işlemler: {r['tersi_olmayan_geri_alinanlar']}")
        if r["politika"] == "tutarsiz":
            sorunlar.append("Politika tablosu son onaylı sürümden farklı (onaysız değişiklik olabilir)")
        if r["belirsiz_teslimler"]:
            self.stdout.write(self.style.WARNING(
                f"UYARI: Teslimi 10 dk'dan uzun süredir bildirilmeyen çekmeler: {r['belirsiz_teslimler']}"))
        if r["politika"] == "baslangic_yok":
            self.stdout.write(self.style.WARNING(
                "UYARI: Politika için onaylı başlangıç görüntüsü yok (python manage.py politika_goruntu_al)"))
        if not r["zincir"]["saglam"]:
            sorunlar.append(f"Hash zinciri bozuk: {r['zincir']['bozuk_sira']}. kayıt, {r['zincir']['sebep']}")

        kayit_ekle("mutabakat", {"tarih": r["tarih"], "saglam": not sorunlar, "sorun_sayisi": len(sorunlar)})

        if sorunlar:
            for s in sorunlar:
                self.stderr.write(self.style.ERROR("FARK: " + s))
            sys.exit(1)
        self.stdout.write(self.style.SUCCESS("Mutabakat tamam: defter, bakiyeler ve zincir tutarlı."))
