from django import forms
from django.contrib import admin, messages

from . import politika_degisikligi, yonetim_talepleri
from .models import (Bildirim, Hesap, HesapHareketi, Islem, Kaset, Kart, LogKaydi, Musteri, Onay,
                     OnayTalebi, Personel, Politika, PolitikaDegisikligi, PolitikaDegisiklikOyu, Rol, SimulasyonKosusu,
                     Terminal, TerminalGunlukKaydi, YonetimOyu, YonetimTalebi)
from .politika_degisikligi import PolitikaHatasi
from .yonetim_talepleri import YonetimHatasi


@admin.register(Kart)
class KartAdmin(admin.ModelAdmin):
    """Salt okunur. Kart tanımlama ve PIN sıfırlama 'Yönetim talepleri' (maker-checker) ile yapılır, yeni PIN
    müşterinin kendi telefonuna SMS ile gider; bloke kaldırma da talep ile. Admin hiçbir PIN'i bilemez ya da belirleyemez."""
    list_display = ("uid", "hesap", "bloke", "hatali_deneme")
    list_filter = ("bloke",)
    exclude = ("pin_hash", "mac")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Personel)
class PersonelAdmin(admin.ModelAdmin):
    """Yetkili ekleme, rol, aktiflik, PIN ve bloke yalnızca 'Yönetim talepleri' (maker-checker) ile değişir.
    Burada yalnızca iletişim bilgileri (ad, e-posta, telefon) düzenlenebilir."""
    list_display = ("ad", "kart_uid", "rol", "eposta", "aktif", "bloke")
    list_filter = ("rol", "aktif", "bloke")
    exclude = ("pin_hash",)
    readonly_fields = ("kart_uid", "rol", "aktif", "bloke", "hatali_deneme", "imza_sayaci", "imza_zamani")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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
    """Bakiye yalnızca defter hareketleriyle, günlük limit yalnızca 'Yönetim talepleri' ile değişir."""
    list_display = ("hesap_no", "musteri", "tur", "bakiye", "gunluk_limit")
    list_filter = ("tur",)
    readonly_fields = ("tur", "acilis_bakiyesi", "bakiye", "gunluk_limit")


@admin.register(Musteri)
class MusteriAdmin(admin.ModelAdmin):
    """Telefon (PIN'in gideceği numara) yalnızca 'Yönetim talepleri → Müşteri telefonunu değiştir' ile değişir."""
    list_display = ("ad", "telefon_maskeli")
    readonly_fields = ("telefon",)

    @admin.display(description="Telefon")
    def telefon_maskeli(self, obj):
        return yonetim_talepleri._maske(obj.telefon) if obj.telefon else "-"


class SadeceOkunur(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SimulasyonKosusu)
class SimulasyonKosusuAdmin(SadeceOkunur):
    list_display = ("id", "durum", "olusturan", "olusturma", "bitis")
    list_filter = ("durum",)


@admin.register(Islem)
class IslemAdmin(SadeceOkunur):
    list_display = ("id", "tur", "tutar", "hesap", "durum", "teslim", "risk_skoru", "dagitim_plani", "terminal", "olusturma")
    list_filter = ("tur", "durum", "teslim")


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
    """Açık/kapalı durumu yalnızca 'Yönetim talepleri → Terminali aç/kapat' ile değişir."""
    list_display = ("kod", "ad", "enlem", "boylam", "aktif", "nakit_dagitimi")
    readonly_fields = ("aktif", "nakit_dagitimi")


@admin.register(Kaset)
class KasetAdmin(SadeceOkunur):
    """Stok yalnızca servis katmanı ve 'kaset_doldur' komutuyla değişir (hash zincirine yazılır)."""
    list_display = ("terminal", "nominal", "adet", "rezerve", "kullanilabilir", "iade_kutusu")
    list_filter = ("terminal",)


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


# ---------------------------------------------------------------- hassas yonetim islemleri (maker-checker)

