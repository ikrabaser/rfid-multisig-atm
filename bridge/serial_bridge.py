#!/usr/bin/env python3
"""Arduino <-> Django kopru programi.

Arduino seri porttan satir satir komut yollar (KART|uid, PIN|uid|pin ...), bu program
bunlari Django API'ye cevirir ve cevabi tekrar seri porttan Arduino'ya yazar.
Protokolun tamami arduino/atm_terminal/atm_terminal.ino basindaki yorumda.

Hat cercevelemesi (protokol v2, bkz. cerceve.py): firmware her satiri "SS:yuk*CRC16" olarak yollar; bozuk cerceve
reddedilir, yeniden gonderilen ayni istek tekrar islenmez (onbellekten cevaplanir). Eski cercevesiz firmware de
cerceve gelene kadar calisir; --kati ile cercevesiz satirlar hic kabul edilmez.

Kullanim:
    python serial_bridge.py --port COM3 --terminal ATM-01     # gercek Arduino
    python serial_bridge.py --konsol                          # Arduino'suz, klavyeden dene

HTTPS (PIN trafigi sifreli gitsin):
    python serial_bridge.py --port COM3 --api https://127.0.0.1:8443 --ca-dosyasi rootCA.pem

Cevrimdisi davranis (store-and-forward):
    Sunucuya ulasilamazsa para islemleri YAPILMAZ ("Sunucu kapali"); cunku PIN dogrulayicilari ve
    bakiyeler yalnizca sunucuda durur. Cevrimdisiyken olanlar (kart okutma denemeleri, cevrimdisi
    kalma suresi) bir gunluk dosyasina yazilir ve baglanti gelince sunucuya yuklenir. Her satirin
    benzersiz bir kimligi vardir; ayni satir iki kez yuklense bile sunucuda bir kez kaydedilir.
    Gunluge PIN, token ya da tutar YAZILMAZ.

Ayarlar ortam degiskeniyle de verilebilir: KOPRU_API, KOPRU_API_ANAHTARI, KOPRU_TERMINAL.
Dikkat: Seri port ayni anda tek programda acik olabilir. Arduino IDE'nin Serial
Monitor'unu kapat.
"""
import argparse
import datetime
import json
import os
import sys
import time
import unicodedata
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests

try:
    from cerceve import Oturum, eski_komut_mu, cerceve_mi
except ImportError:                      # paket olarak (bridge.serial_bridge) ice aktarilirsa
    from .cerceve import Oturum, eski_komut_mu, cerceve_mi

ESLEME = str.maketrans({"ı": "i", "İ": "I", "ß": "ss"})
TUR = {"C": "CEKME", "Y": "YATIRMA", "H": "HAVALE"}
TUR_KISA = {"CEKME": "Cek", "YATIRMA": "Yatir", "HAVALE": "Havale", "KASA_ACMA": "Kasa"}
KOMUTLAR = {"KART", "PIN", "PINDEGIS", "BAKIYE", "ISLEM", "BEKLEYEN", "DURUM", "ONAY", "KASAAC", "TESLIM"}
GIZLI_KOMUTLAR = {"PIN", "PINDEGIS", "ONAY", "BAKIYE", "ISLEM", "KASAAC"}  # konsola yazilirken ayrintilari gizlenir
YEREL_ADRESLER = {"127.0.0.1", "localhost", "::1"}

KUYRUK_VARSAYILAN = Path(__file__).with_name("cevrimdisi_kuyruk.jsonl")
KUYRUK_MAKS = 1000            # dosya sismesin: en eski satirlar atilir
CEVRIMDISI_BEKLEME_SN = 5     # sunucuya ulasilamayinca bu sure boyunca hic denemeden "kapali" de
BOSALT_ARALIK_SN = 5
ZAMAN_ASIMI = (2, 3.5)        # (baglanti, okuma) sn. Arduino'nun bekleme suresinden KISA olmali


