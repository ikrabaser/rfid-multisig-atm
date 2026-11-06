#!/usr/bin/env python3
"""Arduino <-> Django kopru programi.

Arduino seri porttan satir satir komut yollar (KART|uid, PIN|uid|pin ...), bu program
bunlari Django API'ye cevirir ve cevabi tekrar seri porttan Arduino'ya yazar.
Protokolun tamami arduino/atm_terminal/atm_terminal.ino basindaki yorumda.

Kullanim:
    python serial_bridge.py --port COM3                 # gercek Arduino
    python serial_bridge.py --konsol                    # Arduino'suz, klavyeden dene

HTTPS (PIN trafigi sifreli gitsin):
    python serial_bridge.py --port COM3 --api https://127.0.0.1:8443 --ca-dosyasi rootCA.pem

Ayarlar ortam degiskeniyle de verilebilir: KOPRU_API (adres), KOPRU_API_ANAHTARI.
Dikkat: Seri port ayni anda tek programda acik olabilir. Arduino IDE'nin Serial
Monitor'unu kapat.
"""
import argparse
import os
import sys
import time
import unicodedata
from urllib.parse import urlparse

import requests

ESLEME = str.maketrans({"ı": "i", "İ": "I", "ß": "ss"})
TUR = {"C": "CEKME", "Y": "YATIRMA", "H": "HAVALE"}
TUR_KISA = {"CEKME": "Cek", "YATIRMA": "Yatir", "HAVALE": "Havale", "KASA_ACMA": "Kasa"}
KOMUTLAR = {"KART", "PIN", "BAKIYE", "ISLEM", "BEKLEYEN", "DURUM", "ONAY", "KASAAC"}
GIZLI_KOMUTLAR = {"PIN", "ONAY", "BAKIYE", "ISLEM", "KASAAC"}  # konsola yazilirken ayrintilari gizlenir
YEREL_ADRESLER = {"127.0.0.1", "localhost", "::1"}


def ascii_yap(metin):
    """LCD Turkce karakter gostermez: ascii'ye cevir, '|' ve satir sonlarini temizle."""
    metin = str(metin).translate(ESLEME)
    metin = unicodedata.normalize("NFKD", metin).encode("ascii", "ignore").decode("ascii")
    return metin.replace("|", " ").replace("\n", " ").replace("\r", " ").strip()


def hata(mesaj):
    return "HATA|" + ascii_yap(mesaj)[:32]


def api_guvenli_mi(api):
    """PIN gonderilecek adres HTTPS olmali; duz HTTP yalnizca bu bilgisayarda kabul."""
    u = urlparse(api)
    return u.scheme == "https" or (u.hostname or "") in YEREL_ADRESLER


class Kopru:
    def __init__(self, api, anahtar, ca_dosyasi=None):
        self.api = api.rstrip("/")
        self.oturum = requests.Session()
        self.oturum.headers.update({"X-Kopru-Anahtari": anahtar})
        if ca_dosyasi:
            self.oturum.verify = ca_dosyasi  # ornegin mkcert'in rootCA.pem'i

    def _post(self, yol, veri):
        try:
            r = self.oturum.post(f"{self.api}{yol}", json=veri, timeout=8)
        except requests.exceptions.SSLError:
            return {"ok": False, "hata": "TLS sertifikasi gecersiz"}
        except requests.RequestException:
            return {"ok": False, "hata": "Sunucuya ulasilamadi"}
        try:
            return r.json()
        except ValueError:
            return {"ok": False, "hata": f"Sunucu hatasi {r.status_code}"}

    def isle(self, satir):
        """Arduino satirini isler, Arduino'ya yazilacak cevap satirini dondurur."""
        parca = satir.strip().split("|")
        komut = parca[0].upper()
        try:
            return self._isle(komut, parca[1:])
        except (IndexError, ValueError, KeyError):
            return hata("Gecersiz komut")

    def _isle(self, komut, a):
        if komut == "KART":
            r = self._post("/api/kart/", {"uid": a[0]})
            return f"TIP|{r['tip']}" if r.get("ok") else hata(r.get("hata", "Hata"))

        if komut == "PIN":
            r = self._post("/api/giris/", {"uid": a[0], "pin": a[1]})
            if not r.get("ok"):
                return hata(r.get("hata", "Hata"))
            return f"GIRIS|{r['token']}|{ascii_yap(r['ad'])[:16]}"

        if komut == "BAKIYE":
            r = self._post("/api/bakiye/", {"token": a[0]})
            return f"BAKIYE|{r['bakiye']}" if r.get("ok") else hata(r.get("hata", "Hata"))

        if komut == "ISLEM":
            # ISLEM|token|tur|tutar|hedef|anahtar   (hedef ve anahtar bos olabilir)
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
            return f"TAMAM|{r['bakiye']}"

        if komut == "KASAAC":
            r = self._post("/api/kasa-ac/", {"uid": a[0], "pin": a[1]})
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
            tutar = "ac" if t["tur"] == "KASA_ACMA" else t["tutar"].split(".")[0]
            return (f"BEKLEYEN|{t['talep_id']}|{TUR_KISA.get(t['tur'], t['tur'])}|"
                    f"{tutar}|{t['verilen']}|{t['gereken']}")

        if komut == "DURUM":
            r = self._post("/api/talep/", {"talep_id": int(a[0])})
            if not r.get("ok"):
                return hata(r.get("hata", "Hata"))
            ust = 1 if r["ust_yetkili_gerekli"] else 0
            return f"DURUM|{r['verilen']}|{r['gereken']}|{r['durum']}|{r['kalan_sn']}|{ust}|{r['tur']}"

        if komut == "ONAY":
            karar = "RED" if a[3].upper() == "H" else "ONAY"
            r = self._post("/api/onay/", {"talep_id": int(a[0]), "uid": a[1], "pin": a[2], "karar": karar})
            if not r.get("ok"):
                return hata(r.get("hata", "Hata"))
            return f"ONAY|{r['verilen']}|{r['gereken']}|{r['durum']}"

        return hata("Bilinmeyen komut")


