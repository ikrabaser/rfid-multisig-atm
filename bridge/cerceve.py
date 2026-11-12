"""Seri hat cercevelemesi (protokol v2): CRC16 + sira numarasi + tekrar onbellegi.

Neden: USB seri hat gurultuye (kablo, kotu temas, Arduino resetleri) acik; eski duz satir protokolunde
bozuk bir satir sessizce yanlis komut olabilir ve zaman asimina ugrayan bir cevap SONRAKI istegin cevabi sanilirdi
(hat kayardi). Cerceve bunu onler:

    SS:yuk*CCCC\\n          SS  : 2 haneli buyuk harf onaltilik sira numarasi (cevap, istegin numarasini aynen yansitir)
                           yuk : eski protokoldeki satir (KART|uid, PIN|uid|pin ...). '*' icerebilir
                           CCCC: "SS:yuk" baytlarinin CRC-16/CCITT-FALSE'i, 4 haneli buyuk harf onaltilik

* Bozuk cerceve (CRC tutmaz): kopru "--:NAK|CRC" yollar, terminal hemen yeniden dener.
* Ayni numara + ayni yuk tekrar gelirse (terminal cevabi alamadi, yeniden yolladi) kopru ISTEGI TEKRAR ISLEMEZ,
  onbellekteki cevabi yollar: PIN sayaci, onay oyu gibi yan etkili komutlar iki kez calismaz.
* "HELLO|surum" terminal acilisinda gelir: onbellek temizlenir (terminal resetlenince numaralar basa doner).
* "PING" baglanti sagligi icindir ("PONG" doner).
* Cerceve gormeden once eski (cercevesiz) satirlar kabul edilir (eski firmware uyumlulugu); ilk gecerli cerceveden
  sonra cercevesiz satirlar reddedilir (yarim kalmis bir cerceve komut sanilmasin). `kati=True`: hic kabul edilmez.
"""
import re
from collections import OrderedDict

SURUM = 2
YUK_EN_COK = 118          # firmware ile ayni: cerceve en fazla 128 bayt
ONBELLEK_BOYUTU = 8
KOMUTLAR = {"KART", "PIN", "PINDEGIS", "BAKIYE", "ISLEM", "BEKLEYEN", "DURUM", "ONAY", "KASAAC", "TESLIM"}
NAK_SIRA = "--"

_CERCEVE = re.compile(r"^([0-9A-Fa-f]{2}|--):(.*)\*([0-9A-Fa-f]{4})$", re.S)


class CerceveHatasi(ValueError):
    pass


def crc16(veri):
    """CRC-16/CCITT-FALSE (poly 0x1021, baslangic 0xFFFF). Standart kontrol degeri: crc16(b"123456789") == 0x29B1."""
    if isinstance(veri, str):
        veri = veri.encode("ascii")
    crc = 0xFFFF
    for b in veri:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def kodla(sira, yuk):
    """(sira, yuk) -> 'SS:yuk*CCCC' (sonunda satir sonu YOK). sira: 0..255 ya da NAK_SIRA."""
    if len(yuk) > YUK_EN_COK:
        raise CerceveHatasi("yuk cok uzun")
    if any(ord(c) < 0x20 or ord(c) > 0x7E for c in yuk):
        raise CerceveHatasi("yuk yalnizca yazdirilabilir ASCII olabilir")
    ss = NAK_SIRA if sira == NAK_SIRA else f"{sira:02X}"
    govde = f"{ss}:{yuk}"
    return f"{govde}*{crc16(govde):04X}"


def cerceve_mi(satir):
    return satir.isascii() and bool(_CERCEVE.match(satir))


def eski_komut_mu(satir):
    """Eski (cercevesiz) protokolun gecerli bir komut satiri mi: ilk alan bilinen bir komut."""
    return satir.split("|")[0].upper() in KOMUTLAR


def bozuk_cerceve_gibi_mi(satir):
    """Gurultuyle bozulmus cerceveye benziyor ('SS:...*...') ama CRC/bicim tutmuyor."""
    return "*" in satir and ":" in satir[:3]


