"""Banka operasyon simülasyonu: sanal müşterilerle iş yükü, performans ve operasyon analizi.

Akış:  panel -> SimulasyonKosusu kaydı -> Procrastinate görevi (gorevler.py) -> yürütücü (yurutucu.py)
       -> ALT SÜREÇ (manage.py operasyon_calistir, ayrı SANDBOX veritabanı) -> JSON satırları
       -> yürütücü: Centrifugo'ya yayın (centrifugo.py) + kayıt güncelleme -> panel.

Yük, gerçek veriden tamamen ayrı bir veritabanında çalışır; gerçek kasa, müşteri, bildirim ve silinemeyen hash zinciri
simülasyon kayıtlarıyla büyümez.
"""