def ascii_yap(metin):
    """LCD Turkce karakter gostermez: ascii'ye cevir, '|' ve satir sonlarini temizle."""
    metin = str(metin).translate(ESLEME)
    metin = unicodedata.normalize("NFKD", metin).encode("ascii", "ignore").decode("ascii")
    return metin.replace("|", " ").replace("\n", " ").replace("\r", " ").strip()


def hata(mesaj):
    return "HATA|" + ascii_yap(mesaj)[:32]


def imza_alanlari(a, bas):
    """Komutun sonundaki istege bagli |sayac|mac (imzali kart, NTAG 424 DNA mantigi): API'ye aynen iletilir.
    RC522 imza uretemez; imza alani yoksa bos kalir ve sunucu moda gore karar verir (KART_IMZA_ZORUNLU)."""
    return {"sayac": a[bas], "mac": a[bas + 1]} if len(a) > bas + 1 else {}


def tutar_kisa(tutar):
    """'60000.00' -> '60000', '100.50' -> '100.50'"""
    return tutar[:-3] if tutar.endswith(".00") else tutar


def api_guvenli_mi(api):
    """PIN gonderilecek adres HTTPS olmali; duz HTTP yalnizca bu bilgisayarda kabul."""
    u = urlparse(api)
    return u.scheme == "https" or (u.hostname or "") in YEREL_ADRESLER


class Kuyruk:
    """Gunluk satirlarini bir JSON-lines dosyasinda tutar."""

    def __init__(self, yol):
        self.yol = Path(yol)

    def _oku(self):
        try:
            satirlar = self.yol.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        girdiler = []
        for s in satirlar:
            try:
                girdiler.append(json.loads(s))
            except ValueError:
                continue
        return girdiler

    def _yaz(self, girdiler):
        self.yol.write_text("".join(json.dumps(g, ensure_ascii=False) + "\n" for g in girdiler),
                            encoding="utf-8")

    def ekle(self, girdi):
        girdiler = self._oku()
        girdiler.append(girdi)
        self._yaz(girdiler[-KUYRUK_MAKS:])

    def ilk(self, n):
        return self._oku()[:n]

    def sil_ilk(self, n):
        self._yaz(self._oku()[n:])

    def bos_mu(self):
        return not self._oku()