def konsol_dongusu(kopru):
    print("Konsol modu: Arduino komutlarını yaz (örn. KART|a0a0a001). Çıkış: Ctrl+C", file=sys.stderr)
    for satir in sys.stdin:
        satir = satir.strip()
        if satir:
            print(kopru.isle(satir), flush=True)


def seri_dongusu(kopru, port, baud):
    import serial  # pyserial

    with serial.Serial(port, baud, timeout=1) as sp:
        print(f"{port} açıldı, Arduino'nun açılması bekleniyor...", file=sys.stderr)
        time.sleep(2)  # port acilinca Uno resetlenir
        sp.reset_input_buffer()
        print("Hazır. Çıkış: Ctrl+C", file=sys.stderr)
        while True:
            ham = sp.readline()
            if not ham:
                continue
            satir = ham.decode("utf-8", errors="ignore").strip()
            if not satir:
                continue
            komut = satir.split("|")[0].upper()
            if komut not in KOMUTLAR:
                print(f"[arduino] {satir}")
                continue
            cevap = kopru.isle(satir)
            # PIN ve token loga yazilmasin
            gorunen = komut + "|..." if komut in GIZLI_KOMUTLAR else satir
            print(f"{gorunen}  ->  {cevap.split('|')[0]}")
            sp.write((cevap + "\n").encode("ascii", "ignore"))


def main():
    ap = argparse.ArgumentParser(description="Arduino <-> Django koprusu")
    ap.add_argument("--port", help="Seri port (Windows: COM3, Linux: /dev/ttyUSB0)")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--api", default=os.environ.get("KOPRU_API", "http://127.0.0.1:8000"))
    ap.add_argument("--anahtar", default=os.environ.get("KOPRU_API_ANAHTARI", "gelistirme-kopru-anahtari"))
    ap.add_argument("--ca-dosyasi", help="Kendi imzali/mkcert sertifikasi icin kok sertifika (rootCA.pem)")
    ap.add_argument("--guvensiz-http", action="store_true",
                    help="Bu bilgisayar disindaki bir adrese DUZ HTTP ile baglanmaya izin ver (PIN acikta gider!)")
    ap.add_argument("--konsol", action="store_true", help="Seri port yerine klavyeden komut oku")
    args = ap.parse_args()

    if not api_guvenli_mi(args.api) and not args.guvensiz_http:
        ap.error("PIN'ler bu adrese duz HTTP ile gonderilirdi. https:// kullan "
                 "(bkz. backend/https_sunucu.py) ya da bilerek --guvensiz-http ver.")

    kopru = Kopru(args.api, args.anahtar, args.ca_dosyasi)
    try:
        if args.konsol:
            konsol_dongusu(kopru)
        elif args.port:
            seri_dongusu(kopru, args.port, args.baud)
        else:
            ap.error("--port COMx ya da --konsol ver")
    except KeyboardInterrupt:
        print("\nKapatıldı.", file=sys.stderr)


if __name__ == "__main__":
    main()
