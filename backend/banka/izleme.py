"""Terminal izleme: heartbeat, çevrimdışı tespiti ve otomatik arıza vakası.

Akış:
  1. Köprü, terminalin PING/HELLO çerçevelerini sunucuya heartbeat olarak iletir (`/api/heartbeat/`, köprü anahtarıyla yetkili).
     Terminalden gelen her API isteği de "görüldü" sayılır.
  2. Bir terminalden TERMINAL_CEVRIMDISI_SN saniyedir ses yoksa `tara()` onu çevrimdışı sayar, bir ARIZA VAKASI açar, sorumlu
     görevliye atar ve olayı hash zincirine yazar. Vaka terminal başına tek açık olabilir (veritabanı kısıtı), yani tarama
     tekrar tekrar çalışsa da çift vaka açılmaz.
  3. Heartbeat geri gelince vaka otomatik ÇÖZÜLÜR (kesinti süresi kaydedilir, zincire yazılır).

Dürüst sınırlar:
  * Heartbeat, "köprü terminali duyuyor" demektir; röle, okuyucu ya da LCD'nin sağlam olduğunu kanıtlamaz.
  * Hiç heartbeat göndermemiş terminal "bilinmiyor" sayılır, vaka açılmaz (demoda bağlı olmayan terminaller alarm çalmasın).
  * Sunucuda zamanlayıcı yoktur: `tara()` panel/laboratuvar sayfaları açıldığında ve `manage.py terminal_izle` ile çalışır.
    Kimse bakmıyorsa ve komut da çalışmıyorsa vaka, bir sonraki taramaya kadar açılmaz. Üretimde komut zamanlanmalıdır.
  * Vaka ataması şimdilik "en az açık vakası olan aktif Görevli"dir; ayrı bir teknik servis rolü yoktur.
"""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q

from .hashlog import kayit_ekle
from .models import ArizaVakasi, Personel, Rol, Terminal

DOKUNMA_ARALIGI_SN = 3     # sık API isteği her seferinde veritabanına yazmasın

CEVRIMICI, CEVRIMDISI, BILINMIYOR = "CEVRIMICI", "CEVRIMDISI", "BILINMIYOR"


def _simdi():
    from .services import simdi  # testlerde simdi() oynatilabilsin
    return simdi()


def baglanti_durumu(terminal, simdi=None):
    """{'durum', 'son_gorulme', 'sn_once', 'esik_sn'}. Hiç görülmemiş terminal BILINMIYOR'dur."""
    simdi = simdi or _simdi()
    esik = settings.TERMINAL_CEVRIMDISI_SN
    if terminal.son_gorulme is None:
        return {"durum": BILINMIYOR, "son_gorulme": None, "sn_once": None, "esik_sn": esik}
    gecen = max(0.0, (simdi - terminal.son_gorulme).total_seconds())      # tara() ile AYNI karşılaştırma (tam sayıya yuvarlamadan)
    return {"durum": CEVRIMDISI if gecen > esik else CEVRIMICI, "son_gorulme": terminal.son_gorulme,
            "sn_once": int(gecen), "esik_sn": esik}


def _sorumlu_sec():
    """En az açık vakası olan aktif, bloke olmayan Görevli (eşitlikte en eski kayıt)."""
    adaylar = (Personel.objects.filter(aktif=True, bloke=False, rol=Rol.GOREVLI)
               .annotate(acik=Count("ariza_vakalari", filter=Q(ariza_vakalari__durum=ArizaVakasi.Durum.ACIK)))
               .order_by("acik", "id"))
    return adaylar.first()


def nabiz(terminal_kodu, simdi=None, zorla=False):
    """Terminalin canlı olduğunu kaydeder; açık bağlantı vakası varsa çözer. Bilinmeyen/kapalı terminal için None döner.

    `zorla=False` iken son kayıt çok yeniyse veritabanına yazmaz (her API isteğinde çağrılabilsin diye)."""
    simdi = simdi or _simdi()
    t = Terminal.objects.filter(kod=terminal_kodu, aktif=True).first()
    if t is None:
        return None
    acik_var = ArizaVakasi.objects.filter(terminal=t, durum=ArizaVakasi.Durum.ACIK).exists()
    if (not zorla and not acik_var and t.son_gorulme is not None
            and (simdi - t.son_gorulme).total_seconds() < DOKUNMA_ARALIGI_SN):
        return baglanti_durumu(t, simdi)
    with transaction.atomic():
        t = Terminal.objects.select_for_update().get(pk=t.pk)
        onceki = t.son_gorulme
        t.son_gorulme = simdi
        t.save(update_fields=["son_gorulme"])
        for vaka in ArizaVakasi.objects.select_for_update().filter(terminal=t, durum=ArizaVakasi.Durum.ACIK):
            vaka.durum = ArizaVakasi.Durum.COZULDU
            vaka.kapanis = simdi
            vaka.sure_sn = max(0, int((simdi - (vaka.son_gorulme or onceki or vaka.acilis)).total_seconds()))
            vaka.save(update_fields=["durum", "kapanis", "sure_sn"])
            kayit_ekle("terminal_baglanti_geri", {"terminal": t.kod, "vaka_id": vaka.id, "sure_sn": vaka.sure_sn})
    return baglanti_durumu(t, simdi)


def tara(simdi=None):
    """Süresi dolan terminaller için vaka açar. Yeni açılan vakaların listesini döndürür (idempotent)."""
    simdi = simdi or _simdi()
    sinir = simdi - timedelta(seconds=settings.TERMINAL_CEVRIMDISI_SN)
    yeni = []
    adaylar = Terminal.objects.filter(aktif=True, son_gorulme__isnull=False, son_gorulme__lt=sinir)
    for pk in list(adaylar.values_list("pk", flat=True)):
        with transaction.atomic():
            t = Terminal.objects.select_for_update().get(pk=pk)
            if t.son_gorulme is None or t.son_gorulme >= sinir:
                continue                                      # arada heartbeat geldi
            if ArizaVakasi.objects.filter(terminal=t, durum=ArizaVakasi.Durum.ACIK).exists():
                continue
            sorumlu = _sorumlu_sec()
            sn = int((simdi - t.son_gorulme).total_seconds())
            vaka = ArizaVakasi.objects.create(
                terminal=t, tur=ArizaVakasi.Tur.BAGLANTI, son_gorulme=t.son_gorulme, atanan=sorumlu,
                aciklama=f"{sn} sn heartbeat yok (eşik {settings.TERMINAL_CEVRIMDISI_SN} sn)")
            kayit_ekle("terminal_cevrimdisi", {
                "terminal": t.kod, "vaka_id": vaka.id, "son_gorulme": t.son_gorulme.isoformat(),
                "atanan": sorumlu.ad if sorumlu else None})
            yeni.append(vaka)
    return yeni