class Kopru:
    def __init__(self, api, anahtar, ca_dosyasi=None, terminal="ATM-01", kuyruk_yolu=KUYRUK_VARSAYILAN):
        self.api = api.rstrip("/")
        self.oturum = requests.Session()
        self.oturum.headers.update({"X-Kopru-Anahtari": anahtar, "X-Terminal-Id": terminal})
        if ca_dosyasi:
            self.oturum.verify = ca_dosyasi  # ornegin mkcert'in rootCA.pem'i
        self.kuyruk = Kuyruk(kuyruk_yolu)
        self._kuyruk_dolu = not self.kuyruk.bos_mu()   # onceki calismadan kalan satirlar olabilir
        self._cevrimdisi = False
        self._cevrimdisi_basi = 0.0
        self._son_hata = 0.0
        self._son_bosaltma = 0.0
        self._son_istek = ("", 0.0)

    # ------------------------------------------------------------ cevrimdisi gunluk

    def _gunluk(self, olay, veri=None):
        self.kuyruk.ekle({
            "id": uuid.uuid4().hex,
            "zaman": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "olay": olay,
            "veri": veri or {},
        })
        self._kuyruk_dolu = True

    def _cevrimdisi_basladi(self):
        self._son_hata = time.monotonic()
        if not self._cevrimdisi:
            self._cevrimdisi = True
            self._cevrimdisi_basi = self._son_hata
            self._gunluk("cevrimdisi_basladi")

    def _cevrimdisi_bitti(self):
        if self._cevrimdisi:
            self._cevrimdisi = False
            self._gunluk("cevrimdisi_bitti", {"sure_sn": int(time.monotonic() - self._cevrimdisi_basi)})
        self._kuyrugu_bosalt()

    def _cevrimdisi_istegi_kaydet(self, komut, a):
        """Cevrimdisiyken gelen istegi denetim icin kaydeder (PIN/token/tutar YOK)."""
        simdi = time.monotonic()
        if self._son_istek[0] == komut and simdi - self._son_istek[1] < 3:
            return  # Arduino'nun hizli yeniden denemeleri tek satir sayilir
        self._son_istek = (komut, simdi)
        veri = {"komut": komut}
        if komut == "KART" and a:
            veri["uid"] = a[0][:20]
        self._gunluk("cevrimdisi_istek", veri)

    def _kuyrugu_bosalt(self, zorla=False):
        if not self._kuyruk_dolu:
            return
        simdi = time.monotonic()
        if not zorla and simdi - self._son_bosaltma < BOSALT_ARALIK_SN:
            return
        self._son_bosaltma = simdi
        while True:
            grup = self.kuyruk.ilk(100)
            if not grup:
                self._kuyruk_dolu = False
                return
            try:
                r = self.oturum.post(f"{self.api}/api/terminal-gunlugu/", json={"girdiler": grup},
                                     timeout=ZAMAN_ASIMI)
            except requests.RequestException:
                return
            if r.status_code == 200:
                self.kuyruk.sil_ilk(len(grup))
            elif r.status_code == 400:
                print("[kopru] Sunucu gunluk grubunu reddetti, grup atiliyor", file=sys.stderr)
                self.kuyruk.sil_ilk(len(grup))
            else:
                return  # 401/403/5xx: sunucu hazir olunca tekrar denenir

    # ------------------------------------------------------------ API

    def _post(self, yol, veri):
        if self._cevrimdisi and time.monotonic() - self._son_hata < CEVRIMDISI_BEKLEME_SN:
            return {"ok": False, "hata": "Sunucu kapali", "cevrimdisi": True}
        try:
            r = self.oturum.post(f"{self.api}{yol}", json=veri, timeout=ZAMAN_ASIMI)
        except requests.exceptions.SSLError:
            return {"ok": False, "hata": "TLS sertifikasi gecersiz"}
        except requests.exceptions.ConnectionError:   # baglanti kurulamadi / zaman asimi: sunucu yok
            self._cevrimdisi_basladi()
            return {"ok": False, "hata": "Sunucu kapali", "cevrimdisi": True}
        except requests.exceptions.ReadTimeout:
            # Sunucu ulasilabilir ama gec cevap verdi: istek uygulanmis olabilir. Cevrimdisi sayma,
            # Arduino ayni idempotency anahtariyla tekrar dener ve sonucu alir.
            return {"ok": False, "hata": "Sunucu yanitsiz"}
        except requests.RequestException:
            return {"ok": False, "hata": "Sunucu hatasi"}
        self._cevrimdisi_bitti()
        try:
            return r.json()
        except ValueError:
            return {"ok": False, "hata": f"Sunucu hatasi {r.status_code}"}

    def isle(self, satir):
        """Arduino satirini isler, Arduino'ya yazilacak cevap satirini dondurur."""
        parca = satir.strip().split("|")
        komut = parca[0].upper()
        if not self._cevrimdisi:
            self._kuyrugu_bosalt()  # onceki calismadan kalan satirlar varsa yukle
        try:
            cevap = self._isle(komut, parca[1:])
        except (IndexError, ValueError, KeyError):
            return hata("Gecersiz komut")
        if cevap.startswith("HATA|Sunucu kapali"):
            self._cevrimdisi_istegi_kaydet(komut, parca[1:])
        return cevap

    def _isle(self, komut, a):
        if komut == "KART":
            r = self._post("/api/kart/", {"uid": a[0], **imza_alanlari(a, 1)})
            return f"TIP|{r['tip']}" if r.get("ok") else hata(r.get("hata", "Hata"))

        if komut == "PIN":
            r = self._post("/api/giris/", {"uid": a[0], "pin": a[1], **imza_alanlari(a, 2)})
            if not r.get("ok"):
                return hata(r.get("hata", "Hata"))
            return f"GIRIS|{r['token']}|{ascii_yap(r['ad'])[:16]}"

        if komut == "PINDEGIS":
            r = self._post("/api/pin-degistir/", {"token": a[0], "eski": a[1], "yeni": a[2]})
            return "PINDEGIS|TAMAM" if r.get("ok") else hata(r.get("hata", "Hata"))

        if komut == "BAKIYE":
            r = self._post("/api/bakiye/", {"token": a[0]})
            return f"BAKIYE|{r['bakiye']}" if r.get("ok") else hata(r.get("hata", "Hata"))

        if komut == "ISLEM":
            # ISLEM|token|tur|tutar|hedef|anahtar   (hedef ve anahtar bos olabilir; tutar kurus icerebilir)
            token, tur, tutar = a[0], TUR[a[1].upper()], a[2]
            hedef = a[3] if len(a) > 3 else ""
            anahtar = a[4] if len(a) > 4 else ""
            r = self._post("/api/islem/", {"token": token, "tur": tur, "tutar": tutar,
                                           "hedef": hedef, "anahtar": anahtar})
            if not r.get("ok"):
                return hata(r.get("hata", "Hata"))
            if r["durum"] == "onay_bekliyor":
                return f"BEKLE|{r['talep_id']}|{r['gereken']}"
            if not r.get("basarili", True):
                return hata("Islem basarisiz")
            return f"TAMAM|{r['bakiye']}|{r['islem_id']}|{r.get('dagitim', '')}"

        if komut == "KASAAC":
            r = self._post("/api/kasa-ac/", {"uid": a[0], "pin": a[1], **imza_alanlari(a, 2)})
            if not r.get("ok"):
                return hata(r.get("hata", "Hata"))
            return f"BEKLE|{r['talep_id']}|{r['gereken']}"

        if komut == "BEKLEYEN":
            r = self._post("/api/bekleyen/", {})
            if not r.get("ok"):
                return hata(r.get("hata", "Hata"))
            t = r.get("talep")
            if not t:
                return "BEKLEYEN|YOK"
            tutar = "ac" if t["tur"] == "KASA_ACMA" else tutar_kisa(t["tutar"])
            return (f"BEKLEYEN|{t['talep_id']}|{TUR_KISA.get(t['tur'], t['tur'])}|"
                    f"{tutar}|{t['verilen']}|{t['gereken']}")

        if komut == "DURUM":
            r = self._post("/api/talep/", {"talep_id": int(a[0])})
            if not r.get("ok"):
                return hata(r.get("hata", "Hata"))
            ust = 1 if r["ust_yetkili_gerekli"] else 0
            return (f"DURUM|{r['verilen']}|{r['gereken']}|{r['durum']}|{r['kalan_sn']}|{ust}|{r['tur']}|"
                    f"{r['islem_id']}|{r.get('dagitim', '')}")

        if komut == "TESLIM":
            # TESLIM|islemId|A (para alindi) | H (alinmadi: para geri cekildi) | S (sikisti, para verilmedi)
            #                         | K|200x8+100x1 (eksik verildi: fiilen verilen banknotlar)
            durum = {"A": "ALINDI", "H": "ALINMADI", "S": "SIKISTI", "K": "EKSIK"}[a[1].upper()]
            r = self._post("/api/teslim/", {"islem_id": int(a[0]), "durum": durum,
                                            "verilen": a[2] if len(a) > 2 else ""})
            if not r.get("ok"):
                return hata(r.get("hata", "Hata"))
            return f"TESLIM|{r['teslim']}|{r.get('iade', '')}"

        if komut == "ONAY":
            karar = "RED" if a[3].upper() == "H" else "ONAY"
            r = self._post("/api/onay/", {"talep_id": int(a[0]), "uid": a[1], "pin": a[2], "karar": karar,
                                           **imza_alanlari(a, 4)})
            if not r.get("ok"):
                return hata(r.get("hata", "Hata"))
            return f"ONAY|{r['verilen']}|{r['gereken']}|{r['durum']}"

        return hata("Bilinmeyen komut")


