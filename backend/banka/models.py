import re
from decimal import Decimal

from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import models


class Rol(models.IntegerChoices):
    GOREVLI = 1, "Görevli"
    MUDUR_YRD = 2, "Müdür yardımcısı"
    MUDUR = 3, "Müdür"


def uid_duzelt(uid):
    """UID'yi her yerde ayni bicime getirir: kucuk harf, bosluksuz."""
    return (uid or "").strip().lower().replace(" ", "")


class PinliKimlik(models.Model):
    """Kart + PIN ile dogrulanan kayitlar icin ortak alanlar."""

    pin_hash = models.CharField("PIN (hash)", max_length=128)
    hatali_deneme = models.PositiveSmallIntegerField("Hatalı PIN sayısı", default=0)
    bloke = models.BooleanField("Bloke", default=False)

    class Meta:
        abstract = True

    def pin_ayarla(self, pin):
        if not re.fullmatch(r"\d{4}", str(pin or "")):
            raise ValidationError("PIN tam olarak 4 rakam olmalı.")
        self.pin_hash = make_password(str(pin))
        self.hatali_deneme = 0

    def pin_dogru_mu(self, pin):
        return bool(self.pin_hash) and check_password(str(pin or ""), self.pin_hash)


class Musteri(models.Model):
    ad = models.CharField(max_length=80)
    olusturma = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Müşteri"
        verbose_name_plural = "Müşteriler"

    def __str__(self):
        return self.ad


class Hesap(models.Model):
    """Hesap bakiyesi `bakiye` alaninda onbellek olarak durur; gercek kaynak defter
    hareketleridir (HesapHareketi). Fark olursa gun_sonu_mutabakat komutu yakalar."""

    class Tur(models.TextChoices):
        MUSTERI = "MUSTERI", "Müşteri hesabı"
        KASA = "KASA", "Banka nakit kasası"

    musteri = models.ForeignKey(Musteri, on_delete=models.PROTECT, related_name="hesaplar")
    hesap_no = models.CharField("Hesap no", max_length=6, unique=True)
    tur = models.CharField(max_length=8, choices=Tur.choices, default=Tur.MUSTERI)
    acilis_bakiyesi = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    bakiye = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    gunluk_limit = models.DecimalField(
        "Günlük çekme/havale limiti", max_digits=14, decimal_places=2, default=Decimal("1000000.00")
    )

    class Meta:
        verbose_name = "Hesap"
        verbose_name_plural = "Hesaplar"
        constraints = [
            models.CheckConstraint(condition=models.Q(bakiye__gte=0), name="bakiye_negatif_olamaz"),
        ]

    def __str__(self):
        return f"{self.hesap_no} ({self.musteri})"


class Kart(PinliKimlik):
    uid = models.CharField("Kart UID", max_length=20, unique=True)
    hesap = models.ForeignKey(Hesap, on_delete=models.PROTECT, related_name="kartlar")

    class Meta:
        verbose_name = "Müşteri kartı"
        verbose_name_plural = "Müşteri kartları"

    def save(self, *args, **kwargs):
        self.uid = uid_duzelt(self.uid)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.uid} → {self.hesap.hesap_no}"


class Personel(PinliKimlik):
    ad = models.CharField(max_length=80)
    kart_uid = models.CharField("Kart UID", max_length=20, unique=True)
    rol = models.PositiveSmallIntegerField(choices=Rol.choices, default=Rol.GOREVLI)
    aktif = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Personel (yetkili)"
        verbose_name_plural = "Personel (yetkililer)"

    def save(self, *args, **kwargs):
        self.kart_uid = uid_duzelt(self.kart_uid)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.ad} ({self.get_rol_display()})"


class Politika(models.Model):
    """Tutar araligina gore kac onay ve en az hangi rol gerektigini soyler.

    Aralik: alt_limit < tutar <= ust_limit  (ust_limit bos ise sinirsiz).
    gereken_onay = 0 ise islem onaysiz gecer.
    en_az_rol: onaylayanlardan EN AZ BIRI bu role (veya ustune) sahip olmali.
    """

    alt_limit = models.DecimalField(max_digits=14, decimal_places=2)
    ust_limit = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    gereken_onay = models.PositiveSmallIntegerField(default=0)
    en_az_rol = models.PositiveSmallIntegerField(choices=Rol.choices, default=Rol.GOREVLI)
    aciklama = models.CharField(max_length=120, blank=True)

    class Meta:
        verbose_name = "Onay politikası"
        verbose_name_plural = "Onay politikaları"
        ordering = ["alt_limit"]

    def __str__(self):
        ust = "∞" if self.ust_limit is None else self.ust_limit
        return f"{self.alt_limit} – {ust}: {self.gereken_onay} onay (≥ {self.get_en_az_rol_display()})"


