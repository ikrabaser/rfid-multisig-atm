"""Deney hattı: sentetik veri → özellikler → model → ölçüm (kural motorunun basitleştirilmiş eşdeğeriyle karşılaştırma).

Ölçüler:
  * ROC AUC          : rastgele bir saldırı olayının, rastgele bir normal olaydan daha yüksek puan alma olasılığı (0.5 = şans).
  * Sabit yanlış alarm oranında (FPR) yakalama: eşik, TEST'teki normal olayların puanlarından seçilir (örn. %1'i alarm versin).
  * Olay düzeyi ve SALDIRI düzeyi: bir saldırı, olaylarından biri alarm verirse yakalanmış sayılır (seri çekimde ilk olay yeter).

Kural karşılaştırması: `kural_puani`, risk.py faktörlerinden VERİ MODELİ ELVERDİĞİ KADARININ (olağandışı tutar, yeni hesapta büyük tutar, işlem hızı, gece,
farklı terminal, hatalı PIN) saf Python eşdeğeridir; imkansız seyahat (konum gerektirir) ve kart imzası yoktur. Sabitler risk.py ile aynı olmalıdır
(testte denetlenir). Bu yüzden sonuç "gerçek kural motorunun başarısı" değil, "benzer kuralların bu senaryolardaki başarısı"dır.
"""
import statistics
from datetime import timedelta

from .ozellikler import OZELLIK_KODLARI, ozellik_vektoru
from .orman import IsolationForest
from .sentetik import SALDIRI_TURLERI, veri_uret

# risk.py ile aynı (test_ml_risk.py denetler)
KURAL_EK_ONAY_ESIGI = 40
KURAL_SABITLERI = {"PIN_PUANI": 15, "PIN_EN_COK": 45, "FARKLI_TERMINAL_DK": 10, "FARKLI_TERMINAL_PUANI": 25, "TUTAR_KATI": 3,
                   "TUTAR_PUANI": 30, "YENI_HESAP_PUANI": 15, "HIZ_PENCERE_DK": 10, "HIZ_ESIKLERI": ((5, 35), (3, 20)),
                   "GECE_PUANI": 10, "TUTAR_EN_AZ_GECMIS": 3}
YENI_HESAP_TUTAR = 5000.0


def kural_puani(olay, gecmis):
    K = KURAL_SABITLERI
    puan = min(olay.pin_hata * K["PIN_PUANI"], K["PIN_EN_COK"])
    n = len(gecmis)
    if n >= K["TUTAR_EN_AZ_GECMIS"]:
        ort = statistics.fmean(g.tutar for g in gecmis)
        if olay.tutar > K["TUTAR_KATI"] * ort and olay.tutar > max(g.tutar for g in gecmis):
            puan += K["TUTAR_PUANI"]
    elif olay.tutar >= YENI_HESAP_TUTAR:
        puan += K["YENI_HESAP_PUANI"]
    pencere = [g for g in gecmis if 0 <= (olay.zaman - g.zaman).total_seconds() <= K["HIZ_PENCERE_DK"] * 60]
    for esik, p in K["HIZ_ESIKLERI"]:
        if len(pencere) >= esik:
            puan += p
            break
    if 0 <= olay.zaman.hour < 5:
        puan += K["GECE_PUANI"]
    if any(g.terminal != olay.terminal and (olay.zaman - g.zaman) <= timedelta(minutes=K["FARKLI_TERMINAL_DK"]) for g in gecmis):
        puan += K["FARKLI_TERMINAL_PUANI"]
    return puan


def ozellikleri_cikar(etiketli, once=()):
    """Etiketli olayları (zamana göre sıralı) müşteri bazında geçmişle birlikte vektöre çevirir.
    `once`: bu listeden önceki olaylar yalnızca GEÇMİŞ olarak kullanılır (örn. eğitim dönemi). Dönüş: [(EtiketliOlay, vektör, kural_puani)]."""
    gecmis = {}
    for e in once:
        gecmis.setdefault(e.musteri, []).append(e.olay)
    cikti = []
    for e in etiketli:
        g = gecmis.setdefault(e.musteri, [])
        cikti.append((e, ozellik_vektoru(e.olay, g), kural_puani(e.olay, g)))
        g.append(e.olay)
    return cikti


def roc_auc(pozitif, negatif):
    """Mann-Whitney: P(poz > neg) + 0.5·P(poz = neg)."""
    if not pozitif or not negatif:
        return float("nan")
    tum = sorted([(s, 1) for s in pozitif] + [(s, 0) for s in negatif])
    siralar, i = {}, 0
    while i < len(tum):
        j = i
        while j < len(tum) and tum[j][0] == tum[i][0]:
            j += 1
        ort = (i + j - 1) / 2.0 + 1
        for k in range(i, j):
            siralar[k] = ort
        i = j
    toplam_poz = sum(siralar[k] for k, (_, e) in enumerate(tum) if e == 1)
    n1, n0 = len(pozitif), len(negatif)
    return (toplam_poz - n1 * (n1 + 1) / 2.0) / (n1 * n0)


