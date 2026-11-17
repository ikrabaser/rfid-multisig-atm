"""Skorun NEDENİ: eğitim trafiğine göre en çok sapan özellikler.

Isolation Forest tek başına "neden" söylemez. Burada her özellik için sağlam z-skoru (medyan ve MAD ile) hesaplanır; MAD'ı 0 olan ikili
ve sayaç özelliklerinde (çoğu zaman 0 olanlar) 0'dan her uzaklaşma güçlü sapma sayılır. En büyük sapmalar gerekçe olarak döner.
Bu bir "yaklaşık açıklama"dır: modelin iç mantığı değil, değerin ne kadar alışılmadık olduğudur.
"""
from .ozellikler import OZELLIK_ADLARI

MAD_OLCEK = 1.4826            # normal dağılımda MAD → standart sapma
EPS = 0.05                    # MAD'ın 0 olduğu özelliklerde ölçek tabanı
MIN_SAPMA = 3.0               # bundan küçük sapmalar gerekçe sayılmaz


def sapmalar(model, x):
    """[(özellik_kodu, değer, sağlam_z)] — mutlak z'ye göre azalan."""
    sonuc = []
    for kod, deger, m, mad in zip(model.ozellik_kodlari, x, model.medyan, model.mad):
        z = (deger - m) / max(MAD_OLCEK * mad, EPS)
        sonuc.append((kod, deger, z))
    sonuc.sort(key=lambda s: -abs(s[2]))
    return sonuc


def gerekce(model, x, en_cok=3):
    """Kısa, okunur gerekçe listesi, örn. ['ilk kez görülen havale hedefi', 'son 10 dk işlem sayısı: 6']."""
    cikti = []
    for kod, deger, z in sapmalar(model, x):
        if abs(z) < MIN_SAPMA or len(cikti) >= en_cok:
            continue
        ad = OZELLIK_ADLARI[kod]
        cikti.append(ad if deger in (0.0, 1.0) and kod in ("gece", "yeni_terminal", "yeni_hedef", "havale") else f"{ad}: {deger:.2f}")
    return cikti
