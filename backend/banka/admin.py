from django import forms
from django.contrib import admin

from .models import (Bildirim, Hesap, HesapHareketi, Islem, Kart, LogKaydi, Musteri, Onay,
                     OnayTalebi, Personel, Politika, Terminal, TerminalGunlukKaydi)


class PinFormu(forms.ModelForm):
    yeni_pin = forms.CharField(
        label="Yeni PIN", required=False, widget=forms.PasswordInput(render_value=False),
        help_text="4 rakam. Boş bırakırsan PIN değişmez (yeni kayıtta zorunlu).",
    )

    def clean(self):
        veri = super().clean()
        pin = veri.get("yeni_pin")
        if not self.instance.pk and not pin:
            self.add_error("yeni_pin", "Yeni kayıt için PIN zorunlu.")
        if pin:
            try:
                self.instance.pin_ayarla(pin)
            except Exception as e:  # ValidationError
                self.add_error("yeni_pin", getattr(e, "messages", [str(e)]))
        return veri


class PinliAdmin(admin.ModelAdmin):
    form = PinFormu
    exclude = ("pin_hash",)
    readonly_fields = ("hatali_deneme",)

    def save_model(self, request, obj, form, change):
        pin = form.cleaned_data.get("yeni_pin")
        if pin:
            obj.pin_ayarla(pin)
        if change and "bloke" in form.changed_data and not obj.bloke:
            obj.hatali_deneme = 0  # blokeyi kaldirinca deneme hakki yenilenir
        super().save_model(request, obj, form, change)


@admin.register(Kart)
class KartAdmin(PinliAdmin):
    list_display = ("uid", "hesap", "bloke", "hatali_deneme")
    list_filter = ("bloke",)


@admin.register(Personel)
class PersonelAdmin(PinliAdmin):
    list_display = ("ad", "kart_uid", "rol", "eposta", "aktif", "bloke")
    list_filter = ("rol", "aktif", "bloke")


@admin.register(Politika)
class PolitikaAdmin(admin.ModelAdmin):
    list_display = ("alt_limit", "ust_limit", "gereken_onay", "en_az_rol", "aciklama")


@admin.register(Hesap)
class HesapAdmin(admin.ModelAdmin):
    list_display = ("hesap_no", "musteri", "tur", "bakiye", "gunluk_limit")
    list_filter = ("tur",)


admin.site.register(Musteri)


class SadeceOkunur(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Islem)
class IslemAdmin(SadeceOkunur):
    list_display = ("id", "tur", "tutar", "hesap", "durum", "olusturma")
    list_filter = ("tur", "durum")


@admin.register(OnayTalebi)
class OnayTalebiAdmin(SadeceOkunur):
    list_display = ("id", "islem", "gereken_onay", "en_az_rol", "durum", "son_tarih")


@admin.register(Onay)
class OnayAdmin(SadeceOkunur):
    list_display = ("talep", "personel", "karar", "zaman")


@admin.register(LogKaydi)
class LogKaydiAdmin(SadeceOkunur):
    list_display = ("sira", "zaman", "olay_turu", "hash")
    search_fields = ("olay_turu", "veri")


@admin.register(HesapHareketi)
class HesapHareketiAdmin(SadeceOkunur):
    list_display = ("id", "islem", "hesap", "yon", "tutar", "bakiye_sonrasi", "zaman")
    list_filter = ("yon",)


@admin.register(Terminal)
class TerminalAdmin(admin.ModelAdmin):
    list_display = ("kod", "ad", "enlem", "boylam", "aktif")


@admin.register(TerminalGunlukKaydi)
class TerminalGunlukKaydiAdmin(SadeceOkunur):
    list_display = ("terminal", "olay", "terminal_zamani", "guvenilir_zaman", "yuklenme")
    list_filter = ("terminal", "olay")


@admin.register(Bildirim)
class BildirimAdmin(SadeceOkunur):
    list_display = ("olusturma", "kanal", "durum", "alici", "konu")
    list_filter = ("kanal", "durum", "olay")