def _ham(veri):
    """Admin formundaki alanları işlem türüne göre servisin beklediği sözlüğe çevirir."""
    tur = veri["tur"]
    T = YonetimTalebi.Tur
    if tur == T.PERSONEL_EKLE:
        return {"ad": veri.get("ad"), "kart_uid": veri.get("kart_uid"), "rol": veri.get("deger"),
                "eposta": veri.get("eposta"), "telefon": veri.get("telefon")}
    if tur == T.PERSONEL_ROL:
        return {"personel_id": veri.get("hedef_no"), "yeni_rol": veri.get("deger")}
    if tur == T.PERSONEL_DURUM:
        return {"personel_id": veri.get("hedef_no"), "aktif": veri.get("deger")}
    if tur == T.PERSONEL_PIN_SIFIRLA:
        return {"personel_id": veri.get("hedef_no")}
    if tur == T.BLOKE_KALDIR:
        return {"hedef": veri.get("hedef_turu"), "id": veri.get("hedef_no")}
    if tur == T.GUNLUK_LIMIT:
        return {"hesap_no": veri.get("hedef_no"), "yeni_limit": veri.get("deger")}
    if tur == T.KART_TANIMLA:
        return {"hesap_no": veri.get("hedef_no"), "kart_uid": veri.get("kart_uid")}
    if tur == T.KART_PIN_SIFIRLA:
        return {"kart_id": veri.get("hedef_no")}
    if tur == T.MUSTERI_TELEFON:
        return {"musteri_id": veri.get("hedef_no"), "yeni_telefon": veri.get("telefon")}
    if tur == T.NAKIT_COZ:
        return {"islem_id": veri.get("hedef_no"), "sonuc": veri.get("deger"), "verilen": veri.get("verilen")}
    return {"kod": veri.get("hedef_no"), "aktif": veri.get("deger")}  # TERMINAL_DURUM


class YonetimTalebiFormu(forms.ModelForm):
    hedef_no = forms.CharField(label="Hedef", required=False,
                               help_text="Personel no / kart no / hesap no / müşteri no / terminal kodu / nakit çekme işlem no")
    hedef_turu = forms.ChoiceField(label="Hedef türü (yalnızca bloke kaldırma)", required=False,
                                   choices=[("", "—"), ("personel", "Yetkili"), ("kart", "Müşteri kartı")])
    deger = forms.CharField(label="Yeni değer", required=False,
                            help_text="Rol 1-3 / aktif ya da pasif / günlük limit (TL) / nakit çözümü: VERILDI, VERILMEDI ya da KISMI")
    ad = forms.CharField(label="Ad (yeni yetkili)", required=False)
    kart_uid = forms.CharField(label="Kart UID (yeni yetkili ya da yeni müşteri kartı)", required=False)
    verilen = forms.CharField(label="Fiilen verilen banknotlar (yalnızca nakit çözümü, KISMI)", required=False,
                              help_text="Örn. 200x8 ya da 200x8+100x2")
    eposta = forms.EmailField(label="E-posta (yeni yetkili)", required=False)
    telefon = forms.CharField(label="Telefon (yeni yetkili ya da müşteri)", required=False,
                              help_text="Yeni yetkilide ilk PIN bu telefona gider; hiçbir ekranda gösterilmez.")

    class Meta:
        model = YonetimTalebi
        fields = ["tur", "gerekce"]

    def clean(self):
        veri = super().clean()
        if not veri.get("tur"):
            return veri
        try:
            yonetim_talepleri.talep_dogrula(veri["tur"], _ham(veri), veri.get("gerekce"))
        except YonetimHatasi as e:
            raise forms.ValidationError(e.mesaj)
        return veri


@admin.register(YonetimTalebi)
class YonetimTalebiAdmin(admin.ModelAdmin):
    """Maker-checker: talep eden (maker) yalnızca talep açar; işlemin ağırlığına göre 1-3 BAŞKA kullanıcı onaylar."""
    form = YonetimTalebiFormu
    list_display = ("id", "tur", "ozet", "durum", "talep_eden", "oy_durumu", "son_tarih")
    list_filter = ("durum", "tur")
    actions = ["onayla", "reddet", "iptal_et"]

    def has_add_permission(self, request):
        return request.user.has_perm("banka.yonetim_talep_et")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_onayla_permission(self, request):
        return request.user.has_perm("banka.yonetim_onayla")

    def has_talep_et_permission(self, request):
        return request.user.has_perm("banka.yonetim_talep_et")

    def save_model(self, request, obj, form, change):
        yonetim_talepleri.talep_kaydet(obj, request.user, _ham(form.cleaned_data))

    @admin.display(description="Onay")
    def oy_durumu(self, obj):
        return f"{obj.oylar.filter(karar='ONAY').count()}/{obj.gereken_onay}"

    def _oy(self, request, queryset, karar):
        for talep in queryset:
            try:
                sonuc = yonetim_talepleri.oy_ver(talep.id, request.user, karar)
            except YonetimHatasi as e:
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
                yonetim_talepleri.iptal(talep.id, request.user)
            except YonetimHatasi as e:
                self.message_user(request, f"Talep #{talep.id}: {e.mesaj}", messages.ERROR)
            else:
                self.message_user(request, f"Talep #{talep.id} iptal edildi", messages.SUCCESS)


@admin.register(YonetimOyu)
class YonetimOyuAdmin(SadeceOkunur):
    list_display = ("talep", "kullanici", "karar", "zaman")
