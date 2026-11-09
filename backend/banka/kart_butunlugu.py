"""Müşteri kartlarının bütünlüğü: satır başına HMAC mührü + onaylı özet.

Tehdit: Veritabanına doğrudan sahte bir kart eklenir (bilinen PIN'li), bir kartın PIN'i değiştirilir ya da kart başka
bir hesaba taşınırsa o hesabın parası çalınabilir.

Çözüm iki katmanlı ve ÖLÇEKLENEBİLİR (kart sayısı arttıkça log büyümez, kontrol kartla aynı hızda kalır):
  1. Her kartın satırında bir MÜHÜR (`Kart.mac`) vardır: HMAC(anahtar, uid | hesap no | PIN hash özeti). Mührü yalnızca
     onaylı işlemler (kart tanımlama, PIN sıfırlama) ve ilk kurulum üretir; anahtar veritabanında DEĞİLDİR
     (KART_MAC_ANAHTARI). Terminalde kart okutulunca/giriş yapılınca yalnızca o kartın mührü kontrol edilir: O(1).
     Mührü geçersiz kart "bloke" gibi davranır; DİĞER MÜŞTERİLER ETKİLENMEZ.
  2. Her onaylı kart değişikliğinde yalnızca küçük bir ÖZET hash zincirine yazılır (tüm mühürlerin özeti ve kart sayısı;
     satır listesi yazılmaz). Mutabakat bu özeti yeniden hesaplayıp karşılaştırır: silinen kart, eski PIN+eski mühür ile
     "geri alma" ya da hesap içi değişiklikler böylece yakalanır.

Dürüst sınır: Eski bir (PIN, mühür) çiftinin geri yüklenmesi (rollback) terminalde anlık engellenmez (o an mühür geçerlidir);
mutabakat/panel özet farkıyla yakalar. Anlık engellemek için kart başına sürüm sayacını veritabanı DIŞINDA tutmak gerekir.

Özete/mühre girmeyenler: bloke ve hatalı deneme sayacı (bkz. bloke_butunlugu.py).
"""
import hashlib
import hmac
import json
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .hashlog import kanonik_json, kayit_ekle
from .models import Kart, LogKaydi

FORMAT = 2  # eski (satir listeli) goruntuler gecersiz sayilir; yukseltmede yeniden baslangic gerekir


def _mac(kart):
    mesaj = "|".join([kart.uid, kart.hesap.hesap_no, hashlib.sha256(kart.pin_hash.encode("utf-8")).hexdigest()])
    return hmac.new(settings.KART_MAC_ANAHTARI.encode("utf-8"), mesaj.encode("utf-8"), hashlib.sha256).hexdigest()


def muhurle(kart):
    """Kartın mührünü (yeniden) üretir ve kaydeder. Yalnızca onaylı işlemlerden/kurulumdan çağrılır."""
    kart.mac = _mac(kart)
    kart.save(update_fields=["mac"])


def tum_kartlari_muhurle():
    for kart in Kart.objects.select_related("hesap"):
        muhurle(kart)


def ozet():
    """Tüm kart mühürlerinin özeti (uid sırasıyla)."""
    liste = sorted([k.uid, k.mac] for k in Kart.objects.all())
    return hashlib.sha256(kanonik_json(liste).encode("utf-8")).hexdigest()


def goruntu_kaydet(neden, **ek):
    """Mevcut mühür özetini 'onaylı sürüm' olarak hash zincirine yazar (satır listesi yazılmaz)."""
    return kayit_ekle("kart_goruntusu", {"format": FORMAT, "ozet": ozet(), "adet": Kart.objects.count(),
                                         "neden": neden, **ek})


def baslangic(neden):
    """Tüm kartları mühürler ve onaylı başlangıç görüntüsünü yazar (ilk kurulum / bilinçli yeniden başlatma)."""
    tum_kartlari_muhurle()
    return goruntu_kaydet(neden)


def son_ozet():
    """Son onaylı özet; onaylı (yeni biçimli) sürüm yoksa None."""
    kayit = LogKaydi.objects.filter(olay_turu="kart_goruntusu").order_by("-sira").first()
    if kayit is None:
        return None
    try:
        veri = json.loads(kayit.veri)
    except ValueError:
        return None
    return veri.get("ozet") if veri.get("format") == FORMAT else None


def kart_dogrulandi_mi(kart):
    """True: mühür geçerli. False: geçersiz/yok. None: onaylı başlangıç yok (zorlama yapılmaz)."""
    if son_ozet() is None:
        return None
    return hmac.compare_digest(kart.mac or "", _mac(kart))


def dogrulanamayan_kartlar():
    """Mührü geçersiz kartların UID'leri (onaylı başlangıç varsa)."""
    if son_ozet() is None:
        return []
    return sorted(k.uid for k in Kart.objects.select_related("hesap") if not hmac.compare_digest(k.mac or "", _mac(k)))


def tutarli_mi():
    """Tüm küme son onaylı özetle aynı mı? None: onaylı başlangıç yok."""
    onayli = son_ozet()
    if onayli is None:
        return None
    return onayli == ozet()


def durum():
    return {None: "baslangic_yok", True: "tutarli", False: "tutarsiz"}[tutarli_mi()]


def ihlal_kaydet(kart):
    """Doğrulanamayan kart: kart başına saatte bir log."""
    sinir = timezone.now() - timedelta(hours=1)
    if LogKaydi.objects.filter(olay_turu="kart_butunluk_ihlali", zaman__gte=sinir,
                               veri__contains=f'"uid":"{kart.uid}"').exists():
        return
    kayit_ekle("kart_butunluk_ihlali", {"uid": kart.uid, "hesap_no": kart.hesap.hesap_no})
