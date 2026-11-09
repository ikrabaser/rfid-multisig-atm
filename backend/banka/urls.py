from django.urls import path

from . import views

urlpatterns = [
    path("", views.panel, name="panel"),
    path("simulator/", views.simulator_sayfa, name="simulator"),
    path("simulator/komut/", views.simulator_komut, name="simulator_komut"),
    path("simulator/serit/", views.simulator_serit, name="simulator_serit"),
    path("simulator/telefon/", views.simulator_telefon, name="simulator_telefon"),
]
