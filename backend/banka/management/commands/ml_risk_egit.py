"""ML risk modelini eğitir, mühürlü dosyaya yazar ve mührü hash zincirine kaydeder; ya da mevcut modeli doğrular.

  python manage.py ml_risk_egit                    sentetik veriyle eğit, ML_RISK_MODEL_YOLU'na yaz
  python manage.py ml_risk_egit --dogrula          dosya mührü + zincirdeki son eğitim kaydı eşleşiyor mu?

Sonra sunucuyu ML_RISK_MODU=golge ile başlat. Dürüst sınır: model SENTETİK normal trafikle eğitilir; gerçek müşteri davranışı farklıdır.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from banka import ml_golge
from banka.ml_risk import degerlendir, ozellikler, sentetik
from banka.ml_risk.orman import IsolationForest


class Command(BaseCommand):
    help = "ML risk modelini eğitir (mühürlü) ya da doğrular"

    def add_arguments(self, p):
        p.add_argument("--dogrula", action="store_true", help="Eğitmeden mevcut modeli doğrula")
        p.add_argument("--yol", help="Model dosyası (varsayılan: ML_RISK_MODEL_YOLU)")
        p.add_argument("--tohum", type=int, default=1)
        p.add_argument("--musteri", type=int, default=200)
        p.add_argument("--agac", type=int, default=100)

    def handle(self, *args, **o):
        yol = o["yol"] or settings.ML_RISK_MODEL_YOLU
        if o["dogrula"]:
            model, sebep = ml_golge.modeli_yukle(yol)
            if model is None:
                raise CommandError(f"Model geçersiz: {sebep}")
            self.stdout.write(f"Model geçerli: mühür {model.muhur()[:16]}…, {len(model.agaclar)} ağaç, zincirdeki eğitim kaydıyla eşleşiyor.")
            return
        egitim, _ = sentetik.veri_uret(tohum=o["tohum"], musteri=o["musteri"], test_gun=1, saldiri=1)
        X = [v for _, v, _ in degerlendir.ozellikleri_cikar(egitim)]
        model = IsolationForest.egit(X, ozellikler.OZELLIK_KODLARI, agac_sayisi=o["agac"], tohum=o["tohum"])
        ml_golge.modeli_yaz(model, yol)
        self.stdout.write(f"Model yazıldı: {yol}\n  {len(X)} sentetik normal olay, {o['agac']} ağaç, mühür {model.muhur()[:16]}… (zincire kaydedildi)")
        self.stdout.write("Gölge modu açmak için: ML_RISK_MODU=golge")
