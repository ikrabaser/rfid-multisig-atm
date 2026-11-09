"""SMS sağlayıcı soyutlaması.

PIN gibi gizli içerikler sunucu log'una, veritabanına ya da panele YAZILMAZ; yalnızca sağlayıcıya verilir. Gerçek bir
SMS sağlayıcısı (Twilio, Netgsm vb.) için `SmsSaglayici`'dan türeyen bir sınıf yazıp `SMS_SAGLAYICI` ayarına koymak yeter.

Simülasyonda (varsayılan) `DosyaSmsSaglayici` mesajı, müşterinin/yetkilinin TELEFONUNU temsil eden ayrı bir dosyaya
yazar (`SMS_KUTUSU_DOSYASI`, varsayılan backend/sms_kutusu.jsonl, git'e girmez, mümkünse yalnızca sahibi okur).
Telefonu "açmak" için: `python manage.py sms_kutusu`.
"""
import json
import os
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from django.utils.module_loading import import_string


class SmsSaglayici:
    def gonder(self, telefon, metin):
        raise NotImplementedError


class DosyaSmsSaglayici(SmsSaglayici):
    """Simülasyon: mesaj, 'telefon' sayılan bir dosyaya eklenir."""

    def gonder(self, telefon, metin):
        yol = Path(settings.SMS_KUTUSU_DOSYASI)
        satir = json.dumps({"zaman": timezone.now().isoformat(timespec="seconds"), "telefon": telefon, "metin": metin},
                           ensure_ascii=False)
        with open(yol, "a", encoding="utf-8") as f:
            f.write(satir + "\n")
        try:
            os.chmod(yol, 0o600)
        except OSError:  # bazi dosya sistemlerinde (Windows) desteklenmez
            pass


class BellekSmsSaglayici(SmsSaglayici):
    """Testler için: mesajlar bellekte tutulur."""

    gelen = []

    def gonder(self, telefon, metin):
        BellekSmsSaglayici.gelen.append((telefon, metin))


def gonder(telefon, metin):
    import_string(settings.SMS_SAGLAYICI)().gonder(telefon, metin)


def kutuyu_oku(telefon=None, temizle=False):
    """Simülasyon gelen kutusunu okur (DosyaSmsSaglayici). `temizle`: okunan mesajlar silinir."""
    yol = Path(settings.SMS_KUTUSU_DOSYASI)
    if not yol.exists():
        return []
    mesajlar, kalan = [], []
    for satir in yol.read_text(encoding="utf-8").splitlines():
        try:
            m = json.loads(satir)
        except ValueError:
            continue
        if telefon is None or m.get("telefon") == telefon:
            mesajlar.append(m)
        else:
            kalan.append(satir)
    if temizle:
        yol.write_text("".join(s + "\n" for s in kalan), encoding="utf-8")
    return mesajlar
