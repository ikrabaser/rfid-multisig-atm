"""PostgreSQL eşzamanlılık yardımcıları.

Neden gerekli: Hash zinciri tek bir sıralı zincirdir ("sıra = son sıra + 1"). PostgreSQL'de iki işlem
aynı anda "son kayıt"ı okursa aynı sırayı üretir. Ayrıca bakiye/günlük limit/banknot stoğu gibi
"oku-kontrol et-yaz" adımları, iki istek arasında araya girilirse aşılabilir.

Çözüm (kilit sırası HER YERDE aynıdır, böylece kilitler birbirini beklemez):
    1) zincir_kilidi()       : işlem sonuna kadar tutulan tek bir danışma kilidi (log zinciri + sıra)
    2) hesap satırları       : select_for_update, id sırasıyla
    3) kaset satırları       : select_for_update, id sırasıyla
Kontroller (bakiye, limit, stok) bu kilitler ALTINDA yapılır; yani iki işlemin kontrolü iç içe geçemez.

Yine de bir deadlock ya da serileştirme hatası çıkarsa (PostgreSQL 40P01 / 40001), `yeniden_dene`
iş mantığını baştan çalıştırır. SQLite'ta bu kilitler gerekmez (tek yazıcı) ve hiçbir şey yapmaz.
"""
import functools
import time

from django.db import OperationalError, connection

ZINCIR_KILIT_ANAHTARI = 7340011          # keyfi, projeye özel sabit
GECICI_HATA_KODLARI = {"40P01", "40001"}  # deadlock_detected, serialization_failure


def postgres_mu():
    return connection.vendor == "postgresql"


def zincir_kilidi():
    """transaction.atomic() içinde çağrılır; işlem bitene (commit/rollback) kadar tutulur."""
    if not postgres_mu():
        return
    with connection.cursor() as imlec:
        imlec.execute("SELECT pg_advisory_xact_lock(%s::bigint)", [ZINCIR_KILIT_ANAHTARI])


def gecici_hata_mi(hata):
    """psycopg3 (sqlstate) ve psycopg2 (pgcode) için: deadlock/serileştirme hatası mı?"""
    neden = hata.__cause__
    kod = getattr(neden, "sqlstate", None) or getattr(neden, "pgcode", None)
    return kod in GECICI_HATA_KODLARI


def yeniden_dene(fn=None, *, deneme=3, bekleme=0.05):
    """Geçici PostgreSQL hatasında fonksiyonu baştan dener. Dış bir transaction içindeyse denemez
    (bozulmuş transaction'ı kurtarmak mümkün değil), hatayı olduğu gibi yükseltir."""
    def sar(f):
        @functools.wraps(f)
        def sarmal(*args, **kwargs):
            for sira in range(1, deneme + 1):
                try:
                    return f(*args, **kwargs)
                except OperationalError as hata:
                    if connection.in_atomic_block or sira == deneme or not gecici_hata_mi(hata):
                        raise
                    time.sleep(bekleme * sira)
        return sarmal

    return sar(fn) if fn is not None else sar
