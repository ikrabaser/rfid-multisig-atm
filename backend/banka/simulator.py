"""Web ATM simülatörünün sunucu tarafı.

Simülatör, firmware'in (ve köprünün) konuştuğu SATIR PROTOKOLÜNÜ aynen kullanır: tarayıcıdaki sanal terminal
`KART|uid`, `PIN|uid|pin`, `ISLEM|...` gibi satırlar yollar; burada bunlar doğrudan servis katmanına çevrilir ve
firmware'in beklediği cevap satırları (`TIP|MUSTERI`, `GIRIS|token|ad`, `BEKLE|talep|gereken` ...) döner. Yani
simülatörde yaptığın her şey (defter, bütünlük kontrolleri, onay kuralları, log) gerçek akışla birebir aynıdır.

Köprü API anahtarı tarayıcıya VERİLMEZ: simülatör oturum açmış bir yönetim kullanıcısıyla, süreç içinde çalışır.
Yalnızca geliştirme içindir: `SIMULATOR_AKTIF` (varsayılan: DEBUG) kapalıysa hiçbir uç çalışmaz.
"""
import json
import re
import unicodedata

from django.conf import settings

from . import (anomali, baglam, kart_butunlugu, nakit, personel_butunlugu, politika_degisikligi, services, sms,
               terminal as terminal_modulu)
from .models import Hesap, HesapHareketi, Islem, Kart, LogKaydi, OnayTalebi, Personel, Rol, Terminal
from .services import IslemHatasi

ESLEME = str.maketrans({"ı": "i", "İ": "I", "ß": "ss"})
TUR = {"C": "CEKME", "Y": "YATIRMA", "H": "HAVALE"}
TUR_KISA = {"CEKME": "Cek", "YATIRMA": "Yatir", "HAVALE": "Havale", "KASA_ACMA": "Kasa"}
# TESLIM|id|A (alindi) | H (alinmadi) | S (sikisti) | K|banknotlar (eksik verildi)
TESLIM_DURUMU = {"A": "ALINDI", "H": "ALINMADI", "S": "SIKISTI", "K": "EKSIK"}


def aktif_mi():
    return bool(getattr(settings, "SIMULATOR_AKTIF", settings.DEBUG))


def ascii_yap(metin):
    """LCD Türkçe karakter göstermez (köprüdeki ile aynı dönüşüm)."""
    metin = str(metin).translate(ESLEME)
    metin = unicodedata.normalize("NFKD", metin).encode("ascii", "ignore").decode("ascii")
    return metin.replace("|", " ").replace("\n", " ").replace("\r", " ").strip()


def hata(mesaj):
    return "HATA|" + ascii_yap(mesaj)[:32]


def tutar_kisa(tutar):
    return tutar[:-3] if tutar.endswith(".00") else tutar


def isle(satir, terminal_kodu):
    """Bir firmware satırını işler, firmware'e yazılacak cevap satırını döndürür."""
    parca = str(satir).strip().split("|")
    komut = parca[0].upper()
    try:
        terminal = terminal_modulu.terminal_coz(terminal_kodu)
        with baglam.terminal_baglami(terminal.kod):
            return _isle(komut, parca[1:])
    except IslemHatasi as e:
        return hata(e.mesaj)
    except (IndexError, ValueError, KeyError):
        return hata("Gecersiz komut")


