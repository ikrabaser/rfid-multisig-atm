"""Yük üreteci ve yürütücü: sanal müşteriler ATM'de çekim/yatırma/havale yapar, yetkililer onaylar.

Zaman modeli: işlemler `sure_sn` SANAL saniyeye dağıtılır; gerçek zaman = sanal x `zaman_olcegi` (0: beklemeden, en hızlı).
Tek bir olay yığını (heap) vardır: müşteri işlemleri planlanır, onay gerektirenler yeni "onay adımı" olayları üretir. `isci_sayisi`
iş parçacığı olayları sırayla alır ve gerçek servis katmanını çağırır (gerçek kilitler, gerçek defter, gerçek risk motoru).

Ölçülenler: işlem gecikmesi (p50/p95/p99), başarısızlık nedenleri, kuyruk gecikmesi (planlanan ile gerçek başlangıç farkı) ve
kuyruk boyu, onay bekleme süreleri ve sonuçları, risk kararları, terminal yükü, nakit tükenmesi, defter/zincir bütünlüğü.

Tek işçiyle (`isci_sayisi=1`) çağıran iş parçacığında çalışır ve tohuma göre tamamen belirlidir (testler bunu kullanır).
"""
import heapq
import json
import math
import random
import threading
import time
import traceback
from collections import Counter

from django.db import connections

from .. import anomali, baglam, ledger, services
from ..hashlog import zinciri_dogrula
from ..models import Kaset, LogKaydi, Onay, OnayTalebi
from ..services import IslemHatasi
from .metrikler import Olcer

KAPANIS_DURUMLARI = ("UYGULANDI", "REDDEDILDI", "BASARISIZ", "SURESI_DOLDU")
TUR_ESLEME = {"cekme": ("CEKME", "cekme"), "buyuk": ("CEKME", "cekme"), "yatirma": ("YATIRMA", "yatirma"),
              "havale": ("HAVALE", "havale")}
ZAMAN_SERISI_EN_COK = 300


def _poisson(rng, ort):
    """Knuth; ort küçük (<= 20) olduğundan yeterli."""
    sinir, k, p = math.exp(-ort), 0, 1.0
    while True:
        p *= rng.random()
        if p <= sinir:
            return k
        k += 1


