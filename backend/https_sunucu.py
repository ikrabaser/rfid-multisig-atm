"""Gelistirme icin HTTPS sunucusu: kopru -> API arasindaki PIN trafigi sifreli gitsin.

Bu bir GELISTIRME araci (tek is parcacikli). Gercek kurulumda Django'yu gunicorn/waitress
arkasinda, TLS'i Caddy/nginx gibi bir ters vekil ile sonlandir.

Sertifika uretmek (mkcert ornegi):
    mkcert -install
    mkcert localhost 127.0.0.1 ::1          # localhost+2.pem ve localhost+2-key.pem uretir

Calistirma:
    set DJANGO_HTTPS=1
    python https_sunucu.py --sertifika localhost+2.pem --anahtar localhost+2-key.pem

Kopru (mkcert kok sertifikasi: `mkcert -CAROOT` klasorundeki rootCA.pem):
    python serial_bridge.py --port COM3 --api https://127.0.0.1:8443 --ca-dosyasi <rootCA.pem yolu>
"""
import argparse
import os
import ssl
from wsgiref.simple_server import make_server

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "kasa.settings")

from django.contrib.staticfiles.handlers import StaticFilesHandler  # noqa: E402
from django.core.wsgi import get_wsgi_application  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Gelistirme HTTPS sunucusu")
    ap.add_argument("--sertifika", required=True, help="Sertifika dosyasi (.pem)")
    ap.add_argument("--anahtar", required=True, help="Ozel anahtar dosyasi (-key.pem)")
    ap.add_argument("--adres", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8443)
    args = ap.parse_args()

    django_uygulamasi = StaticFilesHandler(get_wsgi_application())

    def https_uygulamasi(environ, start_response):
        # TLS'i bu surec sonlandiriyor; Django request.is_secure() dogru donsun
        environ["wsgi.url_scheme"] = "https"
        environ["HTTPS"] = "on"
        return django_uygulamasi(environ, start_response)

    httpd = make_server(args.adres, args.port, https_uygulamasi)
    baglam = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    baglam.load_cert_chain(args.sertifika, args.anahtar)
    httpd.socket = baglam.wrap_socket(httpd.socket, server_side=True)
    print(f"HTTPS: https://{args.adres}:{args.port}/   (durdurmak icin Ctrl+C)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nKapatildi.")


if __name__ == "__main__":
    main()
