"""Yürütücü: koşuyu sandbox ALT SÜRECİNDE çalıştırır, ilerlemeyi okur, Centrifugo'ya yayar ve kaydı günceller.

Alt süreç ana veritabanını bilmez; yalnızca stdout'a JSON satırları yazar. Böylece sandbox, gerçek verinin ve silinemeyen
hash zincirinin dışında kalır ve bir koşu çökerse ana süreç etkilenmez.
"""
import json
import subprocess
import threading
from collections import deque

from django.conf import settings
from django.utils import timezone

from ..models import SimulasyonKosusu
from . import sandbox
from .centrifugo import Yayinci, kanal
from .parametreler import Parametreler

AKTIF_DURUMLAR = (SimulasyonKosusu.Durum.BEKLIYOR, SimulasyonKosusu.Durum.CALISIYOR)


def yeni_kosu(olusturan, ham_parametreler):
    """Doğrulanmış parametrelerle kayıt açar. Zaten aktif bir koşu varsa ValueError (sandbox tek, koşular sıralı)."""
    p = Parametreler.al(ham_parametreler)
    if SimulasyonKosusu.objects.filter(durum__in=AKTIF_DURUMLAR).exists():
        raise ValueError("Zaten çalışan ya da kuyrukta bekleyen bir simülasyon var")
    return SimulasyonKosusu.objects.create(olusturan=olusturan, parametreler=p.sozluk())


def _kaydet(kosu, **alanlar):
    for ad, deger in alanlar.items():
        setattr(kosu, ad, deger)
    kosu.save(update_fields=list(alanlar))


def calistir(kosu_id, yayinci=None, komut=None, ortam=None):
    """Koşuyu baştan sona yürütür. `komut`/`ortam` yalnızca testlerde alt süreci değiştirmek içindir."""
    kosu = SimulasyonKosusu.objects.get(id=kosu_id)
    if kosu.durum != SimulasyonKosusu.Durum.BEKLIYOR:
        return kosu                                          # görev iki kez çalışmasın (yeniden deneme, çift tetik)
    yayinci = yayinci or Yayinci()
    p = Parametreler.al(kosu.parametreler)
    k = kanal(kosu.id)
    _kaydet(kosu, durum=SimulasyonKosusu.Durum.CALISIYOR, baslangic=timezone.now(), asama="sandbox")
    yayinci.yayinla(k, {"tur": "asama", "asama": "sandbox", "durum": "CALISIYOR"})
    try:
        sandbox.hazirla()
    except sandbox.SandboxHatasi as e:
        return _bitir(kosu, yayinci, SimulasyonKosusu.Durum.HATA, hata=str(e))

    proc = subprocess.Popen(komut or sandbox.cocuk_komutu(p), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", env=ortam or sandbox.cocuk_ortami(p),
                            cwd=str(settings.BASE_DIR))
    hata_ucu = deque(maxlen=40)
    threading.Thread(target=lambda: [hata_ucu.append(s.rstrip()) for s in proc.stderr], daemon=True).start()

    ozet, iptal = None, False
    for satir in proc.stdout:
        try:
            olay = json.loads(satir)
        except ValueError:
            continue
        tur = olay.get("tur")
        if tur == "bitti":
            ozet = olay.get("ozet")
            continue
        yayinci.yayinla(k, olay)
        if tur == "asama":
            _kaydet(kosu, asama=olay.get("asama", ""))
        elif tur == "tick":
            kosu.refresh_from_db(fields=["iptal_istendi"])
            _kaydet(kosu, son_durum=olay)
            if kosu.iptal_istendi and not iptal:
                iptal = True
                proc.terminate()
    kod = proc.wait()
    if iptal:
        return _bitir(kosu, yayinci, SimulasyonKosusu.Durum.IPTAL)
    if ozet is not None and kod == 0:
        return _bitir(kosu, yayinci, SimulasyonKosusu.Durum.TAMAMLANDI, ozet=ozet)
    return _bitir(kosu, yayinci, SimulasyonKosusu.Durum.HATA,
                  hata=("\n".join(hata_ucu) or f"Alt süreç {kod} koduyla bitti")[-4000:])


def _bitir(kosu, yayinci, durum, ozet=None, hata=""):
    alanlar = {"durum": durum, "bitis": timezone.now(), "hata": hata, "asama": ""}
    if ozet is not None:
        alanlar["ozet"] = ozet
    _kaydet(kosu, **alanlar)
    yayinci.yayinla(kanal(kosu.id), {"tur": "bitti", "durum": durum, "hata": hata,
                                     "toplam": (ozet or {}).get("toplam")})
    return kosu
