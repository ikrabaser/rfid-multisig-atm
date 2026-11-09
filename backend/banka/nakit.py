"""Banknot stoğu ve dağıtım planı: ATM'in nakit tarafı.

Para çekerken tutarın tek başına yetmesi yetmez; eldeki banknotlarla VERİLEBİLMESİ gerekir. Bu modül:
  * tutarı, kasetlerdeki gerçek stokla (rezervler düşülmüş) en az banknotla böler,
  * planı REZERVE eder (eşzamanlı iki çekim aynı banknotu sayamaz),
  * dağıtım sonucuna göre stoğu kapatır: alındı / geri çekildi / sıkıştı / eksik verildi.

Veritabanı işlemleri, çağıranın transaction.atomic() bloğunda ve kilit.zincir_kilidi() alınmış olarak
çağrılır. Bu modül services'i İÇE AKTARMAZ (döngü olmasın); hata olarak kendi NakitHatasi'nı fırlatır.
"""
import math
import re
from decimal import Decimal
from functools import reduce

from django.conf import settings

from .models import Kaset, Terminal

PLAN_DESENI = re.compile(r"\d{1,4}x\d{1,5}(\+\d{1,4}x\d{1,5})*")   # örn. "200x8+100x2"


class NakitHatasi(Exception):
    def __init__(self, mesaj, kod):
        super().__init__(mesaj)
        self.mesaj = mesaj
        self.kod = kod


# ------------------------------------------------------------------ banknot listesi (plan) yardımcıları

def nominaller():
    """Ayarlardaki banknot cinsleri, büyükten küçüğe."""
    return tuple(sorted({int(n) for n in settings.BANKNOT_NOMINALLERI}, reverse=True))


def en_kucuk_birim():
    """Verilebilecek tutarların birimi: banknot cinslerinin en büyük ortak böleni (200/100/50/20 için 10)."""
    return reduce(math.gcd, nominaller())


def plan_yaz(plan):
    return "+".join(f"{n}x{plan[n]}" for n in sorted(plan, reverse=True) if plan[n] > 0)


def plan_coz(metin):
    """'200x8+100x2' -> {200: 8, 100: 2}. Boş metin boş plandır. Geçersizse NakitHatasi."""
    metin = (metin or "").strip()
    if not metin:
        return {}
    if not PLAN_DESENI.fullmatch(metin):
        raise NakitHatasi("Geçersiz banknot listesi", "plan")
    plan = {}
    for parca in metin.split("+"):
        nominal, adet = (int(x) for x in parca.split("x"))
        if nominal not in nominaller() or adet <= 0 or nominal in plan:
            raise NakitHatasi("Geçersiz banknot listesi", "plan")
        plan[nominal] = adet
    return plan


def plan_tutari(plan):
    return sum(nominal * adet for nominal, adet in plan.items())


def plan_cikar(buyuk, kucuk):
    """buyuk - kucuk (cins cins); yalnızca pozitif kalanlar."""
    return {n: buyuk[n] - kucuk.get(n, 0) for n in buyuk if buyuk[n] - kucuk.get(n, 0) > 0}


def plan_icinde_mi(alt, ust):
    return all(ust.get(n, 0) >= adet for n, adet in alt.items())


# ------------------------------------------------------------------ plan hesabı (saf fonksiyon)

def _tam_lira(tutar):
    tutar = Decimal(tutar)
    if tutar != tutar.to_integral_value():
        raise NakitHatasi(f"Tutar {en_kucuk_birim()} TL'nin katı olmalı", "tutar_birimi")
    return int(tutar)


