"""Sandbox veritabanı: simülasyon yükü gerçek veriden AYRI bir veritabanında çalışır.

PostgreSQL: her koşuda `OPERASYON_SANDBOX_DB` (varsayılan kasa_sim) silinip yeniden yaratılır (yönetici bağlantısı ana
veritabanından; kullanıcıda CREATEDB yetkisi gerekir). SQLite: ayrı bir dosya. Alt süreç bu veritabanına ortam
değişkenleriyle yönlendirilir; yani servis katmanı hiçbir kod değişikliği olmadan sandbox'a yazar.
"""
import os
import sys
from pathlib import Path

from django.conf import settings


class SandboxHatasi(RuntimeError):
    pass


def motor():
    return "sqlite" if settings.DATABASES["default"]["ENGINE"].endswith("sqlite3") else "postgresql"


def ad():
    return settings.OPERASYON_SANDBOX_SQLITE if motor() == "sqlite" else settings.OPERASYON_SANDBOX_DB


def _guvenli_ad(db_adi):
    if not db_adi.replace("_", "").isalnum() or len(db_adi) > 50:
        raise SandboxHatasi("Sandbox veritabanı adı yalnızca harf, rakam ve _ içerebilir")
    return db_adi


def hazirla():
    """Boş bir sandbox hazırlar (varsa önce siler). Ana veritabanıyla aynı adı kullanmayı reddeder."""
    if motor() == "sqlite":
        yol = Path(ad())
        if yol.resolve() == Path(str(settings.DATABASES["default"]["NAME"])).resolve():
            raise SandboxHatasi("Sandbox dosyası ana veritabanıyla aynı olamaz")
        for ek in ("", "-journal", "-wal", "-shm"):
            Path(str(yol) + ek).unlink(missing_ok=True)
        return str(yol)
    import psycopg
    from psycopg import sql
    db_adi = _guvenli_ad(ad())
    ana = settings.DATABASES["default"]
    if db_adi == ana["NAME"]:
        raise SandboxHatasi("Sandbox veritabanı ana veritabanıyla aynı olamaz")
    try:
        with psycopg.connect(dbname=ana["NAME"], user=ana["USER"], password=ana["PASSWORD"] or None,
                             host=ana["HOST"], port=ana["PORT"], autocommit=True) as baglanti:
            baglanti.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db_adi)))
            baglanti.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_adi)))
    except psycopg.Error as e:
        raise SandboxHatasi(f"Sandbox veritabanı oluşturulamadı (CREATEDB yetkisi var mı?): {e}")
    return db_adi


def sandbox_ortaminda_mi():
    """Bu süreç, yürütücünün hazırladığı sandbox ortamında mı çalışıyor? (alt süreç işareti)"""
    return os.environ.get("OPERASYON_SANDBOX_SUREC") == "1"


def cocuk_ortami(p):
    """Alt süreç için ortam: sandbox'a yönlendirme, bildirimleri sessize alma, hızlı PIN hash'i."""
    env = dict(os.environ)
    if motor() == "sqlite":
        env["SQLITE_DOSYASI"] = ad()
    else:
        env["POSTGRES_DB"] = ad()
    env.update({
        "SMS_SAGLAYICI": "banka.sms.BellekSmsSaglayici",                         # gerçek/dosya SMS yok
        "DJANGO_EMAIL_BACKEND": "django.core.mail.backends.dummy.EmailBackend",  # e-posta yok
        "MESAI_DISI_KURALI": "0",                                                # mesai kuralı yükü saate bağlamasın
        "SIMULATOR_AKTIF": "0",
        "CENTRIFUGO_AKTIF": "0",                                                 # yayını yalnızca yürütücü yapar
        "HIZLI_PIN_HASH": "0" if p.gercek_hash else "1",
        "OPERASYON_SANDBOX_SUREC": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    return env


def manage_py():
    return str(Path(settings.BASE_DIR) / "manage.py")


def cocuk_komutu(p):
    import json
    return [sys.executable, manage_py(), "operasyon_calistir", "--parametreler", json.dumps(p.sozluk())]
