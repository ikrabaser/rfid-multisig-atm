"""Firmware simulatorunu (sim_main.cpp: GERCEK C++ terminal cekirdegi) Python'dan suren yardimci.

Kopru tarafi burada yazilmaz: terminalin yazdigi her seri satir ("TX") gercek Python koprusune (bridge/cerceve.py Oturum)
verilir, cevabi "S" ile terminale geri girilir. Boylece C++ firmware ile Python kopru birbirini DOGRUDAN dener.
"""
import shutil
import subprocess
import threading
from pathlib import Path

TEST_KLASORU = Path(__file__).resolve().parent
SIM = TEST_KLASORU / "sim_calistir"


def simulatoru_derle():
    """sim_calistir yoksa ya da kaynaklar daha yeniyse derler. g++/make yoksa None doner (testler atlanir)."""
    if not shutil.which("g++") or not shutil.which("make"):
        return None
    kaynaklar = list(TEST_KLASORU.glob("sim_main.cpp")) + list(TEST_KLASORU.glob("sahte_donanim.h")) + \
        list((TEST_KLASORU.parent / "atm_terminal").glob("*.[ch]*"))
    if not SIM.exists() or SIM.stat().st_mtime < max(k.stat().st_mtime for k in kaynaklar):
        r = subprocess.run(["make", "-C", str(TEST_KLASORU), "sim"], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("firmware simulatoru derlenemedi:\n" + r.stdout + r.stderr)
    return SIM


class Olay:
    __slots__ = ("t", "tur", "veri")

    def __init__(self, t, tur, veri):
        self.t, self.tur, self.veri = t, tur, veri

    def __repr__(self):
        return f"{self.t} {self.tur} {self.veri}"


class YazilimTerminali:
    """Tek bir simule terminal. `kopru`: satir -> cevap satiri ya da None (bridge.cerceve.Oturum.satir_isle)."""

    DURUM = {  # terminal.h'taki Durum sirasi
        "BASLAT": 0, "BOSTA": 1, "KART_BEKLE": 2, "MESAJ": 3, "PIN_AL": 4, "GIRIS_BEKLE": 5, "MENU": 6, "SAYI_AL": 7,
        "BAKIYE_BEKLE": 8, "ISLEM_BEKLE": 9, "ISLEM_TAMAM": 10, "PARA_BEKLE": 11, "TESLIM_BEKLE": 12, "ONAY_IZLE": 13,
        "ONAY_DURUM_BEKLE": 14, "PERSONEL_KART_BEKLE": 15, "BEKLEYEN_BEKLE": 16, "TALEP_YOK": 17, "KARAR_AL": 18,
        "ONAY_ISTEK_BEKLE": 19, "KASAAC_BEKLE": 20, "PINDEGIS_BEKLE": 21, "KILIT": 22,
    }

    _acilis = 0   # gercek cihazda EEPROM'daki sayac gibi: her yeni simulator bir oncekinin devami

    def __init__(self, kopru, gurultu=None, tohum=1, baslangic_ms=None, adim_ms=5):
        exe = simulatoru_derle()
        if exe is None:
            raise RuntimeError("g++/make yok")
        YazilimTerminali._acilis += 1
        args = [str(exe), "--tohum", str(tohum), "--acilis", str(YazilimTerminali._acilis)]
        if gurultu:
            args += ["--gurultu"] + [str(g) for g in gurultu]
        if baslangic_ms is not None:
            args += ["--baslangic", str(baslangic_ms)]
        self.kopru = kopru
        self.adim_ms = adim_ms
        self.p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.olaylar = []          # tum olaylar (Olay)
        self.tx = []               # terminalin yazdigi ham satirlar
        self.lcd = ["", ""]
        self.role = False
        self.led = False
        self.durum = 0
        self.t = 0
        self.istatistik = {}
        self.istatistik_al()       # simulator ilk olaylari basar ve bir komut bekler: "OK" ile senkron ol

    # ---------------------------------------------------------------- sim ile konusma

    def _gonder(self, satir):
        self.p.stdin.write(satir + "\n")
        self.p.stdin.flush()

    def _oku_ta_kadar_ok(self):
        while True:
            satir = self.p.stdout.readline()
            if not satir:
                raise RuntimeError("simulator beklenmedik sekilde kapandi")
            satir = satir.rstrip("\n")
            parca = satir.split(" ", 2)
            t, tur = int(parca[0]), parca[1]
            veri = parca[2] if len(parca) > 2 else ""
            self.t = t
            if tur == "OK":
                return
            self._olay(Olay(t, tur, veri))

    def _olay(self, o):
        self.olaylar.append(o)
        if o.tur == "LCD":
            a, b = o.veri.split("|", 1)
            self.lcd = [a, b]
        elif o.tur == "ROLE":
            self.role = o.veri == "1"
        elif o.tur == "LED":
            self.led = o.veri == "1"
        elif o.tur == "DURUM":
            self.durum = int(o.veri)
        elif o.tur == "ISTATISTIK":
            self.istatistik = {k: int(v) for k, v in (x.split("=") for x in o.veri.split())}
        elif o.tur == "TX":
            self.tx.append(o.veri)
            cevap = self.kopru(o.veri)
            if cevap is not None:
                if self._bekleyen is None:
                    self._bekleyen = []
                self._bekleyen.append(cevap)

    _bekleyen = None

    def ilerle(self, ms):
        """ms kadar simule zaman. Terminalin yazdigi her satir kopruden gecirilir, cevap hemen terminale girilir."""
        kalan = ms
        while kalan > 0:
            n = min(self.adim_ms, kalan)
            kalan -= n
            self._bekleyen = []
            self._gonder(f"T {n}")
            self._oku_ta_kadar_ok()
            for cevap in self._bekleyen:
                self._gonder(f"S {cevap}")
                self._oku_ta_kadar_ok()
            self._bekleyen = None

    def ilerle_kadar(self, kosul, en_cok_ms=20000):
        gecen = 0
        while gecen < en_cok_ms:
            if kosul():
                return True
            self.ilerle(self.adim_ms)
            gecen += self.adim_ms
        return kosul()

    # ---------------------------------------------------------------- kullanici eylemleri

    def tikla(self, tus, en_cok_ms=3000):
        self._gonder(f"K {tus}")
        self._bekleyen = []
        self._oku_ta_kadar_ok()
        self.ilerle(110)             # tus 80 ms basili kalir + bosluk

    def yaz(self, metin):
        for c in metin:
            self.tikla(c)

    def kart_okut(self, uid):
        self._gonder(f"C {uid}")
        self._bekleyen = []
        self._oku_ta_kadar_ok()
        self.ilerle(300)

    def durum_bekle(self, ad, en_cok_ms=20000):
        return self.ilerle_kadar(lambda: self.durum == self.DURUM[ad], en_cok_ms)

    def ekran_bekle(self, satir0, en_cok_ms=20000, satir1=None):
        return self.ilerle_kadar(lambda: self.lcd[0] == satir0 and (satir1 is None or self.lcd[1] == satir1), en_cok_ms)

    def istatistik_al(self):
        self._gonder("I")
        self._bekleyen = []
        self._oku_ta_kadar_ok()
        return self.istatistik

    def role_acilislari(self):
        return [o for o in self.olaylar if o.tur == "ROLE" and o.veri == "1"]

    def kapat(self):
        try:
            self._gonder("Q")
        except (BrokenPipeError, ValueError):
            pass
        try:
            self.p.stdin.close()
        except Exception:
            pass
        self.p.wait(timeout=10)
        self.p.stdout.close()
