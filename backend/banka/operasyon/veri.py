"""Sandbox'a sanal müşteri, yetkili ve terminal verisi kurar (yalnızca sandbox veritabanında çağrılır)."""
import io
import random
from dataclasses import dataclass, field
from decimal import Decimal

from django.core.management import call_command
from django.db import transaction

from .. import nakit, services
from ..management.commands import seed_demo
from ..models import Hesap, Kart, Kaset, Musteri, Terminal

PIN_HAVUZU_BOYUTU = 10


@dataclass
class SanalMusteri:
    sira: int
    uid: str
    pin: str
    hesap_no: str
    ev_terminal: str
    acilis_bakiyesi: Decimal


@dataclass
class Yetkili:
    uid: str
    pin: str
    rol: int
    ad: str


@dataclass
class Dunya:
    musteriler: list = field(default_factory=list)
    yetkililer: list = field(default_factory=list)
    terminaller: list = field(default_factory=list)
    baslangic_nakit: dict = field(default_factory=dict)      # terminal kodu -> TL


def _pin_havuzu(rng):
    pinler = []
    while len(pinler) < PIN_HAVUZU_BOYUTU:
        pin = f"{rng.randrange(1000, 10000)}"
        if not services.pin_zayif_mi(pin) and pin not in pinler:
            pinler.append(pin)
    return pinler


def terminal_kodlari(n):
    return [f"ATM-{i:02d}" for i in range(1, n + 1)]


def kur(p):
    """Önce müşteriler (seed_demo kart mühürlerini ve onaylı başlangıç görüntüsünü HEPSİ için kaydetsin), sonra demo verisi."""
    rng = random.Random(p.tohum)
    pinler = _pin_havuzu(rng)
    ornek = Kart()
    hashler = {}
    for pin in pinler:                      # hash bir kez hesaplanır: 500 müşteri için 500 PBKDF2 beklemeyelim
        ornek.pin_ayarla(pin)
        hashler[pin] = ornek.pin_hash

    kodlar = terminal_kodlari(p.terminal_sayisi)
    dunya = Dunya(terminaller=kodlar)
    with transaction.atomic():
        for i in range(p.musteri_sayisi):
            pin = pinler[i % len(pinler)]
            hesap_no = f"{200000 + i}"
            bakiye = Decimal(rng.randrange(20_000, 200_001, 1000))
            musteri = Musteri.objects.create(ad=f"Sanal Müşteri {i + 1}", telefon=f"+90555{2000000 + i}")
            hesap = Hesap.objects.create(musteri=musteri, hesap_no=hesap_no, acilis_bakiyesi=bakiye, bakiye=bakiye)
            uid = f"d{i:07x}"
            Kart.objects.create(uid=uid, hesap=hesap, pin_hash=hashler[pin])
            dunya.musteriler.append(SanalMusteri(i, uid, pin, hesap_no, rng.choice(kodlar), bakiye))

    call_command("seed_demo", stdout=io.StringIO())           # kasa, politikalar, yetkililer, terminaller, bütünlük görüntüleri

    for kod in kodlar:                                         # seed_demo yalnızca ATM-01 ve ATM-02'yi kurar
        terminal, _ = Terminal.objects.get_or_create(kod=kod, defaults={"ad": f"Sanal Şube {kod[-2:]}"})
        for nominal in nakit.nominaller():
            Kaset.objects.get_or_create(terminal=terminal, nominal=nominal, defaults={"adet": seed_demo.KASET_ADEDI})
        dunya.baslangic_nakit[kod] = sum(
            k.nominal * k.adet for k in Kaset.objects.filter(terminal=terminal))
    for ad, uid, rol, pin, _eposta, _telefon in seed_demo.PERSONEL:
        dunya.yetkililer.append(Yetkili(uid, pin, int(rol), ad))
    dunya.yetkililer.sort(key=lambda y: y.rol)                 # en düşük yetkiden başlayarak seçilir
    return dunya