class Kosucu:
    def __init__(self, p, dunya, yayin=None, tick_sn=1.0, saat=time.monotonic):
        self.p = p
        self.dunya = dunya
        self.yayin = yayin or (lambda olay: None)
        self.tick_sn = tick_sn
        self._saat = saat
        self.olcer = Olcer()
        self.zaman_serisi = []
        self.istisnalar = []
        self._cond = threading.Condition()
        self._heap, self._sayac, self._aktif = [], 0, 0
        self._acik_talep = {}                          # talep_id -> acilis zamani (monotonic)
        self._tick_kilit = threading.Lock()
        self._iptal = threading.Event()
        self._baslangic = 0.0
        self._son_tick_zamani = 0.0
        self._son_tick_toplam = 0
        self._planli = 0

    # ------------------------------------------------------------------ plan
    def plan_uret(self):
        p, rng = self.p, random.Random(f"{self.p.tohum}:plan")
        turler = [("cekme", p.cekme_agirligi), ("yatirma", p.yatirma_agirligi),
                  ("havale", p.havale_agirligi), ("buyuk", p.buyuk_cekme_agirligi)]
        adlar, agirliklar = [t for t, _ in turler], [a for _, a in turler]
        kodlar = self.dunya.terminaller
        hesaplar = [m.hesap_no for m in self.dunya.musteriler]
        olaylar = []
        for m in self.dunya.musteriler:
            for n in range(_poisson(rng, p.ortalama_islem)):
                tur = rng.choices(adlar, agirliklar)[0]
                if tur == "cekme":
                    tutar = rng.choices([100, 200, 300, 500, 1000, 2000], [10, 20, 15, 30, 15, 10])[0]
                elif tur == "buyuk":
                    tutar = rng.randrange(10_000, 60_001, 1000)
                elif tur == "yatirma":
                    tutar = rng.randrange(200, 5001, 100)
                else:
                    tutar = rng.randrange(100, 3001, 50)
                terminal = m.ev_terminal
                if len(kodlar) > 1 and rng.random() < p.terminal_degistirme_orani:
                    terminal = rng.choice([k for k in kodlar if k != terminal])
                hedef = rng.choice([h for h in hesaplar if h != m.hesap_no]) if tur == "havale" and len(hesaplar) > 1 else None
                if tur == "havale" and hedef is None:
                    tur, tutar = "yatirma", 500
                olaylar.append((rng.uniform(0, p.sure_sn), "islem", {
                    "musteri": m.sira, "tur": tur, "tutar": tutar, "terminal": terminal, "hedef": hedef,
                    "hatali_pin": rng.random() < p.hatali_pin_orani, "anahtar": f"sim-{m.sira}-{n}"}))
        olaylar.sort(key=lambda o: o[0])
        for t, tur, veri in olaylar:
            self._ekle(t, tur, veri)
        self._planli = len(olaylar)

    def _ekle(self, t, tur, veri):
        with self._cond:
            self._sayac += 1
            heapq.heappush(self._heap, (t, self._sayac, tur, veri))
            self._cond.notify_all()

    # ------------------------------------------------------------------ calistirma
    def calistir(self):
        self.plan_uret()
        self._baslangic = self._son_tick_zamani = self._saat()
        if self.p.isci_sayisi == 1:
            self._isci()
        else:
            isciler = [threading.Thread(target=self._isci, name=f"isci-{i}", daemon=True)
                       for i in range(self.p.isci_sayisi)]
            for i in isciler:
                i.start()
            for i in isciler:
                i.join()
        self._tick(zorla=True)
        return self._ozet()

    def iptal(self):
        self._iptal.set()
        with self._cond:
            self._cond.notify_all()

    def _isci(self):
        try:
            while True:
                olay = self._sonraki()
                if olay is None:
                    return
                tur, veri, t_planli, gecikme_ms = olay
                if gecikme_ms is not None:
                    self.olcer.kuyruk_gecikmesi(gecikme_ms)
                try:
                    if tur == "islem":
                        self._musteri_islemi(veri, t_planli)
                    else:
                        self._onay_adimi(veri)
                except Exception as e:                         # tek bir olay tum kosuyu dusurmesin
                    self.olcer.islem("sistem", 0.0, type(e).__name__)
                    if len(self.istisnalar) < 5:
                        self.istisnalar.append(traceback.format_exc(limit=6))
                finally:
                    with self._cond:
                        self._aktif -= 1
                        self._cond.notify_all()
        finally:
            if self.p.isci_sayisi > 1:
                connections.close_all()

    def _sonraki(self):
        olcek = self.p.zaman_olcegi
        while True:
            self._tick_gerekirse()
            with self._cond:
                if self._iptal.is_set():
                    return None
                if not self._heap:
                    if self._aktif == 0:
                        return None
                    self._cond.wait(0.2)
                    continue
                t, _, tur, veri = self._heap[0]
                simdi = self._saat()
                vade = self._baslangic + t * olcek
                if olcek > 0 and simdi < vade:
                    self._cond.wait(min(vade - simdi, 0.2))
                    continue
                heapq.heappop(self._heap)
                self._aktif += 1
                gecikme = max(0.0, simdi - vade) * 1000 if olcek > 0 else None
                self.olcer.kuyruk_boyu(self._kuyruk_boyu(simdi) + 1)
                return tur, veri, t, gecikme

    def _kuyruk_boyu(self, simdi):
        """Vadesi gelmiş ama henüz başlamamış olay sayısı (kilit altında çağrılır)."""
        if self.p.zaman_olcegi <= 0:
            return len(self._heap)
        gecen = (simdi - self._baslangic) / self.p.zaman_olcegi
        return sum(1 for e in self._heap if e[0] <= gecen)

    # ------------------------------------------------------------------ musteri
    def _musteri_islemi(self, v, t_planli):
        m = self.dunya.musteriler[v["musteri"]]
        islem_turu, ad = TUR_ESLEME[v["tur"]]
        terminal = v["terminal"]
        with baglam.terminal_baglami(terminal):
            token = self._giris(m, terminal, v["hatali_pin"])
            if token is None:
                return
            t0 = time.perf_counter()
            try:
                r = services.islem_baslat(token, islem_turu, str(v["tutar"]), v["hedef"], v["anahtar"])
            except IslemHatasi as e:
                self.olcer.islem(ad, (time.perf_counter() - t0) * 1000, e.kod, terminal)
                return
            ms = (time.perf_counter() - t0) * 1000
            if r["durum"] == "onay_bekliyor":
                self.olcer.islem(ad, ms, None, terminal)
                self._onay_planla(r, v, t_planli)
            elif not r.get("basarili", True):
                self.olcer.islem(ad, ms, "basarisiz", terminal)
            else:
                self.olcer.islem(ad, ms, None, terminal)
                if ad == "cekme":
                    self._teslim(r["islem_id"], terminal, v["anahtar"])

    def _giris(self, m, terminal, hatali_pin):
        if hatali_pin:                                          # müşteri önce yanlış PIN girer, sonra doğrusunu
            t0 = time.perf_counter()
            try:
                services.musteri_giris(m.uid, "0000" if m.pin != "0000" else "9999")
            except IslemHatasi as e:
                self.olcer.islem("giris", (time.perf_counter() - t0) * 1000, e.kod, terminal)
        t0 = time.perf_counter()
        try:
            g = services.musteri_giris(m.uid, m.pin)
        except IslemHatasi as e:
            self.olcer.islem("giris", (time.perf_counter() - t0) * 1000, e.kod, terminal)
            return None
        self.olcer.islem("giris", (time.perf_counter() - t0) * 1000, None, terminal)
        return g["token"]

    def _teslim(self, islem_id, terminal, anahtar):
        rng = random.Random(f"{self.p.tohum}:teslim:{anahtar}")
        x = rng.random()
        durum = ("SIKISTI" if x < self.p.sikisma_orani
                 else "ALINMADI" if x < self.p.sikisma_orani + self.p.teslim_sorun_orani else "ALINDI")
        t0 = time.perf_counter()
        try:
            with baglam.terminal_baglami(terminal):
                services.teslim_bildir(islem_id, durum)
        except IslemHatasi as e:
            self.olcer.islem("teslim", (time.perf_counter() - t0) * 1000, e.kod, terminal)
            return
        self.olcer.islem("teslim", (time.perf_counter() - t0) * 1000, None, terminal)

    # ------------------------------------------------------------------ onay
    def _gecikme(self, rng):
        ort = self.p.onay_gecikme_sn
        if ort <= 0:
            return 0.0
        sigma = 0.6
        return rng.lognormvariate(math.log(ort) - sigma ** 2 / 2, sigma)

    def _onay_planla(self, r, v, t_planli):
        with self._cond:
            self._acik_talep[r["talep_id"]] = self._saat()
        rng = random.Random(f"{self.p.tohum}:onay:{v['anahtar']}")
        red = rng.random() < self.p.onay_red_orani
        t = t_planli
        for k in range(r["gereken"]):
            t += self._gecikme(rng)
            self._ekle(t, "onay", {"talep": r["talep_id"], "red": red and k == 0})

    def _talep_kapandi(self, talep_id, durum):
        with self._cond:
            acilis = self._acik_talep.pop(talep_id, None)
        if acilis is not None:
            self.olcer.onay_bitti(durum, self._saat() - acilis)

    def _onay_adimi(self, o):
        talep = OnayTalebi.objects.select_related("islem").filter(id=o["talep"]).first()
        if talep is None or talep.durum != OnayTalebi.Durum.BEKLIYOR:
            if talep is not None:
                self._talep_kapandi(talep.id, talep.durum)
            return
        oylar = list(Onay.objects.filter(talep=talep).values_list("personel__kart_uid", "personel__rol"))
        verenler = {uid for uid, _ in oylar}
        rol_tamam = any(rol >= talep.en_az_rol for _, rol in oylar)
        adaylar = [y for y in self.dunya.yetkililer if y.uid not in verenler]
        if not rol_tamam and talep.gereken_onay - len(oylar) <= 1:      # son oy: rol şartını sağlayan biri olmalı
            adaylar = [y for y in adaylar if y.rol >= talep.en_az_rol] or adaylar
        if not adaylar:
            return
        y = adaylar[talep.id % len(adaylar)]
        terminal = talep.islem.terminal
        t0 = time.perf_counter()
        try:
            with baglam.terminal_baglami(terminal):
                ozet = services.onay_karar(talep.id, y.uid, y.pin, "RED" if o["red"] else "ONAY")
        except IslemHatasi as e:
            self.olcer.islem("onay", (time.perf_counter() - t0) * 1000, e.kod)
            return
        self.olcer.islem("onay", (time.perf_counter() - t0) * 1000, None)
        if ozet.get("durum") in KAPANIS_DURUMLARI:
            self._talep_kapandi(talep.id, ozet["durum"])
            if ozet["durum"] == "UYGULANDI" and talep.islem.tur == "CEKME":
                self._teslim(talep.islem_id, terminal, f"onay-{talep.id}")

    def _acik_talepleri_tara(self):
        """Süresi dolan talepleri kapatır ve açık talepleri son durumlarıyla ölçüme işler."""
        services.suresi_dolanlari_kapat()
        with self._cond:
            idler = list(self._acik_talep)
        if not idler:
            return
        for talep_id, durum in OnayTalebi.objects.filter(id__in=idler).values_list("id", "durum"):
            if durum != OnayTalebi.Durum.BEKLIYOR:
                self._talep_kapandi(talep_id, durum)

    # ------------------------------------------------------------------ canli olcum
    def _tick_gerekirse(self):
        with self._tick_kilit:
            simdi = self._saat()
            if simdi - self._son_tick_zamani < self.tick_sn:
                return
            self._son_tick_zamani = simdi
        self._tick()

    def _tick(self, zorla=False):
        try:
            self._acik_talepleri_tara()
            bekleyen = OnayTalebi.objects.filter(durum=OnayTalebi.Durum.BEKLIYOR).count()
        except Exception:                                    # ölçüm hatası koşuyu bozmasın
            bekleyen = None
        gecen = self._saat() - self._baslangic
        basarili, basarisiz = self.olcer.toplam()
        toplam = basarili + basarisiz
        with self._cond:
            kuyruk, kalan = self._kuyruk_boyu(self._saat()), len(self._heap)
        aralik = max(self.tick_sn, 1e-6)
        olay = {"tur": "tick", "gecen_sn": round(gecen, 2), "islem": toplam,
                "islem_sn": round((toplam - self._son_tick_toplam) / aralik, 1),
                "kuyruk": kuyruk, "kalan_olay": kalan, "planli": self._planli, "bekleyen_onay": bekleyen,
                **self.olcer.anlik()}
        self._son_tick_toplam = toplam
        self.zaman_serisi.append(dict(olay))             # seriye akış girmez (rapor şişmesin)
        akis, akis_sayisi = self.olcer.akis_al()
        olay["akis"], olay["akis_sayisi"] = akis, akis_sayisi
        self.yayin(olay)

    # ------------------------------------------------------------------ nihai rapor
    def _seyrelt(self, seri):
        if len(seri) <= ZAMAN_SERISI_EN_COK:
            return seri
        adim = len(seri) / ZAMAN_SERISI_EN_COK
        return [seri[int(i * adim)] for i in range(ZAMAN_SERISI_EN_COK)]

    def _ozet(self):
        gercek_sn = self._saat() - self._baslangic
        try:
            services.suresi_dolanlari_kapat()
            self._acik_talepleri_tara()
        except Exception:
            pass
        with self._cond:
            acik_kalan = len(self._acik_talep)
        basarili, basarisiz = self.olcer.toplam()
        risk = Counter()
        for veri in LogKaydi.objects.filter(olay_turu="risk_karari").values_list("veri", flat=True):
            risk[json.loads(veri).get("karar", "?")] += 1
        terminaller = []
        for kod in self.dunya.terminaller:
            kalan = sum(k.nominal * k.adet for k in Kaset.objects.filter(terminal__kod=kod))
            ilk = self.dunya.baslangic_nakit.get(kod, 0)
            terminaller.append({"kod": kod, "islem": self.olcer.terminal_islem.get(kod, 0),
                                "nakit_ilk": float(ilk), "nakit_kalan": float(kalan)})
        mutabakat = ledger.mutabakat()
        bulgular = Counter(b["kural"] for b in anomali.tara(saat=24 * 7))
        return {
            "parametreler": self.p.sozluk(),
            "sure": {"gercek_sn": round(gercek_sn, 2), "sanal_sn": self.p.sure_sn},
            "toplam": {"planli_olay": self._planli, "islem": basarili + basarisiz, "basarili": basarili,
                       "basarisiz": basarisiz,
                       "islem_sn": round((basarili + basarisiz) / gercek_sn, 1) if gercek_sn > 0 else None},
            **self.olcer.ozet(),
            "onay_acik_kalan": acik_kalan,
            "risk": dict(risk),
            "anomali": dict(bulgular),
            "terminaller": terminaller,
            "butunluk": {"defter_saglam": bool(mutabakat.get("saglam")), "zincir_saglam": bool(zinciri_dogrula()["saglam"])},
            "istisnalar": self.istisnalar,
            "zaman_serisi": self._seyrelt(self.zaman_serisi),
        }
