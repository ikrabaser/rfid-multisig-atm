import os
import sys
from decimal import Decimal
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(ad, varsayilan):
    return os.environ.get(ad, "1" if varsayilan else "0") == "1"


def _env_int(ad, varsayilan, en_az, en_fazla):
    """Ortam degiskeninden tam sayi okur; gecersiz ya da aralik disiysa varsayilana doner."""
    try:
        deger = int(os.environ.get(ad, varsayilan))
    except ValueError:
        return varsayilan
    return deger if en_az <= deger <= en_fazla else varsayilan


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "gelistirme-icin-gizli-anahtar-degistir")
DEBUG = _env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "banka",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "kasa.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "kasa.wsgi.application"

def _veritabani():
    """Varsayilan PostgreSQL. Hizli yerel deneme icin DB_ENGINE=sqlite (esz amanlilik testleri atlanir).

    Baglanti: POSTGRES_DB / POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_HOST / POSTGRES_PORT.
    Varsayilanlar docker-compose.yml ile uyumludur; GERCEK ortamda parolayi degistir.
    """
    if os.environ.get("DB_ENGINE", "postgresql").lower() == "sqlite":
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": os.environ.get("SQLITE_DOSYASI") or BASE_DIR / "db.sqlite3"}
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "kasa"),
        "USER": os.environ.get("POSTGRES_USER", "kasa"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "kasa"),
        "HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        # Kilit beklemesi sonsuza gitmesin; yarim kalan islem kilit tutmasin
        "OPTIONS": {"options": "-c lock_timeout=10000 -c idle_in_transaction_session_timeout=60000"},
    }


DATABASES = {"default": _veritabani()}

LANGUAGE_CODE = "tr"
TIME_ZONE = "Europe/Istanbul"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/giris/"          # kendi giris sayfamiz (banka/webgiris.py); /admin/login/ da buraya yonlendirilir
LOGIN_REDIRECT_URL = "/"