def coz(satir):
    """'SS:yuk*CCCC' -> (sira, yuk). Bozuksa CerceveHatasi."""
    if not satir.isascii():
        raise CerceveHatasi("ascii disi karakter")
    m = _CERCEVE.match(satir)
    if not m:
        raise CerceveHatasi("cerceve degil")
    ss, yuk, crc = m.group(1), m.group(2), int(m.group(3), 16)
    if crc16(f"{ss}:{yuk}") != crc:
        raise CerceveHatasi("crc uyusmuyor")
    return (NAK_SIRA if ss == "--" else int(ss, 16)), yuk


class Oturum:
    """Bir seri baglantinin cerceve durumu. `isle`: yuk (eski satir) -> cevap satiri (Kopru.isle)."""

    def __init__(self, isle, kati=False):
        self.isle = isle
        self.kati = kati
        self._onbellek = OrderedDict()      # sira -> (yuk, cevap)
        self._cerceve_goruldu = False
        self.istatistik = {"cerceve": 0, "bozuk": 0, "tekrar": 0, "eski_satir": 0, "reddedilen": 0, "yoksayilan": 0, "yanit_hatasi": 0}

    def satir_isle(self, satir):
        """Seriden gelen bir satiri isler. Arduino'ya yazilacak satiri (sonu satir sonu olmadan) ya da None dondurur."""
        satir = satir.strip()
        if not satir:
            return None
        if not cerceve_mi(satir):
            return self._cercevesiz(satir)
        try:
            sira, yuk = coz(satir)
        except CerceveHatasi:
            self.istatistik["bozuk"] += 1
            return kodla(NAK_SIRA, "NAK|CRC")
        if sira == NAK_SIRA:
            return None
        self._cerceve_goruldu = True
        self.istatistik["cerceve"] += 1

        ad = yuk.split("|")[0].upper()
        if ad == "HELLO":
            self._onbellek.clear()
            surum = yuk.split("|")[1] if "|" in yuk else ""
            cevap = f"HELLO|OK|{SURUM}" if surum == str(SURUM) else f"HELLO|UYUMSUZ|{SURUM}"
            return kodla(sira, cevap)
        if ad == "PING":
            return kodla(sira, "PONG")

        onceki = self._onbellek.get(sira)
        if onceki and onceki[0] == yuk:
            self.istatistik["tekrar"] += 1
            return self._cevap_kodla(sira, onceki[1])
        cevap = self.isle(yuk)
        self._onbellek[sira] = (yuk, cevap)
        self._onbellek.move_to_end(sira)
        while len(self._onbellek) > ONBELLEK_BOYUTU:
            self._onbellek.popitem(last=False)
        return self._cevap_kodla(sira, cevap)

    def _cevap_kodla(self, sira, cevap):
        """Cevap cerceveye sigmiyorsa (cok uzun / ASCII disi) hatayi terminale bildir; koprunun cokmesine izin verme."""
        try:
            return kodla(sira, cevap)
        except CerceveHatasi:
            self.istatistik["yanit_hatasi"] += 1
            return kodla(sira, "HATA|Yanit gecersiz")

    def _cercevesiz(self, satir):
        """Eski protokol: cevap da cercevesiz. Yalnizca BILINEN komutlar ve yalnizca ilk cerceveden once kabul edilir.

        Bozulmus bir cerceve ("04:BAKIYE|x*65zD") asla komut sayilmaz: ilk alani komut degildir ("04:BAKIYE").
        Cerceve gibi gorunen bozuk satira NAK donulur (terminal hemen yeniden dener); diger satirlar (firmware'in hata
        ayiklama yazilari) yoksayilir.
        """
        if bozuk_cerceve_gibi_mi(satir):
            self.istatistik["bozuk"] += 1
            return kodla(NAK_SIRA, "NAK|CRC")
        if not eski_komut_mu(satir):
            self.istatistik["yoksayilan"] += 1
            return None
        if self.kati or self._cerceve_goruldu:
            self.istatistik["reddedilen"] += 1
            return None
        self.istatistik["eski_satir"] += 1
        return self.isle(satir)
