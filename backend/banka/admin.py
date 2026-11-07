from django import forms
from django.contrib import admin, messages

from . import politika_degisikligi
from .models import (Bildirim, Hesap, HesapHareketi, Islem, Kart, LogKaydi, Musteri, Onay,
                     OnayTalebi, Personel, Politika, PolitikaDegisikligi, PolitikaDegisiklikOyu, Rol,
                     Terminal, TerminalGunlukKaydi)
from .politika_degisikligi import PolitikaHatasi


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
    """Salt okunur: politika yalnızca "Politika değişikliği talepleri" (maker-checker) ile değişir."""
    list_display = ("id", "alt_limit", "ust_limit", "gereken_onay", "en_az_rol", "aciklama")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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
    list_display = ("id", "tur", "tutar", "hesap", "durum", "teslim", "terminal", "olusturma")
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


class PolitikaDegisikligiFormu(forms.ModelForm):
    class Meta:
        model = PolitikaDegisikligi
        fields = ["islem", "hedef_id", "yeni_alt_limit", "yeni_gereken_onay",
                  "yeni_en_az_rol", "yeni_aciklama", "gerekce"]

    def clean(self):
        veri = super().clean()
        if not veri.get("islem"):
            return veri
        try:
            politika_degisikligi.talep_dogrula(
                veri["islem"], veri.get("hedef_id"),
                {"alt": veri.get("yeni_alt_limit"), "onay": veri.get("yeni_gereken_onay"),
                 "rol": veri.get("yeni_en_az_rol"), "aciklama": veri.get("yeni_aciklama")},
                veri.get("gerekce"),
            )
        except PolitikaHatasi as e:
            raise forms.ValidationError(e.mesaj)
        return veri


@admin.register(PolitikaDegisikligi)
class PolitikaDegisikligiAdmin(admin.ModelAdmin):
    """Maker-checker: talep eden (maker) yalnızca talep açar; en az 2 BAŞKA kullanıcı (checker) onaylar."""
    form = PolitikaDegisikligiFormu
    list_display = ("id", "islem", "hedef_id", "ozet", "durum", "talep_eden", "oy_durumu", "son_tarih")
    list_filter = ("durum", "islem")
    actions = ["onayla", "reddet", "iptal_et"]

    def has_add_permission(self, request):
        return request.user.has_perm("banka.politika_talep_et")

    def has_change_permission(self, request, obj=None):
        return False  # talep kaydedildikten sonra degistirilemez

    def has_delete_permission(self, request, obj=None):
        return False

    def has_onayla_permission(self, request):
        return request.user.has_perm("banka.politika_onayla")

    def has_talep_et_permission(self, request):
        return request.user.has_perm("banka.politika_talep_et")

    def save_model(self, request, obj, form, change):
        politika_degisikligi.talep_kaydet(obj, request.user)

    @admin.display(description="Özet")
    def ozet(self, obj):
        if obj.islem == PolitikaDegisikligi.Islem.SIL:
            return f"Sil: politika #{obj.hedef_id}"
        return (f"{obj.get_islem_display()}: {obj.yeni_alt_limit:,.2f} TL üstü → {obj.yeni_gereken_onay} onay "
                f"(en az {Rol(obj.yeni_en_az_rol).label})")

    @admin.display(description="Onay")
    def oy_durumu(self, obj):
        return f"{obj.oylar.filter(karar='ONAY').count()}/{obj.gereken_onay}"

    def _oy(self, request, queryset, karar):
        for talep in queryset:
            try:
                sonuc = politika_degisikligi.oy_ver(talep.id, request.user, karar)
            except PolitikaHatasi as e:
                self.message_user(request, f"Talep #{talep.id}: {e.mesaj}", messages.ERROR)
            else:
                self.message_user(request, f"Talep #{talep.id}: oyunuz kaydedildi, durum: {sonuc.get_durum_display()}",
                                  messages.SUCCESS)

    @admin.action(description="Seçili talepleri onayla", permissions=["onayla"])
    def onayla(self, request, queryset):
        self._oy(request, queryset, "ONAY")

    @admin.action(description="Seçili talepleri reddet", permissions=["onayla"])
    def reddet(self, request, queryset):
        self._oy(request, queryset, "RED")

    @admin.action(description="Kendi bekleyen taleplerimi iptal et", permissions=["talep_et"])
    def iptal_et(self, request, queryset):
        for talep in queryset:
            try:
                politika_degisikligi.iptal(talep.id, request.user)
            except PolitikaHatasi as e:
                self.message_user(request, f"Talep #{talep.id}: {e.mesaj}", messages.ERROR)
            else:
                self.message_user(request, f"Talep #{talep.id} iptal edildi", messages.SUCCESS)


@admin.register(PolitikaDegisiklikOyu)
class PolitikaDegisiklikOyuAdmin(SadeceOkunur):
    list_display = ("talep", "kullanici", "karar", "zaman")