# Web girisi: hatali deneme siniri (hash zincirinden sayilir), bosta kalma suresi, parola kurallari
GIRIS_MAKS_HATA = _env_int("GIRIS_MAKS_HATA", 5, 3, 50)           # kullanici adi basina, pencere icinde
GIRIS_MAKS_HATA_IP = _env_int("GIRIS_MAKS_HATA_IP", 20, 5, 500)   # IP basina (kullanici adi taramasini keser)
GIRIS_KILIT_SN = _env_int("GIRIS_KILIT_SN", 600, 60, 86400)       # pencere = kilit suresi
SESSION_COOKIE_AGE = _env_int("OTURUM_ZAMAN_ASIMI_SN", 1800, 120, 86400)   # bostayken oturum sonu (sn)
SESSION_SAVE_EVERY_REQUEST = True                                  # her istekte yenilenir: bosta kalma suresi
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Testlerde (ve operasyon simulasyonunun sandbox'inda) PIN hash'leme hizli olsun
if "test" in sys.argv or _env_bool("HIZLI_PIN_HASH", False):
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# ----------------------------------------------------------------- Proje ayarlari
# Koprunun API'ye gonderdigi anahtar (X-Kopru-Anahtari basligi). Gercekte degistir!
KOPRU_API_ANAHTARI = os.environ.get("KOPRU_API_ANAHTARI", "gelistirme-kopru-anahtari")

# Hash zinciri HMAC anahtari. Veritabaninin disinda tutulursa, veritabanina yazma
# yetkisi olan biri bile zinciri yeniden hesaplayamaz.
LOG_HMAC_ANAHTARI = os.environ.get("LOG_HMAC_ANAHTARI", "gelistirme-log-anahtari")

MAKS_PIN_DENEME = 3        # asilinca kart bloke olur
# Onaylarin toplanma suresi (sn). Varsayilan 30; demo icin: $env:ONAY_PENCERESI_SN=120 (10-600 arasi gecerli)
ONAY_PENCERESI_SN = _env_int("ONAY_PENCERESI_SN", 30, 10, 600)
OTURUM_SURESI_SN = 120     # musteri oturum token'i omru

# Mesai disinda (hafta sonu dahil) onay isteyen islemlerde +1 onay ve bir ust rol
MESAI_DISI_KURALI = _env_bool("MESAI_DISI_KURALI", True)
MESAI_BASLANGIC = 9
MESAI_BITIS = 18

# Kasa dairesi acma: kac yetkili ve en az hangi rol (3 = Mudur). Talebi baslatan sayilmaz.
KASA_ACMA_GEREKEN_ONAY = 3
KASA_ACMA_EN_AZ_ROL = 3

# HTTPS: DJANGO_HTTPS=1 ise API yalnizca HTTPS kabul eder (PIN trafigi duz HTTP ile gitmesin)
# ve oturum/CSRF cerezleri yalnizca HTTPS ile gonderilir. Gelistirme icin: https_sunucu.py
API_SADECE_HTTPS = _env_bool("DJANGO_HTTPS", False)
if API_SADECE_HTTPS:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# Terminal: baslik yoksa varsayilan terminal; imkansiz seyahat kurali en fazla bu hizi (km/sa) kabul eder
VARSAYILAN_TERMINAL = "ATM-01"
MAKS_SEYAHAT_HIZI_KMH = 150

# Satir ici risk motoru (risk.py). Testlerde varsayilan kapali: eski testler saat/hiz gibi sinyallerden etkilenmesin.
RISK_MOTORU_AKTIF = _env_bool("RISK_MOTORU_AKTIF", "test" not in sys.argv)
RISK_EK_ONAY_ESIGI = _env_int("RISK_EK_ONAY_ESIGI", 40, 1, 1000)
RISK_RED_ESIGI = _env_int("RISK_RED_ESIGI", 80, 1, 1000)
RISK_BLOKE_ESIGI = _env_int("RISK_BLOKE_ESIGI", 120, 1, 1000)
RISK_EK_ONAY_SAYISI = _env_int("RISK_EK_ONAY_SAYISI", 1, 1, 5)
RISK_YENI_HESAP_TUTAR = Decimal(os.environ.get("RISK_YENI_HESAP_TUTAR", "5000"))

# E-posta: varsayilan konsola yazar. Gercek SMTP icin DJANGO_EMAIL_BACKEND ve EMAIL_* ayarlarini ver.
EMAIL_BACKEND = os.environ.get("DJANGO_EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "banka@banka.example")

# Maker-checker (politika degisikligi) ve korkuluklar. Bu sinirlar panelden DEGISMEZ; kod/ayar degisikligi ister.
POLITIKA_DEGISIKLIK_ONAY = 2             # talep sahibi HARIC kac farkli onaylayici
POLITIKA_DEGISIKLIK_SURE_SAAT = 24       # talebin gecerlilik suresi
POLITIKA_ONAYSIZ_AZAMI_TUTAR = Decimal("10000")   # onaysiz (0 onay) aralik en fazla bu tutara kadar olabilir
POLITIKA_EN_UST_ARALIK_MIN_ONAY = 2      # en yuksek tutar araligi en az bu kadar onay istemeli
POLITIKA_MAKS_ONAY = 5

# Ters kayit: nakit cekmede para teslimi bu sure icinde bildirilmelidir. Gecerse teslim "belirsiz" olur
# ve operator sayimiyla cozulur (otomatik iade yok). Demo icin: $env:TESLIM_PENCERESI_SN=15
TESLIM_PENCERESI_SN = _env_int("TESLIM_PENCERESI_SN", 120, 10, 3600)

# ATM'in dagittigi banknotlar (TL), buyukten kucuge. En kucuk birimin (ebob) katlari cekilebilir.
BANKNOT_NOMINALLERI = (200, 100, 50, 20)
# Bir banknot cinsinden kullanilabilir adet bu sayinin altina inerse anomali uyarisi verilir
KASET_UYARI_ESIGI = 100

# Hassas yonetim islemleri (maker-checker): gereken BASKA onaylayici sayisi. Koruyucu yonler az, genisletici yonler cok onay ister.
YONETIM_ONAY_SAYILARI = {
    "PERSONEL_EKLE": 2, "PERSONEL_EKLE_MUDUR": 3,
    "PERSONEL_ROL_YUKSELT": 2, "PERSONEL_ROL_YUKSELT_MUDUR": 3, "PERSONEL_ROL_DUSUR": 1,
    "PERSONEL_AKTIF_ET": 2, "PERSONEL_PASIF_ET": 1,
    "PERSONEL_PIN_SIFIRLA": 2,
    "BLOKE_KALDIR_PERSONEL": 2, "BLOKE_KALDIR_KART": 1,
    "GUNLUK_LIMIT_ARTIR": 2, "GUNLUK_LIMIT_AZALT": 1,
    "TERMINAL_AKTIF_ET": 2, "TERMINAL_PASIF_ET": 1,
    "KART_TANIMLA": 2, "KART_PIN_SIFIRLA": 2, "MUSTERI_TELEFON": 2,
    # Belirsiz nakit teslimi: "verildi" para hareketi yaratmaz (1 onay); iade (VERILMEDI/KISMI) müşteriye para geri yazar (2 onay)
    "NAKIT_COZ_VERILDI": 1, "NAKIT_COZ_IADE": 2,
}
MAKS_KART_SAYISI = 3                               # bir hesaba en fazla bu kadar kart tanimlanabilir
YONETIM_TALEP_SURE_SAAT = 24
YONETIM_MIN_AKTIF_YETKILI = 4                      # 3 onay + talebi baslatan (kasa acma) icin
HESAP_GUNLUK_LIMIT_AZAMI = Decimal("5000000")      # hicbir hesabin gunluk limiti bunu asamaz

# SMS: PIN gibi gizli icerikler yalnizca saglayiciya verilir. Gercek saglayici icin sms.SmsSaglayici'dan turet.
SMS_SAGLAYICI = os.environ.get("SMS_SAGLAYICI", "banka.sms.DosyaSmsSaglayici")
SMS_KUTUSU_DOSYASI = BASE_DIR / "sms_kutusu.jsonl"   # simulasyon: telefonu temsil eden dosya (git'e girmez)

# Kart satir muhru (HMAC) anahtari: veritabaninin DISINDA tutulmali; bilmeyen biri gecerli bir kart satiri uretemez.
KART_MAC_ANAHTARI = os.environ.get("KART_MAC_ANAHTARI", "gelistirme-kart-mac-anahtari")

# Imzali kart (NTAG 424 DNA simulasyonu, bkz. banka/kart_imzasi.py). Kapaliyken imzasiz kart kabul edilir
# (RC522'li gercek donanim imza uretemez); imza gonderilmis ve gecersizse her zaman reddedilir.
KART_IMZA_ZORUNLU = _env_bool("KART_IMZA_ZORUNLU", False)
KART_IMZA_ANAHTARI = os.environ.get("KART_IMZA_ANAHTARI", "gelistirme-kart-imza-anahtari")   # veritabaninda degil
KART_IMZA_GECERLILIK_SN = _env_int("KART_IMZA_GECERLILIK_SN", 90, 10, 600)

# Web ATM simulatoru (/simulator/): yalnizca gelistirme icin. Kapaliyken tum uclari 404 verir.
SIMULATOR_AKTIF = _env_bool("SIMULATOR_AKTIF", DEBUG)
# Belirsiz nakit teslimini TEK kişinin sonuçlandırmasına (nakit_coz --dogrudan) izin verir. Üretimde kapalı kalmalı:
# yol, NAKIT_COZ yönetim talebidir (talep eden + başka onaylayan). Simülatör zaten ayrı bir geliştirme aracıdır.
NAKIT_COZ_TEK_KISI = _env_bool("NAKIT_COZ_TEK_KISI", DEBUG)


# ----------------------------------------------------------------- Banka operasyon simulasyonu (operasyon/)
# Sanal musteri yuku ayri bir "sandbox" veritabaninda calisir; gercek kasa, zincir ve bildirimler etkilenmez.
OPERASYON_SIM_AKTIF = _env_bool("OPERASYON_SIM_AKTIF", DEBUG)
OPERASYON_SANDBOX_DB = os.environ.get("OPERASYON_SANDBOX_DB", "kasa_sim")                # PostgreSQL veritabani adi
OPERASYON_SANDBOX_SQLITE = os.environ.get("OPERASYON_SANDBOX_SQLITE", str(BASE_DIR / "db_sim.sqlite3"))
# Gorev kuyrugu (Procrastinate, PostgreSQL'de calisir). "memory": kuyruk bellekte (testler, SQLite denemesi)
PROCRASTINATE_BAGLANTI = os.environ.get(
    "PROCRASTINATE_BAGLANTI", "memory" if (DATABASES["default"]["ENGINE"].endswith("sqlite3") or "test" in sys.argv) else "postgres")
# Canli akis (Centrifugo). Yayin hatasi simulasyonu bozmaz; panel her durumda 2 sn'de bir sorgulayarak da guncellenir.
CENTRIFUGO_AKTIF = _env_bool("CENTRIFUGO_AKTIF", DEBUG and "test" not in sys.argv)
CENTRIFUGO_API_URL = os.environ.get("CENTRIFUGO_API_URL", "http://127.0.0.1:8001/api")
CENTRIFUGO_WS_URL = os.environ.get("CENTRIFUGO_WS_URL", "ws://127.0.0.1:8001/connection/websocket")
CENTRIFUGO_API_ANAHTARI = os.environ.get("CENTRIFUGO_API_ANAHTARI", "gelistirme-centrifugo-api-anahtari")
CENTRIFUGO_HMAC_ANAHTARI = os.environ.get("CENTRIFUGO_HMAC_ANAHTARI", "gelistirme-centrifugo-hmac-anahtari-degistir")
