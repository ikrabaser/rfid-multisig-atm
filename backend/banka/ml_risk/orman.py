"""Isolation Forest (Liu, Ting, Zhou 2008), bağımlılıksız ve tohumlu.

Fikir: anormal noktalar azdır ve farklıdır, bu yüzden rastgele bölmelerle ÇOK ERKEN yalıtılırlar. Normal noktalar kalabalığın
içinde olduğundan yalıtılmaları için daha çok bölme gerekir. Bir noktanın ağaçlardaki ortalama yol uzunluğu E[h(x)] kısaysa anormaldir.

    c(n)  = 2·H(n-1) − 2(n-1)/n     (n örnekli bir ağaçta başarısız aramanın ortalama yolu; H(i) ≈ ln(i) + 0.5772156649)
    s(x)  = 2 ^ ( −E[h(x)] / c(ψ) )   ψ: alt örneklem boyu

    s → 1 : kesinlikle anormal,  s ≈ 0.5 : ayırt edilemez,  s → 0 : çok normal.

Ağaç düğümleri JSON'a yazılabilir listelerdir: iç düğüm [özellik, bölme_değeri, sol, sağ], yaprak [örnek_sayısı].
"""
import hashlib
import json
import math
import random
from bisect import bisect_left

EULER = 0.5772156649015329
SURUM = 1


def c(n):
    """n örnekli bir ikili arama ağacında başarısız aramanın ortalama yol uzunluğu (normalleştirme sabiti)."""
    if n <= 1:
        return 0.0
    if n == 2:
        return 1.0
    return 2.0 * (math.log(n - 1) + EULER) - 2.0 * (n - 1) / n


def _agac_kur(X, idx, derinlik, sinir, rng):
    if derinlik >= sinir or len(idx) <= 1:
        return [len(idx)]
    d = len(X[0])
    ozellikler = list(range(d))
    rng.shuffle(ozellikler)
    for q in ozellikler:                                   # değeri değişen ilk rastgele özelliği seç
        degerler = [X[i][q] for i in idx]
        lo, hi = min(degerler), max(degerler)
        if lo < hi:
            p = rng.uniform(lo, hi)
            sol = [i for i in idx if X[i][q] < p]
            sag = [i for i in idx if X[i][q] >= p]
            if sol and sag:
                return [q, p, _agac_kur(X, sol, derinlik + 1, sinir, rng), _agac_kur(X, sag, derinlik + 1, sinir, rng)]
    return [len(idx)]                                      # hiçbir özellik bölünemiyor: aynı noktalar


def _yol(x, dugum):
    e = 0
    while len(dugum) == 4:
        dugum = dugum[2] if x[dugum[0]] < dugum[1] else dugum[3]
        e += 1
    return e + c(dugum[0])


class IsolationForest:
    def __init__(self, agaclar, alt_orneklem, egitim_skorlari, medyan, mad, tohum, ozellik_kodlari):
        self.agaclar = agaclar
        self.alt_orneklem = alt_orneklem
        self.egitim_skorlari = egitim_skorlari             # sıralı: yüzdelik hesabı için
        self.medyan = medyan                               # özellik başına eğitim medyanı ve MAD'ı (açıklama için)
        self.mad = mad
        self.tohum = tohum
        self.ozellik_kodlari = list(ozellik_kodlari)

    # ------------------------------------------------------------ eğitim
    @classmethod
    def egit(cls, X, ozellik_kodlari, agac_sayisi=100, alt_orneklem=256, tohum=1, skor_ornegi=2000):
        if len(X) < 2:
            raise ValueError("Eğitim için en az 2 örnek gerekir")
        rng = random.Random(tohum)
        psi = min(alt_orneklem, len(X))
        sinir = math.ceil(math.log2(psi)) if psi > 1 else 1
        agaclar = []
        for _ in range(agac_sayisi):
            idx = rng.sample(range(len(X)), psi)
            agaclar.append(_agac_kur(X, idx, 0, sinir, rng))
        d = len(X[0])
        medyan, mad = [], []
        for q in range(d):
            sutun = sorted(r[q] for r in X)
            m = sutun[len(sutun) // 2]
            sapma = sorted(abs(v - m) for v in sutun)
            medyan.append(m)
            mad.append(sapma[len(sapma) // 2])
        model = cls(agaclar, psi, [], medyan, mad, tohum, ozellik_kodlari)
        ornek = X if len(X) <= skor_ornegi else rng.sample(X, skor_ornegi)
        model.egitim_skorlari = sorted(model.skor(r) for r in ornek)
        return model

    # ------------------------------------------------------------ puanlama
    def skor(self, x):
        """Ham anomali skoru s(x) ∈ (0, 1)."""
        ortalama = sum(_yol(x, a) for a in self.agaclar) / len(self.agaclar)
        return 2.0 ** (-ortalama / c(self.alt_orneklem))

    def yuzdelik(self, skor):
        """Skorun eğitim (normal) trafiğindeki yüzdelik sırası, 0..100. 99 = normal trafiğin %99'undan daha sıra dışı."""
        sirali = self.egitim_skorlari
        return 100.0 * bisect_left(sirali, skor) / len(sirali)

    def puan(self, x):
        return self.yuzdelik(self.skor(x))

    # ------------------------------------------------------------ kayıt
    def sozluge(self):
        return {"surum": SURUM, "tohum": self.tohum, "alt_orneklem": self.alt_orneklem, "ozellikler": self.ozellik_kodlari,
                "medyan": self.medyan, "mad": self.mad, "egitim_skorlari": self.egitim_skorlari, "agaclar": self.agaclar}

    @classmethod
    def sozlukten(cls, s):
        if s.get("surum") != SURUM:
            raise ValueError("Model sürümü uyumsuz")
        return cls(s["agaclar"], s["alt_orneklem"], s["egitim_skorlari"], s["medyan"], s["mad"], s["tohum"], s["ozellikler"])

    def muhur(self):
        """Modelin içeriğinin SHA-256'sı. Model dosyası değiştirilirse (zehirleme/bozulma) değişir; entegrasyonda doğrulanır."""
        veri = json.dumps(self.sozluge(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(veri.encode("utf-8")).hexdigest()
