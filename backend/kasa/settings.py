import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(ad, varsayilan):
    return os.environ.get(ad, "1" if varsayilan else "0") == "1"


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

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

LANGUAGE_CODE = "tr"
TIME_ZONE = "Europe/Istanbul"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/admin/login/"

# Testlerde PIN hash'leme hizli olsun
if "test" in sys.argv:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# ----------------------------------------------------------------- Proje ayarlari
# Koprunun API'ye gonderdigi anahtar (X-Kopru-Anahtari basligi). Gercekte degistir!
KOPRU_API_ANAHTARI = os.environ.get("KOPRU_API_ANAHTARI", "gelistirme-kopru-anahtari")

# Hash zinciri HMAC anahtari. Veritabaninin disinda tutulursa, veritabanina yazma
# yetkisi olan biri bile zinciri yeniden hesaplayamaz.
LOG_HMAC_ANAHTARI = os.environ.get("LOG_HMAC_ANAHTARI", "gelistirme-log-anahtari")

MAKS_PIN_DENEME = 3        # asilinca kart bloke olur
ONAY_PENCERESI_SN = 30     # onaylarin toplanmasi icin sure
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

# E-posta: varsayilan konsola yazar. Gercek SMTP icin DJANGO_EMAIL_BACKEND ve EMAIL_* ayarlarini ver.
EMAIL_BACKEND = os.environ.get("DJANGO_EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "banka@banka.example")