def _isle(komut, a):
    if komut == "KART":
        return f"TIP|{services.kart_tipi(a[0])}"

    if komut == "PIN":
        g = services.musteri_giris(a[0], a[1])
        return f"GIRIS|{g['token']}|{ascii_yap(g['ad'])[:16]}"

    if komut == "BAKIYE":
        return f"BAKIYE|{services.bakiye_sorgula(a[0])['bakiye']}"

    if komut == "ISLEM":
        # ISLEM|token|C/Y/H|tutar|hedef|anahtar  (hedef ve anahtar bos olabilir)
        tur = TUR[a[1].upper()]
        hedef = a[3] if len(a) > 3 else ""
        anahtar = a[4] if len(a) > 4 else ""
        r = services.islem_baslat(a[0], tur, a[2], hedef, anahtar)
        if r["durum"] == "onay_bekliyor":
            return f"BEKLE|{r['talep_id']}|{r['gereken']}"
        if not r.get("basarili", True):
            return hata("Islem basarisiz")
        return f"TAMAM|{r['bakiye']}|{r['islem_id']}|{r.get('dagitim', '')}"

    if komut == "KASAAC":
        r = services.kasa_acma_baslat(a[0], a[1])
        return f"BEKLE|{r['talep_id']}|{r['gereken']}"

    if komut == "BEKLEYEN":
        t = services.bekleyen_talep()
        if not t:
            return "BEKLEYEN|YOK"
        tutar = "ac" if t["tur"] == "KASA_ACMA" else tutar_kisa(t["tutar"])
        return f"BEKLEYEN|{t['talep_id']}|{TUR_KISA.get(t['tur'], t['tur'])}|{tutar}|{t['verilen']}|{t['gereken']}"

    if komut == "DURUM":
        r = services.talep_durumu(int(a[0]))
        ust = 1 if r["ust_yetkili_gerekli"] else 0
        return (f"DURUM|{r['verilen']}|{r['gereken']}|{r['durum']}|{r['kalan_sn']}|{ust}|{r['tur']}|"
                f"{r['islem_id']}|{r.get('dagitim', '')}")

    if komut == "ONAY":
        karar = "RED" if a[3].upper() == "H" else "ONAY"
        r = services.onay_karar(int(a[0]), a[1], a[2], karar)
        return f"ONAY|{r['verilen']}|{r['gereken']}|{r['durum']}"

    if komut == "TESLIM":
        durum = TESLIM_DURUMU[a[1].upper()]
        r = services.teslim_bildir(int(a[0]), durum, a[2] if len(a) > 2 else None)
        return f"TESLIM|{r['teslim']}|{r['iade']}"

    return hata("Bilinmeyen komut")


# ------------------------------------------------------------------ sayfa ve serit verileri

def _demo_pinler():
    """Yalnızca DEBUG'da: seed_demo'nun sabit demo PIN'leri (hâlâ geçerliyse ipucu olarak gösterilir)."""
    if not settings.DEBUG:
        return {}, {}
    from .management.commands import seed_demo
    return ({uid: pin for _, _, _, uid, pin in seed_demo.MUSTERILER},
            {uid: pin for _, uid, _, pin, _, _ in seed_demo.PERSONEL})


def sayfa_verisi():
    musteri_pin, personel_pin = _demo_pinler()
    musteriler = []
    for k in Kart.objects.select_related("hesap__musteri").order_by("id")[:30]:
        pin = musteri_pin.get(k.uid)
        musteriler.append({
            "uid": k.uid, "ad": k.hesap.musteri.ad, "hesap_no": k.hesap.hesap_no, "bloke": k.bloke,
            "ipucu": pin if pin and k.pin_dogru_mu(pin) else "",
        })
    yetkililer = []
    for p in Personel.objects.filter(aktif=True).order_by("id")[:30]:
        pin = personel_pin.get(p.kart_uid)
        yetkililer.append({
            "uid": p.kart_uid, "ad": p.ad, "rol": Rol(p.rol).label, "bloke": p.bloke,
            "ipucu": pin if pin and p.pin_dogru_mu(pin) else "",
        })
    return {
        "musteriler": musteriler,
        "yetkililer": yetkililer,
        "terminaller": [{"kod": t.kod, "ad": t.ad} for t in Terminal.objects.filter(aktif=True)],
        "ipuclari_acik": bool(musteri_pin or personel_pin),
    }


_HASH_DESENI = re.compile(r"^[0-9a-f]{32,}$")


def _log_degeri(deger):
    """Denetim kaydi ozetinde tek bir degeri okunur kisaltir."""
    if isinstance(deger, str):
        if _HASH_DESENI.match(deger):
            return deger[:12] + "…"
        if len(deger) > 48:
            return deger[:45] + "…"
    return deger


