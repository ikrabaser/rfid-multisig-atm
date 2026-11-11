"""İmzalı kart (klonlanamayan kimlik) SİMÜLASYONU: NTAG 424 DNA'nın "güvenli dinamik mesaj" (SDM) mantığı.

Sorun: RC522 + MIFARE Classic kartta yalnızca UID okunur ve UID kopyalanabilir. UID'yi kopyalayan biri kartın "kendisi"
gibi davranır; PIN'i de bilirse (omuz sörfü) parayı çeker, yetkili kartıysa onay verir.

Çözüm (gerçek donanımda NTAG 424 DNA): kartın çipi her okutmada artan bir SAYAÇ tutar ve (UID, sayaç) üzerinden, çipin
İÇİNDEKİ gizli anahtarla bir MAC üretir. Anahtar çipten çıkmaz; UID'yi kopyalamak anahtarı kopyalamaz. Sunucu aynı
anahtarı tutar, MAC'i doğrular ve sayacın ÖNCEKİNDEN BÜYÜK olmasını ister (eski bir okutmanın tekrar oynatılması reddedilir).

Burada: kartın anahtarı, ana anahtardan UID ile türetilir (`KART_IMZA_ANAHTARI`; veritabanında DEĞİLDİR):
    cip_anahtari = HMAC(ana_anahtar, "banka.kart-cipi|" + uid)        (çipin içindeki anahtar)
    mac          = HMAC(cip_anahtari, uid + "|" + sayac)[:16 hex]
Sunucu yalnızca `imza_sayaci` ve `imza_zamani` tutar. Doğrulama: mac geçerli VE (sayac > kayıtlı sayaç, ya da sayac ==
kayıtlı sayaç ve `KART_IMZA_GECERLILIK_SN` içinde: aynı okutma KART, PIN ve ONAY adımlarında yeniden sunulur).

Modlar: `KART_IMZA_ZORUNLU` kapalıyken imzasız kart kabul edilir (RC522'li gerçek donanım imza üretemez), ama
imza GÖNDERİLMİŞ ve geçersizse her zaman reddedilir. Açıkken imzasız kart reddedilir.

Dürüst sınırlar:
  * RC522 NTAG 424 DNA'nın AES kimlik doğrulamasını yapamaz; gerçek donanım için PN532 gibi bir okuyucu gerekir.
    Arduino kodu imza üretmez; imza simülatörde ("kart çipi" simülasyonu) oynatılır.
  * Aynı okutmanın imzası pencere içinde (varsayılan 90 sn) KART/PIN/ONAY adımları için yeniden kabul edilir; bu sürede
    ele geçirilmiş bir imza PIN olmadan işe yaramaz ama tek başına PIN şartını kaldırmaz.
  * Ana anahtar sızarsa tüm kartların anahtarı türetilebilir; gerçek sistemde anahtarlar HSM'de tutulur.
"""
import hashlib
import hmac
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .hashlog import kayit_ekle
from .models import LogKaydi


class ImzaHatasi(Exception):
    def __init__(self, mesaj, kod):
        super().__init__(mesaj)
        self.mesaj = mesaj
        self.kod = kod


def cip_anahtari(uid):
    """Kartın çipindeki gizli anahtar (simülasyon: ana anahtardan UID ile türetilir)."""
    return hmac.new(settings.KART_IMZA_ANAHTARI.encode("utf-8"), f"banka.kart-cipi|{uid}".encode("utf-8"),
                    hashlib.sha256).digest()


def mac_hesapla(uid, sayac):
    return hmac.new(cip_anahtari(uid), f"{uid}|{int(sayac)}".encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def coz(sayac, mac):
    """Protokol alanlarından (sayac, mac) çifti; ikisi de boşsa None (imza gönderilmemiş)."""
    sayac, mac = str(sayac or "").strip(), str(mac or "").strip().lower()
    if not sayac and not mac:
        return None
    if not sayac.isdigit() or len(sayac) > 18 or not (len(mac) == 16 and all(c in "0123456789abcdef" for c in mac)):
        raise ImzaHatasi("Kart imzası geçersiz biçimde", "imza")
    return int(sayac), mac


def _ihlal_logla(olay, uid):
    """Aynı karttan aynı ihlal 10 dakikada bir loglanır (zincir şişmesin)."""
    sinir = timezone.now() - timedelta(minutes=10)
    if LogKaydi.objects.filter(olay_turu=olay, zaman__gte=sinir, veri__contains=f'"uid":"{uid}"').exists():
        return
    kayit_ekle(olay, {"uid": uid})


def kontrol(nesne, uid, imza):
    """Kart (Kart ya da Personel) için imzayı doğrular; sayaç ilerlemesi atomik (karşılaştır-ve-yaz) yapılır.

    Başarılıysa None; değilse ImzaHatasi. ihlaller zincire yazılır (anomali taraması sayar).
    """
    if imza is None:
        if settings.KART_IMZA_ZORUNLU:
            _ihlal_logla("kart_imzasiz", uid)
            raise ImzaHatasi("Kart doğrulanamadı (imza yok)", "imza")
        return
    sayac, mac = imza
    if not hmac.compare_digest(mac, mac_hesapla(uid, sayac)):
        _ihlal_logla("kart_imza_gecersiz", uid)
        raise ImzaHatasi("Kart doğrulanamadı (klon şüphesi)", "imza")
    simdi = timezone.now()
    model = type(nesne)
    if model.objects.filter(pk=nesne.pk, imza_sayaci__lt=sayac).update(imza_sayaci=sayac, imza_zamani=simdi):
        return                                                    # yeni okutma: sayaç ilerledi
    taze = model.objects.filter(pk=nesne.pk).values("imza_sayaci", "imza_zamani").first()
    if (taze and taze["imza_sayaci"] == sayac and taze["imza_zamani"]
            and simdi - taze["imza_zamani"] <= timedelta(seconds=settings.KART_IMZA_GECERLILIK_SN)):
        return                                                    # aynı okutma, aynı akış içinde yeniden sunuldu
    _ihlal_logla("kart_tekrar_oynatma", uid)
    raise ImzaHatasi("Kart doğrulanamadı (tekrar oynatma)", "imza_tekrar")
