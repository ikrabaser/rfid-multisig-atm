"""ML risk katmanı (gölge sinyal): hesap davranışından sapmayı denetimsiz bir modelle puanlar.

Saf Python, Django'dan bağımsız çekirdek (ozellikler, orman, aciklama, sentetik, degerlendir). Kural motorunu (risk.py) DEĞİŞTİRMEZ,
yanına bir sinyal ekler. Bu paket yalnızca hesaplar; veritabanına yazan entegrasyon ayrı bir adımdır.

Dürüst sınır: gerçek dolandırıcılık verisi yoktur. Model, sentetik normal trafikle eğitilir ve yine kendi ürettiğimiz saldırı
senaryolarıyla ölçülür. Sonuçlar "bu mimari bu senaryoları şu oranda yakalıyor" demektir, gerçek dünya başarısı değildir.
"""