def _log_ozeti(veri):
    """Log verisini 'anahtar=deger' satirina cevirir; bos alanlari atlar, hash'leri kisaltir."""
    try:
        v = json.loads(veri)
    except ValueError:
        return ""
    if not isinstance(v, dict):
        return ""
    parcalar = []
    for anahtar, deger in v.items():
        if deger is None or deger == "" or isinstance(deger, (dict, list)):
            continue
        parcalar.append(f"{anahtar}={_log_degeri(deger)}")
    return ", ".join(parcalar)[:200]


def serit_verisi(zincir=False):
    """Simülatörün sağındaki canlı şerit için küçük bir özet."""
    from django.utils import timezone

    from .hashlog import zinciri_dogrula

    services.suresi_dolanlari_kapat()
    bekleyenler = [services.talep_durumu(t) for t in
                   OnayTalebi.objects.filter(durum=OnayTalebi.Durum.BEKLIYOR).values_list("id", flat=True)]
    veri = {
        "hesaplar": [
            {"hesap_no": h.hesap_no, "ad": h.musteri.ad, "tur": h.tur, "bakiye": f"{h.bakiye:.2f}",
             "limit": f"{h.gunluk_limit:.2f}"}
            for h in Hesap.objects.select_related("musteri").order_by("hesap_no")[:12]
        ],
        "bekleyenler": bekleyenler,
        "loglar": [
            {"sira": l.sira, "olay": l.olay_turu, "ozet": _log_ozeti(l.veri),
             "zaman": timezone.localtime(l.zaman).strftime("%H:%M:%S")}
            for l in LogKaydi.objects.order_by("-sira")[:14]
        ],
        "hareketler": [
            {"islem": h.islem_id, "hesap": h.hesap.hesap_no, "yon": h.yon, "tutar": f"{h.tutar:.2f}",
             "bakiye": f"{h.bakiye_sonrasi:.2f}"}
            for h in HesapHareketi.objects.select_related("hesap")[:8]
        ],
        "durumlar": {
            "politika": politika_degisikligi.butunluk_durumu(),
            "personel": personel_butunlugu.durum(),
            "kart": kart_butunlugu.durum(),
        },
        "anomali_sayisi": len(anomali.tara()),
        "kasetler": nakit.stok_ozeti(),
        "dagiticilar": {t.kod: t.nakit_dagitimi for t in Terminal.objects.all()},
        "belirsiz": [
            {"islem_id": i.id, "tutar": f"{i.tutar:.2f}", "terminal": nakit.terminal_kodu(i.terminal),
             "plan": i.dagitim_plani}
            for i in Islem.objects.filter(tur=Islem.Tur.CEKME, durum=Islem.Durum.UYGULANDI,
                                          teslim=Islem.Teslim.BELIRSIZ)[:10]
        ],
    }
    if zincir:
        veri["zincir"] = zinciri_dogrula()
    return veri


def telefon_mesajlari():
    """Simülasyon 'telefonlar' (SMS gelen kutusu): yalnızca dosya sağlayıcıda anlamlıdır."""
    if not str(settings.SMS_SAGLAYICI).endswith("DosyaSmsSaglayici"):
        return []
    return sms.kutuyu_oku()[-20:]


def operator_eylemi(kullanici, govde):
    """Simülatörün 'operatör' düğmeleri: belirsiz teslimi sayımla çöz, dağıtıcıyı yeniden aç.

    Yalnızca simülatör (superuser, SIMULATOR_AKTIF) içindir; yapan kişi hash zincirine yazılır.
    """
    yapan = f"simulator:{kullanici.get_username()}"
    eylem = govde.get("eylem")
    try:
        if eylem == "coz":
            r = services.nakit_coz(govde.get("islem_id"), govde.get("sonuc"), yapan,
                                   "Simülatörden operatör sayımı", govde.get("verilen"))
        elif eylem == "dagitici_ac":
            r = services.dagitici_ac(govde.get("terminal"), yapan, "Simülatörden teknik servis onayı")
        else:
            return {"ok": False, "hata": "Bilinmeyen eylem"}
    except IslemHatasi as e:
        return {"ok": False, "hata": e.mesaj}
    return {"ok": True, **r}