def _gorunen(yuk):
    """PIN ve token loga yazilmasin."""
    komut = yuk.split("|")[0].upper()
    return komut + "|..." if komut in GIZLI_KOMUTLAR else yuk


def _yuk_ve_cevap_logu(oturum_isle):
    """Oturum.isle sarmalayici: islenen komutu (gizlenmis) ve cevabin turunu yazar."""
    def sarmal(yuk):
        cevap = oturum_isle(yuk)
        print(f"{_gorunen(yuk)}  ->  {cevap.split('|')[0]}")
        return cevap
    return sarmal


def konsol_dongusu(kopru, kati=False):
    print("Konsol modu: Arduino komutlarını yaz (örn. KART|a0a0a001; çerçeveli de olur). Çıkış: Ctrl+C", file=sys.stderr)
    oturum = Oturum(kopru.isle, kati=kati)
    for satir in sys.stdin:
        satir = satir.strip()
        if satir:
            cevap = oturum.satir_isle(satir)
            if cevap is not None:
                print(cevap, flush=True)


def seri_dongusu(kopru, port, baud, kati=False):
    import serial  # pyserial

    oturum = Oturum(_yuk_ve_cevap_logu(kopru.isle), kati=kati)
    with serial.Serial(port, baud, timeout=1) as sp:
        print(f"{port} açıldı, Arduino'nun açılması bekleniyor...", file=sys.stderr)
        time.sleep(2)  # port acilinca Uno resetlenir
        sp.reset_input_buffer()
        print("Hazır. Çıkış: Ctrl+C", file=sys.stderr)
        while True:
            ham = sp.readline()
            if not ham:
                continue
            satir = ham.decode("ascii", errors="ignore").strip()
            if not satir:
                continue
            if not cerceve_mi(satir) and not eski_komut_mu(satir):
                print(f"[arduino] {satir}")          # firmware'in hata ayiklama yazilari ya da bozuk satir
            cevap = oturum.satir_isle(satir)         # cerceve gibi bozuk satira NAK doner, digerleri yoksayilir
            if cevap is not None:
                sp.write((cevap + "\n").encode("ascii", "ignore"))


