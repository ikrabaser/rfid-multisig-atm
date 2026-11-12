from django.urls import path

from . import firmware_lab, views
from .operasyon import gorunumler as op

urlpatterns = [
    path("", views.panel, name="panel"),
    path("giris/", views.giris_sayfa, name="giris"),
    path("cikis/", views.cikis, name="cikis"),
    path("sifre-degistir/", views.sifre_degistir, name="sifre_degistir"),
    path("simulator/", views.simulator_sayfa, name="simulator"),
    path("simulator/komut/", views.simulator_komut, name="simulator_komut"),
    path("simulator/serit/", views.simulator_serit, name="simulator_serit"),
    path("simulator/telefon/", views.simulator_telefon, name="simulator_telefon"),
    path("simulator/operator/", views.simulator_operator, name="simulator_operator"),
    path("simulator/kart-dokun/", views.simulator_kart_dokun, name="simulator_kart_dokun"),
    path("firmware-lab/", firmware_lab.sayfa, name="firmware_lab"),
    path("firmware-lab/hat/", firmware_lab.hat, name="firmware_lab_hat"),
    path("operasyon/", op.liste, name="operasyon"),
    path("operasyon/baslat/", op.baslat, name="operasyon_baslat"),
    path("operasyon/<int:kosu_id>/", op.kosu, name="operasyon_kosu"),
    path("operasyon/<int:kosu_id>/durum/", op.durum, name="operasyon_durum"),
    path("operasyon/<int:kosu_id>/jeton/", op.jeton, name="operasyon_jeton"),
    path("operasyon/<int:kosu_id>/iptal/", op.iptal, name="operasyon_iptal"),
]
