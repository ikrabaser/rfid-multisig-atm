"""ML risk katmanını (Isolation Forest) sentetik trafikle eğitir ve kural motorunun basitleştirilmiş eşdeğeriyle karşılaştırır.

  python manage.py ml_risk_degerlendir                      tek deney, tablo
  python manage.py ml_risk_degerlendir --tohumlar 5         5 farklı tohumla: ortalama ve aralık (tek şanslı sonuca güvenme)
  python manage.py ml_risk_degerlendir --kaydet model.json  eğitilen modeli (mühürlü) dosyaya yazar

Veritabanına dokunmaz. Sonuçlar SENTETİK veridedir; gerçek dolandırıcılık başarısı anlamına gelmez (bkz. banka/ml_risk/__init__.py).
"""
import json
import statistics

from django.core.management.base import BaseCommand

from banka.ml_risk import aciklama, degerlendir


class Command(BaseCommand):
    help = "ML risk katmanını sentetik veriyle ölçer"

    def add_arguments(self, p):
        p.add_argument("--tohum", type=int, default=1)
        p.add_argument("--tohumlar", type=int, default=0, help="Verilirse bu kadar ardışık tohumla çalıştırıp özetler")
        p.add_argument("--musteri", type=int, default=200)
        p.add_argument("--agac", type=int, default=100)
        p.add_argument("--saldiri", type=int, default=60)
        p.add_argument("--kaydet", help="Modeli JSON olarak yaz (mühür dahil)")
        p.add_argument("--ornek", action="store_true", help="En yüksek puanlı 5 saldırı olayını gerekçesiyle göster")

    def handle(self, *args, **o):
        ortak = dict(musteri=o["musteri"], saldiri=o["saldiri"], agac=o["agac"])
        if o["tohumlar"]:
            sonuclar = [degerlendir.deney(tohum=t, **ortak) for t in range(o["tohum"], o["tohum"] + o["tohumlar"])]
            self.stdout.write(f"{len(sonuclar)} tohum ({o['tohum']}..{o['tohum'] + len(sonuclar) - 1}), her biri {o['musteri']} müşteri, {o['saldiri']} saldırı\n")

            def satir(ad, degerler):
                return f"{ad:34}{statistics.mean(degerler):8.3f}   [{min(degerler):.3f} .. {max(degerler):.3f}]"
            self.stdout.write(satir("ROC AUC  ML", [s["auc_ml"] for s in sonuclar]))
            self.stdout.write(satir("ROC AUC  kural (basit)", [s["auc_kural"] for s in sonuclar]))
            self.stdout.write(satir("kural: saldırı yakalama", [s["kural"]["saldiri"] for s in sonuclar]))
            for i, n in enumerate(sonuclar[0]["noktalar"]):
                self.stdout.write(satir(f"ML @FPR {n['fpr_hedef']:.0%}: saldırı yakalama", [s["noktalar"][i]["ml"]["saldiri"] for s in sonuclar]))
                self.stdout.write(satir(f"kural VEYA ML @FPR {n['fpr_hedef']:.0%}", [s["noktalar"][i]["birlesik"]["saldiri"] for s in sonuclar]))
            return
        s = degerlendir.deney(tohum=o["tohum"], **ortak)
        self.stdout.write(degerlendir.rapor(s))
        model = s["model"]
        if o["ornek"]:
            self.stdout.write("\nEn yüksek puanlı saldırı olayları:")
            sirali = sorted((i for i, (e, _, _) in enumerate(s["test_v"]) if e.etiket != "normal"), key=lambda i: -s["puan"][i])[:5]
            for i in sirali:
                e, v, _ = s["test_v"][i]
                self.stdout.write(f"  puan {s['puan'][i]:5.1f}  {e.etiket:18} gerekçe: {'; '.join(aciklama.gerekce(model, v)) or '-'}")
        if o["kaydet"]:
            veri = {"muhur": model.muhur(), "model": model.sozluge()}
            with open(o["kaydet"], "w", encoding="utf-8") as f:
                json.dump(veri, f, separators=(",", ":"))
            self.stdout.write(f"\nModel yazıldı: {o['kaydet']} (mühür {model.muhur()[:16]}…)")