def esik_sec(normal_skorlar, fpr):
    """Normal olayların en fazla `fpr` oranı eşiğin ÜSTÜNDE (alarm) kalacak en küçük eşik."""
    sirali = sorted(normal_skorlar)
    izinli = int(len(sirali) * fpr)
    return sirali[-izinli - 1] if izinli < len(sirali) else sirali[0]


def deney(tohum=1, musteri=200, egitim_gun=60, test_gun=15, saldiri=60, agac=100, fprler=(0.01, 0.05)):
    egitim, test = veri_uret(tohum, musteri, egitim_gun, test_gun, saldiri)
    egitim_v = ozellikleri_cikar(egitim)
    X = [v for _, v, _ in egitim_v]
    model = IsolationForest.egit(X, OZELLIK_KODLARI, agac_sayisi=agac, tohum=tohum)
    test_v = ozellikleri_cikar(test, once=egitim)
    puan = [model.puan(v) for _, v, _ in test_v]
    kural = [k for _, _, k in test_v]
    etiket = [e.etiket for e, _, _ in test_v]
    normal = [i for i, t in enumerate(etiket) if t == "normal"]
    saldirgan = [i for i, t in enumerate(etiket) if t != "normal"]

    sonuc = {"egitim_olay": len(X), "test_normal": len(normal), "test_saldiri_olay": len(saldirgan),
             "saldiri_sayisi": len({test_v[i][0].saldiri_no for i in saldirgan}),
             "auc_ml": roc_auc([puan[i] for i in saldirgan], [puan[i] for i in normal]),
             "auc_kural": roc_auc([kural[i] for i in saldirgan], [kural[i] for i in normal]), "noktalar": []}

    def olcu(bayrak):
        """bayrak(i) -> alarm mı. (olay_yakalama, saldiri_yakalama, tür_yakalamaları, yanlış_alarm)"""
        yanlis = sum(1 for i in normal if bayrak(i)) / max(len(normal), 1)
        olay = sum(1 for i in saldirgan if bayrak(i)) / max(len(saldirgan), 1)
        saldirilar = {}
        for i in saldirgan:
            e = test_v[i][0]
            saldirilar.setdefault(e.saldiri_no, [e.etiket, False])
            saldirilar[e.saldiri_no][1] |= bool(bayrak(i))
        tur = {t: [0, 0] for t in SALDIRI_TURLERI}
        for etk, yakalandi in saldirilar.values():
            tur[etk][1] += 1
            tur[etk][0] += 1 if yakalandi else 0
        genel = sum(a for a, _ in tur.values()) / max(sum(b for _, b in tur.values()), 1)
        return {"yanlis_alarm": yanlis, "olay": olay, "saldiri": genel, "tur": {t: a / b if b else float("nan") for t, (a, b) in tur.items()}}

    sonuc["kural"] = olcu(lambda i: kural[i] >= KURAL_EK_ONAY_ESIGI)
    for fpr in fprler:
        esik = esik_sec([puan[i] for i in normal], fpr)
        ml = olcu(lambda i: puan[i] > esik)
        birlesik = olcu(lambda i: puan[i] > esik or kural[i] >= KURAL_EK_ONAY_ESIGI)
        sonuc["noktalar"].append({"fpr_hedef": fpr, "esik": esik, "ml": ml, "birlesik": birlesik})
    sonuc["model"] = model
    sonuc["test_v"] = test_v
    sonuc["puan"] = puan
    return sonuc


def rapor(s):
    """İnsan okuyacağı metin tablosu."""
    satirlar = [
        f"Eğitim: {s['egitim_olay']} normal olay | Test: {s['test_normal']} normal + {s['test_saldiri_olay']} saldırı olayı ({s['saldiri_sayisi']} saldırı)",
        f"ROC AUC  ML: {s['auc_ml']:.3f}   kural (basitleştirilmiş): {s['auc_kural']:.3f}   (0.5 = şans, 1.0 = kusursuz)",
        "",
        f"{'':20}{'yanlış alarm':>14}{'saldırı yakalama':>19}",
    ]
    k = s["kural"]
    satirlar.append(f"{'kural (eşik 40)':20}{k['yanlis_alarm']:>13.1%}{k['saldiri']:>19.1%}")
    for n in s["noktalar"]:
        satirlar.append(f"{'ML  @FPR ' + format(n['fpr_hedef'], '.0%'):20}{n['ml']['yanlis_alarm']:>13.1%}{n['ml']['saldiri']:>19.1%}")
        satirlar.append(f"{'kural VEYA ML':20}{n['birlesik']['yanlis_alarm']:>13.1%}{n['birlesik']['saldiri']:>19.1%}")
    satirlar += ["", "Saldırı türüne göre yakalama (saldırı düzeyi):", f"{'tür':22}{'kural':>9}" + "".join(f"{'ML@' + format(n['fpr_hedef'], '.0%'):>9}" for n in s["noktalar"])]
    for t in SALDIRI_TURLERI:
        satirlar.append(f"{t:22}{k['tur'][t]:>9.0%}" + "".join(f"{n['ml']['tur'][t]:>9.0%}" for n in s["noktalar"]))
    return "\n".join(satirlar)
