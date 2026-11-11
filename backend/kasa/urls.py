from django.contrib import admin
from django.urls import include, path

from banka import views

urlpatterns = [
    path("admin/login/", views.admin_giris_yonlendir),   # kendi giris sayfamiz (hatali deneme siniri)
    path("admin/", admin.site.urls),
    path("api/", include("banka.urls_api")),
    path("", include("banka.urls")),
]