class Islem(models.Model):
    class Tur(models.TextChoices):
        CEKME = "CEKME", "Para çekme"
        YATIRMA = "YATIRMA", "Para yatırma"
        HAVALE = "HAVALE", "Havale"
        KASA_ACMA = "KASA_ACMA", "Kasa dairesi açma"

    class Durum(models.TextChoices):
        HAZIR = "HAZIR", "Hazır"
        ONAY_BEKLIYOR = "ONAY_BEKLIYOR", "Onay bekliyor"
        UYGULANDI = "UYGULANDI", "Uygulandı"
        REDDEDILDI = "REDDEDILDI", "Reddedildi"
        SURESI_DOLDU = "SURESI_DOLDU", "Süresi doldu"
        BASARISIZ = "BASARISIZ", "Başarısız"

    hesap = models.ForeignKey(Hesap, on_delete=models.PROTECT, related_name="islemler")
    hedef_hesap = models.ForeignKey(
        Hesap, on_delete=models.PROTECT, null=True, blank=True, related_name="gelen_islemler"
    )
    tur = models.CharField(max_length=10, choices=Tur.choices)
    tutar = models.DecimalField(max_digits=14, decimal_places=2)
    durum = models.CharField(max_length=15, choices=Durum.choices, default=Durum.HAZIR)
    baslatan_uid = models.CharField(max_length=20)
    anahtar = models.CharField(
        "Idempotency anahtarı", max_length=40, null=True, blank=True
    )
    olusturma = models.DateTimeField(auto_now_add=True)
    tamamlanma = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "İşlem"
        verbose_name_plural = "İşlemler"
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["hesap", "anahtar"],
                condition=models.Q(anahtar__isnull=False),
                name="islem_anahtari_tekil",
            ),
        ]

    def __str__(self):
        return f"#{self.id} {self.tur} {self.tutar} [{self.durum}]"


class HesapHareketi(models.Model):
    """Çift taraflı defter satırı. Her işlemde borç toplamı = alacak toplamı.

    Müşteri hesabı (yükümlülük): alacak bakiyeyi artırır, borç azaltır.
    Kasa hesabı (varlık)       : borç bakiyeyi artırır, alacak azaltır.
    Eklenir, değiştirilmez, silinmez.
    """

    class Yon(models.TextChoices):
        BORC = "BORC", "Borç"
        ALACAK = "ALACAK", "Alacak"

    islem = models.ForeignKey(Islem, on_delete=models.PROTECT, related_name="hareketler")
    hesap = models.ForeignKey(Hesap, on_delete=models.PROTECT, related_name="hareketler")
    yon = models.CharField(max_length=6, choices=Yon.choices)
    tutar = models.DecimalField(max_digits=14, decimal_places=2)
    bakiye_sonrasi = models.DecimalField(max_digits=14, decimal_places=2)
    zaman = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Defter hareketi"
        verbose_name_plural = "Defter hareketleri"
        ordering = ["-id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(tutar__gt=0), name="hareket_tutari_pozitif"),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("Defter hareketleri değiştirilemez.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Defter hareketleri silinemez.")

    def __str__(self):
        return f"işlem #{self.islem_id} {self.hesap.hesap_no} {self.yon} {self.tutar}"


class OnayTalebi(models.Model):
    class Durum(models.TextChoices):
        BEKLIYOR = "BEKLIYOR", "Bekliyor"
        ONAYLANDI = "ONAYLANDI", "Onaylandı"
        UYGULANDI = "UYGULANDI", "Uygulandı"
        REDDEDILDI = "REDDEDILDI", "Reddedildi"
        SURESI_DOLDU = "SURESI_DOLDU", "Süresi doldu"
        BASARISIZ = "BASARISIZ", "Başarısız"

    islem = models.OneToOneField(Islem, on_delete=models.PROTECT, related_name="talep")
    gereken_onay = models.PositiveSmallIntegerField()
    en_az_rol = models.PositiveSmallIntegerField(choices=Rol.choices)
    son_tarih = models.DateTimeField()
    durum = models.CharField(max_length=15, choices=Durum.choices, default=Durum.BEKLIYOR)
    olusturma = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Onay talebi"
        verbose_name_plural = "Onay talepleri"
        ordering = ["-id"]

    def __str__(self):
        return f"Talep #{self.id} → işlem #{self.islem_id} [{self.durum}]"


class Onay(models.Model):
    class Karar(models.TextChoices):
        ONAY = "ONAY", "Onay"
        RED = "RED", "Red"

    talep = models.ForeignKey(OnayTalebi, on_delete=models.PROTECT, related_name="onaylar")
    personel = models.ForeignKey(Personel, on_delete=models.PROTECT, related_name="onaylari")
    karar = models.CharField(max_length=5, choices=Karar.choices)
    zaman = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Onay oyu"
        verbose_name_plural = "Onay oyları"
        constraints = [
            models.UniqueConstraint(fields=["talep", "personel"], name="her_yetkili_bir_oy"),
        ]

    def __str__(self):
        return f"{self.personel.ad}: {self.karar} (talep #{self.talep_id})"


class LogKaydi(models.Model):
    """Hash zincirli denetim kaydi. Eklenir, degistirilmez, silinmez."""

    sira = models.PositiveBigIntegerField(unique=True)
    zaman = models.DateTimeField()
    olay_turu = models.CharField(max_length=40)
    veri = models.TextField()
    onceki_hash = models.CharField(max_length=64)
    hash = models.CharField(max_length=64)

    class Meta:
        verbose_name = "Denetim kaydı"
        verbose_name_plural = "Denetim kayıtları"
        ordering = ["sira"]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("Denetim kayıtları değiştirilemez.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Denetim kayıtları silinemez.")

    def __str__(self):
        return f"#{self.sira} {self.olay_turu}"
