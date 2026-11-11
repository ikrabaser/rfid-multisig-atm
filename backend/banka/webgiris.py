"""Web arayüzü (Panel, Yönetim, simülatör) için giriş güvenliği.

Neden kendi giriş sayfamız: Django'nun hazır admin girişi parola tahminine karşı sınır koymaz. Burada:
  * Hatalı denemeler KULLANICI ADI başına ve IP başına sayılır; sınırı aşan istek, doğru parola olsa bile reddedilir.
  * Hatalı/başarılı girişler, çıkışlar ve parola değişiklikleri HASH ZİNCİRİNE yazılır (parola asla yazılmaz).
    Sayaç da zincirden okunur: süreçler arasında ortaktır ve yeniden başlatmayla sıfırlanmaz.
  * Kilitliyken yapılan istekler zincire yazılmaz (saldırgan logu şişiremesin).
  * Aynı mesaj hem olmayan kullanıcı hem yanlış parola için döner (kullanıcı adı sızdırmaz).
/admin/login/ da bu sayfaya yönlendirilir; kilidi yan kapıdan aşmak mümkün değildir.

Dürüst sınır: IP olarak yalnızca REMOTE_ADDR kullanılır (X-Forwarded-For güvenilmez); bir ters vekilin arkasında bu
adres vekilinki olur ve IP sınırı herkesi birden etkiler. Vekil kullanılacaksa bu fonksiyon ona göre uyarlanmalıdır.
"""
import re
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.utils import timezone

from .hashlog import kayit_ekle
from .models import LogKaydi

AD_DESENI = re.compile(r"[\w.@+-]{1,150}")
GENEL_HATA = "Kullanıcı adı veya parola hatalı."
KILIT_HATASI = "Çok fazla hatalı deneme. Bir süre sonra tekrar deneyin."


def _ad(kullanici):
    kullanici = (kullanici or "").strip()
    return kullanici if AD_DESENI.fullmatch(kullanici) else "?"   # log aramasını bozacak karakterler "?" olur


def _ip(request):
    return request.META.get("REMOTE_ADDR", "?")[:45]


def _hatalar(anahtar, deger, ad=None):
    """Pencere içindeki hatalı deneme sayısı; kullanıcının son başarılı girişinden öncekiler sayılmaz."""
    sinir = timezone.now() - timedelta(seconds=settings.GIRIS_KILIT_SN)
    kalip = f'"{anahtar}":"{deger}"'
    hatalar = LogKaydi.objects.filter(olay_turu="web_giris_hatali", zaman__gte=sinir, veri__contains=kalip)
    if ad is not None:
        basarili = LogKaydi.objects.filter(olay_turu="web_giris", zaman__gte=sinir,
                                           veri__contains=f'"kullanici":"{ad}"').order_by("-sira").first()
        if basarili is not None:
            hatalar = hatalar.filter(sira__gt=basarili.sira)
    return hatalar.count()


def kilitli_mi(kullanici, ip):
    ad = _ad(kullanici)
    return (_hatalar("kullanici", ad, ad) >= settings.GIRIS_MAKS_HATA
            or _hatalar("ip", ip) >= settings.GIRIS_MAKS_HATA_IP)


def giris_yap(request, kullanici, parola):
    """(kullanici nesnesi, hata mesaji) döner. Hata varsa nesne None."""
    ip = _ip(request)
    if kilitli_mi(kullanici, ip):
        return None, KILIT_HATASI
    user = authenticate(request, username=(kullanici or "").strip(), password=parola or "")
    ad = _ad(kullanici)
    if user is None:
        kayit_ekle("web_giris_hatali", {"kullanici": ad, "ip": ip})
        return None, GENEL_HATA
    login(request, user)
    kayit_ekle("web_giris", {"kullanici": user.get_username(), "ip": ip})
    return user, None


def cikis_yap(request):
    ad = request.user.get_username() if request.user.is_authenticated else None
    logout(request)
    if ad:
        kayit_ekle("web_cikis", {"kullanici": ad, "ip": _ip(request)})


def sifre_degisti_kaydet(request, user):
    kayit_ekle("web_sifre_degisti", {"kullanici": user.get_username(), "ip": _ip(request)})