def main():
    ap = argparse.ArgumentParser(description="Arduino <-> Django koprusu")
    ap.add_argument("--port", help="Seri port (Windows: COM3, Linux: /dev/ttyUSB0)")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--api", default=os.environ.get("KOPRU_API", "http://127.0.0.1:8000"))
    ap.add_argument("--anahtar", default=os.environ.get("KOPRU_API_ANAHTARI", "gelistirme-kopru-anahtari"))
    ap.add_argument("--terminal", default=os.environ.get("KOPRU_TERMINAL", "ATM-01"),
                    help="Bu terminalin kodu (sunucuda kayitli olmali, ornegin ATM-01)")
    ap.add_argument("--kuyruk", default=str(KUYRUK_VARSAYILAN), help="Cevrimdisi gunluk dosyasi")
    ap.add_argument("--ca-dosyasi", help="Kendi imzali/mkcert sertifikasi icin kok sertifika (rootCA.pem)")
    ap.add_argument("--guvensiz-http", action="store_true",
                    help="Bu bilgisayar disindaki bir adrese DUZ HTTP ile baglanmaya izin ver (PIN acikta gider!)")
    ap.add_argument("--konsol", action="store_true", help="Seri port yerine klavyeden komut oku")
    ap.add_argument("--kati", action="store_true",
                    help="Yalnizca cerceveli (CRC'li, protokol v2) satirlari kabul et; eski cercevesiz firmware calismaz")
    args = ap.parse_args()

    if not api_guvenli_mi(args.api) and not args.guvensiz_http:
        ap.error("PIN'ler bu adrese duz HTTP ile gonderilirdi. https:// kullan "
                 "(bkz. backend/https_sunucu.py) ya da bilerek --guvensiz-http ver.")

    kopru = Kopru(args.api, args.anahtar, args.ca_dosyasi, args.terminal, args.kuyruk)
    try:
        if args.konsol:
            konsol_dongusu(kopru, args.kati)
        elif args.port:
            seri_dongusu(kopru, args.port, args.baud, args.kati)
        else:
            ap.error("--port COMx ya da --konsol ver")
    except KeyboardInterrupt:
        print("\nKapatıldı.", file=sys.stderr)


if __name__ == "__main__":
    main()
