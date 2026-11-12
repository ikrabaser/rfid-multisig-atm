"""Ölçüm toplayıcı: gecikme yüzdelikleri, hata kodları, kuyruk ve onay bekleme süreleri. Evre güvenlidir."""
import threading
from collections import Counter, defaultdict, deque

ISLEM_ADLARI = ("giris", "cekme", "yatirma", "havale", "teslim", "onay", "sistem")


def yuzdelik(degerler, y):
    """En yakın sıra yöntemi. Boş listede None."""
    if not degerler:
        return None
    sirali = sorted(degerler)
    sira = max(1, -(-len(sirali) * y // 100))          # tavan(n * y / 100)
    return sirali[int(sira) - 1]


def dagilim(degerler, basamak=1):
    if not degerler:
        return {"n": 0, "ort": None, "p50": None, "p95": None, "p99": None, "en_yuksek": None}
    yuvarla = lambda v: round(v, basamak)  # noqa: E731
    return {"n": len(degerler), "ort": yuvarla(sum(degerler) / len(degerler)), "p50": yuvarla(yuzdelik(degerler, 50)),
            "p95": yuvarla(yuzdelik(degerler, 95)), "p99": yuvarla(yuzdelik(degerler, 99)),
            "en_yuksek": yuvarla(max(degerler))}


class Olcer:
    def __init__(self):
        self._kilit = threading.Lock()
        self._ms = defaultdict(list)                    # islem adi -> gecikmeler (ms), basarili ve basarisiz
        self._basarili = Counter()
        self._basarisiz = defaultdict(Counter)          # islem adi -> hata kodu -> sayi
        self._kuyruk_ms = []                            # planlanan baslangic ile gercek baslangic farki
        self._onay_bekleme_sn = []
        self.onay_sonuclari = Counter()
        self.en_uzun_kuyruk = 0
        self.terminal_islem = Counter()
        self._akis = deque(maxlen=200)                  # son işlemler (canlı akış için; her ölçümde boşaltılır)

    def islem(self, ad, ms, hata_kodu=None, terminal=None):
        with self._kilit:
            self._ms[ad].append(ms)
            if hata_kodu is None:
                self._basarili[ad] += 1
            else:
                self._basarisiz[ad][hata_kodu] += 1
            if terminal and ad != "giris":
                self.terminal_islem[terminal] += 1
            if ad != "giris" or hata_kodu is not None:    # başarılı girişler akışı boğmasın
                self._akis.append({"islem": ad, "ms": round(ms, 1), "hata": hata_kodu, "terminal": terminal})

    def akis_al(self, en_cok=10):
        """Son ölçümden beri olan işlemlerin en yenileri (en çok `en_cok`) ve toplam sayısı."""
        with self._kilit:
            hepsi = list(self._akis)
            self._akis.clear()
        return hepsi[-en_cok:], len(hepsi)

    def kuyruk_gecikmesi(self, ms):
        with self._kilit:
            self._kuyruk_ms.append(ms)

    def kuyruk_boyu(self, n):
        with self._kilit:
            self.en_uzun_kuyruk = max(self.en_uzun_kuyruk, n)

    def onay_bitti(self, durum, bekleme_sn):
        with self._kilit:
            self.onay_sonuclari[durum] += 1
            self._onay_bekleme_sn.append(bekleme_sn)

    def toplam(self):
        with self._kilit:
            basarili = sum(self._basarili.values())
            basarisiz = sum(sum(c.values()) for c in self._basarisiz.values())
        return basarili, basarisiz

    def anlik(self):
        """Canlı akış için hafif özet."""
        with self._kilit:
            tum = [m for liste in self._ms.values() for m in liste]
            hatalar = Counter()
            for c in self._basarisiz.values():
                hatalar.update(c)
            return {
                "basarili": sum(self._basarili.values()), "basarisiz": sum(hatalar.values()),
                "p95_ms": round(yuzdelik(tum, 95), 1) if tum else None,
                "hatalar": dict(hatalar.most_common(6)),
                "onay_ort_bekleme_sn": (round(sum(self._onay_bekleme_sn) / len(self._onay_bekleme_sn), 2)
                                        if self._onay_bekleme_sn else None),
            }

    def ozet(self):
        with self._kilit:
            islemler = {}
            for ad in ISLEM_ADLARI:
                if ad not in self._ms:
                    continue
                islemler[ad] = {"basarili": self._basarili[ad], "basarisiz": sum(self._basarisiz[ad].values()),
                                "hata_kodlari": dict(self._basarisiz[ad].most_common()), "gecikme_ms": dagilim(self._ms[ad])}
            return {
                "islemler": islemler,
                "kuyruk": {"gecikme_ms": dagilim(self._kuyruk_ms), "en_uzun_kuyruk": self.en_uzun_kuyruk},
                "onay": {"sonuclar": dict(self.onay_sonuclari), "bekleme_sn": dagilim(self._onay_bekleme_sn, 2)},
                "terminal_islem": dict(self.terminal_islem),
            }
