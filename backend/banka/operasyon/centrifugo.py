"""Centrifugo istemcisi (bağımlılıksız): bağlantı JWT'si ve HTTP API ile yayın.

Güvenlik: tarayıcı API anahtarını GÖRMEZ. Tarayıcıya yalnızca kısa ömürlü bir bağlantı JWT'si verilir; jeton, kullanıcıyı yalnızca
ilgili koşunun kanalına sunucu taraflı abone eder (`subs` iddiası), yani başka kanalları dinleyemez ve yayın yapamaz.
Yayın başarısızlığı koşuyu asla bozmaz: panel her durumda kayıttan sorgulayarak da güncellenir.
"""
import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.request

from django.conf import settings

log = logging.getLogger(__name__)


def kanal(kosu_id):
    # DİKKAT: Centrifugo'da ":" ad alanı ayracıdır; ad alanı tanımlamamak için kullanılmaz.
    return f"simulasyon-{int(kosu_id)}"


def _b64(veri):
    return base64.urlsafe_b64encode(veri).rstrip(b"=").decode("ascii")


def jwt_uret(iddialar, anahtar):
    baslik = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    govde = _b64(json.dumps(iddialar, separators=(",", ":")).encode())
    imza = hmac.new(anahtar.encode("utf-8"), f"{baslik}.{govde}".encode("ascii"), hashlib.sha256).digest()
    return f"{baslik}.{govde}.{_b64(imza)}"


def baglanti_jetonu(kullanici, kosu_id, sure_sn=300, an=None):
    """Bu kullanıcıyı yalnızca `kosu_id` koşusunun kanalına abone eden bağlantı JWT'si."""
    an = int(an if an is not None else time.time())
    return jwt_uret({"sub": str(kullanici), "iat": an, "exp": an + int(sure_sn),
                     "subs": {kanal(kosu_id): {}}}, settings.CENTRIFUGO_HMAC_ANAHTARI)


class Yayinci:
    """HTTP API ile yayın. Art arda hata olursa kısa süre denemeyi bırakır (devre kesici)."""

    ARA_SN = 15
    ESIK = 3

    def __init__(self, api_url=None, anahtar=None, zaman_asimi=1.5, acik=None):
        self.api_url = (api_url or settings.CENTRIFUGO_API_URL).rstrip("/")
        self.anahtar = anahtar or settings.CENTRIFUGO_API_ANAHTARI
        self.zaman_asimi = zaman_asimi
        self.acik = settings.CENTRIFUGO_AKTIF if acik is None else acik
        self._hata, self._sessiz_bitis, self.gonderilen, self.basarisiz = 0, 0.0, 0, 0

    def yayinla(self, kanal_adi, veri):
        if not self.acik or time.monotonic() < self._sessiz_bitis:
            return False
        istek = urllib.request.Request(
            f"{self.api_url}/publish", data=json.dumps({"channel": kanal_adi, "data": veri}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-API-Key": self.anahtar}, method="POST")
        try:
            with urllib.request.urlopen(istek, timeout=self.zaman_asimi) as cevap:   # noqa: S310 (yerel/ayarlı adres)
                govde = json.loads(cevap.read() or b"{}")
            if govde.get("error"):
                raise ValueError(f"Centrifugo hatası: {govde['error']}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            self.basarisiz += 1
            self._hata += 1
            if self._hata == 1:
                log.warning("Centrifugo'ya yayın yapılamadı (%s); panel sorgulayarak güncellenir", e)
            if self._hata >= self.ESIK:
                self._sessiz_bitis = time.monotonic() + self.ARA_SN
                self._hata = 0
            return False
        self._hata = 0
        self.gonderilen += 1
        return True