def _ara(lira, stok, pencere):
    """En az banknotlu planı arar (eşitlikte büyük banknot öncelikli). Bulamazsa None.

    Derinlik öncelikli arama: her cins için en çok kullanılabilecek adetten başlayıp `pencere` kadar aşağı
    iner; son cins tam bölünmek zorundadır. Bulunan plan her zaman tutarı TAM verir ve stoğu aşmaz;
    bulunamamak yalnızca "bu tutar verilemiyor" demektir (güvenli yön).
    """
    sirali = nominaller()
    en_iyi = [None]
    secim = {}

    def dfs(i, kalan):
        if kalan == 0:
            adet = sum(secim.values())
            if en_iyi[0] is None or adet < en_iyi[0][0]:
                en_iyi[0] = (adet, dict(secim))
            return
        if i == len(sirali):
            return
        d = sirali[i]
        ust = min(stok.get(d, 0), kalan // d)
        alt = ust if i == len(sirali) - 1 else max(0, ust - pencere)
        for k in range(ust, alt - 1, -1):
            if k:
                secim[d] = k
            else:
                secim.pop(d, None)
            dfs(i + 1, kalan - k * d)
        secim.pop(d, None)

    dfs(0, lira)
    return None if en_iyi[0] is None else en_iyi[0][1]


def plan_hesapla(tutar, stok, pencere=12):
    """tutar (Decimal) için {nominal: adet} planı. stok: {nominal: kullanılabilir adet}.

    NakitHatasi kodları: tutar_birimi (tutar birimin katı değil / bu banknotlarla oluşmaz), banknot (stok yetmez).
    """
    lira = _tam_lira(tutar)
    birim = en_kucuk_birim()
    if lira <= 0 or lira % birim:
        raise NakitHatasi(f"Tutar {birim} TL'nin katı olmalı", "tutar_birimi")
    if _ara(lira, {n: 10 ** 9 for n in nominaller()}, pencere) is None:
        raise NakitHatasi("Bu tutar verilemiyor", "tutar_birimi")
    plan = _ara(lira, stok, pencere)
    if plan is None:
        raise NakitHatasi("ATM'de bu tutar için banknot yok", "banknot")
    if plan_tutari(plan) != lira or not plan_icinde_mi(plan, stok):
        raise NakitHatasi("Dağıtım planı hesaplanamadı", "plan")  # olmamalı; güvenlik ağı
    return plan


# ------------------------------------------------------------------ veritabanı işlemleri

def terminal_kodu(kod):
    return kod or settings.VARSAYILAN_TERMINAL


def _terminal(kod):
    satir = Terminal.objects.filter(kod=terminal_kodu(kod)).values("id", "kod", "nakit_dagitimi").first()
    if satir is None:
        raise NakitHatasi("Terminal tanımsız", "terminal")
    return satir


def _kasetleri_kilitle(terminal_id):
    """Terminalin kasetlerini id sırasıyla kilitler (kilit sırası sabit: deadlock olmasın)."""
    return list(Kaset.objects.select_for_update().filter(terminal_id=terminal_id).order_by("id"))


def plan_uret(kod, tutar, rezerve):
    """Terminalin gerçek stokuyla tutarı böler. rezerve=True ise banknotları rezerve eder.

    Hata: NakitHatasi (dagitici: dağıtıcı kapalı, tutar_birimi, banknot, terminal).
    """
    terminal = _terminal(kod)
    if not terminal["nakit_dagitimi"]:
        raise NakitHatasi("Para dağıtıcı arızalı", "dagitici")
    kasetler = _kasetleri_kilitle(terminal["id"])
    stok = {k.nominal: k.adet - k.rezerve for k in kasetler}
    plan = plan_hesapla(tutar, stok)
    if rezerve:
        for k in kasetler:
            n = plan.get(k.nominal, 0)
            if n:
                k.rezerve += n
                k.save(update_fields=["rezerve"])
    return plan


def sonuclandir(kod, planlanan, verilen, kutuya, kasete_geri):
    """Rezerve edilmiş bir dağıtımın sonucunu stoğa işler. Uyarı (tutarsızlık) listesi döndürür.

      verilen     : müşteriye fiilen çıkan banknotlar           -> kasetten düşer
      kutuya      : geri çekilen / sıkışan / verilmeyen banknot  -> kasetten düşer, iade kutusuna girer
      kasete_geri : hiç dağıtılmadığı sayımla kanıtlanan banknot -> kasette kalır (yalnızca rezerve düşer)
    Her cins için planlanan = verilen + kutuya + kasete_geri olmalıdır.
    """
    if not planlanan:
        return []
    for n in set(planlanan) | set(verilen) | set(kutuya) | set(kasete_geri):
        if planlanan.get(n, 0) != verilen.get(n, 0) + kutuya.get(n, 0) + kasete_geri.get(n, 0):
            raise NakitHatasi("Banknot sonucu plana uymuyor", "plan")

    terminal = _terminal(kod)
    kasetler = {k.nominal: k for k in _kasetleri_kilitle(terminal["id"])}
    uyarilar = []
    for n, p in planlanan.items():
        k = kasetler.get(n)
        if k is None:
            uyarilar.append(f"{n} TL kaseti yok")
            continue
        cikan = verilen.get(n, 0) + kutuya.get(n, 0)
        if k.rezerve < p or k.adet < cikan:
            uyarilar.append(f"{n} TL: rezerve {k.rezerve} < {p} ya da adet {k.adet} < {cikan}")
        k.rezerve = max(0, k.rezerve - p)
        k.adet = max(k.rezerve, k.adet - cikan)
        k.iade_kutusu += kutuya.get(n, 0)
        k.save(update_fields=["adet", "rezerve", "iade_kutusu"])
    return uyarilar


def kaset_ayarla(kod, nominal, adet, iade_kutusunu_bosalt=False):
    """Fiziksel sayım/dolum sonrası kasetteki banknot sayısını ayarlar. (eski, yeni) döndürür. atomic içinde çağrılır."""
    nominal = int(nominal)
    adet = int(adet)
    if nominal not in nominaller():
        raise NakitHatasi(f"{nominal} TL ayarlardaki banknot cinslerinde yok", "nominal")
    if adet < 0:
        raise NakitHatasi("Adet negatif olamaz", "adet")
    terminal = _terminal(kod)
    kaset, _ = Kaset.objects.select_for_update().get_or_create(terminal_id=terminal["id"], nominal=nominal)
    if adet < kaset.rezerve:
        raise NakitHatasi(
            f"Adet rezerve edilen {kaset.rezerve} banknottan az olamaz (teslim bekleyen çekimler var)", "rezerve")
    eski = (kaset.adet, kaset.iade_kutusu)
    kaset.adet = adet
    if iade_kutusunu_bosalt:
        kaset.iade_kutusu = 0
    kaset.save(update_fields=["adet", "iade_kutusu"])
    return eski, (kaset.adet, kaset.iade_kutusu)


def stok_ozeti():
    """Terminal -> kasetler listesi (gösterim için, kilitsiz)."""
    sonuc = {}
    for k in Kaset.objects.select_related("terminal").order_by("terminal__kod", "-nominal"):
        sonuc.setdefault(k.terminal.kod, []).append({
            "nominal": k.nominal, "adet": k.adet, "rezerve": k.rezerve,
            "kullanilabilir": k.adet - k.rezerve, "iade_kutusu": k.iade_kutusu,
        })
    return sonuc
