from django.urls import path

from . import views

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
]
