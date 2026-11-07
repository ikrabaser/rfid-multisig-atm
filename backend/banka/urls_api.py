from django.urls import path

from . import api

urlpatterns = [
    path("kart/", api.kart),
    path("giris/", api.giris),
    path("bakiye/", api.bakiye),
    path("islem/", api.islem),
    path("kasa-ac/", api.kasa_ac),
    path("teslim/", api.teslim),
    path("onay/", api.onay),
    path("talep/", api.talep),
    path("bekleyen/", api.bekleyen),
    path("log/dogrula/", api.log_dogrula),
    path("terminal-gunlugu/", api.terminal_gunlugu),
]
