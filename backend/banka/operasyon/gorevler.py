"""Procrastinate görev kuyruğu (PostgreSQL'de çalışır): simülasyon koşuları panelden kuyruğa atılır, worker çalıştırır.

Ayrı bir işçi süreci gerekir:   python manage.py operasyon_worker        (ilk kurulumda kuyruk şemasını da kurar)
Kuyruk tabloları ana veritabanındadır (procrastinate_*); sandbox'ta değil. `PROCRASTINATE_BAGLANTI=memory` iken (testler,
SQLite denemesi) kuyruk bellektedir ve görev kuyruğa atılır ama işlenmez; testler görevi doğrudan çağırır.
"""
import os

import django
from django.apps import apps
from django.conf import settings

if not apps.ready:                      # worker CLI'si Django'yu kurmadan bu modülü içe aktarabilir
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "kasa.settings")
    django.setup()

import procrastinate  # noqa: E402
from procrastinate import testing  # noqa: E402

KUYRUK = "simulasyon"
KILIT = "simulasyon"


def baglayici_uret():
    if settings.PROCRASTINATE_BAGLANTI == "memory":
        return testing.InMemoryConnector()
    db = settings.DATABASES["default"]
    return procrastinate.PsycopgConnector(kwargs={
        "dbname": db["NAME"], "user": db["USER"], "password": db["PASSWORD"] or None,
        "host": db["HOST"], "port": db["PORT"]})


app = procrastinate.App(connector=baglayici_uret())


@app.task(name="banka.operasyon.simulasyon_calistir", queue=KUYRUK)
def simulasyon_calistir(kosu_id):
    from .yurutucu import calistir
    calistir(int(kosu_id))


def _yapilandir():
    # lock: aynı anda tek koşu (sandbox tek). queueing_lock: kuyrukta ikinci bir bekleyen iş açılmaz.
    return simulasyon_calistir.configure(lock=KILIT, queueing_lock=KILIT)


def kuyruga_at(kosu):
    """Koşuyu kuyruğa atar ve iş numarasını kayda yazar. Kuyruğa ulaşılamazsa koşu HATA olur."""
    from ..models import SimulasyonKosusu
    try:
        if settings.PROCRASTINATE_BAGLANTI == "memory":
            is_no = _yapilandir().defer(kosu_id=kosu.id)
        else:
            with app.open():
                is_no = _yapilandir().defer(kosu_id=kosu.id)
    except Exception as e:                                   # AlreadyEnqueued, bağlantı hatası vb.
        kosu.durum, kosu.hata = SimulasyonKosusu.Durum.HATA, f"Kuyruğa atılamadı: {type(e).__name__}: {e}"[:500]
        kosu.save(update_fields=["durum", "hata"])
        return None
    kosu.gorev_id = is_no
    kosu.save(update_fields=["gorev_id"])
    return is_no


def sema_kur():
    """Procrastinate şemasını kurar (zaten kuruluysa dokunmaz). True: yeni kuruldu."""
    import psycopg
    db = settings.DATABASES["default"]
    with psycopg.connect(dbname=db["NAME"], user=db["USER"], password=db["PASSWORD"] or None,
                         host=db["HOST"], port=db["PORT"]) as baglanti:
        var = baglanti.execute("SELECT to_regclass('procrastinate_jobs')").fetchone()[0]
    if var:
        return False
    with app.open():
        app.schema_manager.apply_schema()
    return True
