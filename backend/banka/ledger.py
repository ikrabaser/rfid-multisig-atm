"""Çift taraflı defter ve gün sonu mutabakatı.

Bakiye tek başına bir sayı değildir: her para hareketi, toplamı sıfır olan borç/alacak
satırlarıdır. `Hesap.bakiye` hızlı okuma için tutulan önbelleğdir; mutabakat komutu
bunu defterden yeniden hesaplayıp karşılaştırır.
"""
import datetime
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone

from .models import Hesap, HesapHareketi, Islem

MUSTERI_ISLEMLERI = (Islem.Tur.CEKME, Islem.Tur.YATIRMA, Islem.Tur.HAVALE, Islem.Tur.TERS_KAYIT)  # defter satiri yazan islemler


def _para(d):
    return f"{Decimal(d):.2f}"


def etki(hesap, yon):
    """Bu satırın hesabın (doğal) bakiyesine etkisi: +1 ya da -1."""
    if hesap.tur == Hesap.Tur.KASA:
        return 1 if yon == HesapHareketi.Yon.BORC else -1
    return 1 if yon == HesapHareketi.Yon.ALACAK else -1


def hareket_ekle(islem, hesap, yon, tutar):
    """Kilitli hesap nesnesinin bakiyesini günceller ve defter satırını yazar.

    Çağıran, hesabı select_for_update ile alıp sonra save(update_fields=["bakiye"]) yapar.
    """
    hesap.bakiye += etki(hesap, yon) * tutar
    return HesapHareketi.objects.create(
        islem=islem, hesap=hesap, yon=yon, tutar=tutar, bakiye_sonrasi=hesap.bakiye
    )


def hesaplanan_bakiye(hesap):
    """Açılış bakiyesi + defter hareketlerinin net etkisi."""
    satirlar = hesap.hareketler.aggregate(
        borc=Sum("tutar", filter=Q(yon=HesapHareketi.Yon.BORC)),
        alacak=Sum("tutar", filter=Q(yon=HesapHareketi.Yon.ALACAK)),
    )
    borc = satirlar["borc"] or Decimal("0")
    alacak = satirlar["alacak"] or Decimal("0")
    net = (borc - alacak) if hesap.tur == Hesap.Tur.KASA else (alacak - borc)
    return hesap.acilis_bakiyesi + net


def mutabakat(tarih=None):
    """Defteri, işlemleri ve hash zincirini birbirleriyle karşılaştırır."""
    from .hashlog import zinciri_dogrula

    bakiye_farklari = []
    for h in Hesap.objects.order_by("hesap_no"):
        hesaplanan = hesaplanan_bakiye(h)
        if hesaplanan != h.bakiye:
            bakiye_farklari.append({
                "hesap_no": h.hesap_no, "kayitli": _para(h.bakiye), "hesaplanan": _para(hesaplanan),
            })

    toplamlar = {}
    for satir in HesapHareketi.objects.values("islem_id").annotate(
        borc=Sum("tutar", filter=Q(yon=HesapHareketi.Yon.BORC)),
        alacak=Sum("tutar", filter=Q(yon=HesapHareketi.Yon.ALACAK)),
    ):
        toplamlar[satir["islem_id"]] = (satir["borc"] or Decimal("0"), satir["alacak"] or Decimal("0"))

    uygulanan_idler = set()
    dengesiz, hareketsiz = [], []
    for islem in Islem.objects.filter(
        durum=Islem.Durum.UYGULANDI, tur__in=[t.value for t in MUSTERI_ISLEMLERI]
    ).iterator():
        uygulanan_idler.add(islem.id)
        if islem.id not in toplamlar:
            hareketsiz.append(islem.id)
            continue
        borc, alacak = toplamlar[islem.id]
        if borc != alacak or borc != islem.tutar:
            dengesiz.append(islem.id)
    yetim = sorted(i for i in toplamlar if i not in uygulanan_idler)

    # Ters kayitlar: her ters kayit uygulanmis bir nakit cekmeyi (ayni hesap, ayni tutar) geri almali,
    # geri alinmis her cekmenin de tam bir ters kaydi olmali.
    tutarsiz_ters = []
    for t in Islem.objects.filter(tur=Islem.Tur.TERS_KAYIT, durum=Islem.Durum.UYGULANDI).select_related("ters_islem"):
        o = t.ters_islem
        if (o is None or o.tur != Islem.Tur.CEKME or o.durum != Islem.Durum.UYGULANDI
                or o.teslim != Islem.Teslim.GERI_ALINDI or o.tutar != t.tutar or o.hesap_id != t.hesap_id):
            tutarsiz_ters.append(t.id)
    tersi_olmayan = sorted(
        Islem.objects.filter(teslim=Islem.Teslim.GERI_ALINDI)
        .exclude(id__in=Islem.objects.filter(ters_islem__isnull=False).values("ters_islem_id"))
        .values_list("id", flat=True)
    )
    # Uyari (hata degil): teslimi 10 dakikadan uzun suredir bildirilmeyen nakit cekmeler
    belirsiz_teslim = sorted(
        Islem.objects.filter(
            tur=Islem.Tur.CEKME, durum=Islem.Durum.UYGULANDI, teslim=Islem.Teslim.BEKLIYOR,
            tamamlanma__lt=timezone.now() - datetime.timedelta(minutes=10),
        ).values_list("id", flat=True)
    )

    from . import kart_butunlugu, personel_butunlugu, politika_degisikligi
    politika_durumu = politika_degisikligi.butunluk_durumu()
    personel_durumu = personel_butunlugu.durum()
    kart_durumu = kart_butunlugu.durum()

    zincir = zinciri_dogrula()

    gun = tarih or timezone.localdate()
    gun_basi = timezone.make_aware(datetime.datetime.combine(gun, datetime.time.min))
    gun_sonu = gun_basi + datetime.timedelta(days=1)
    uygulananlar = (
        Islem.objects.filter(durum=Islem.Durum.UYGULANDI, tamamlanma__gte=gun_basi, tamamlanma__lt=gun_sonu)
        .values("tur").annotate(adet=Count("id"), toplam=Sum("tutar")).order_by("tur")
    )
    durumlar = (
        Islem.objects.filter(olusturma__gte=gun_basi, olusturma__lt=gun_sonu)
        .values("durum").annotate(adet=Count("id")).order_by("durum")
    )
    return {
        "saglam": (not (bakiye_farklari or dengesiz or hareketsiz or yetim or tutarsiz_ters or tersi_olmayan)
                   and zincir["saglam"] and politika_durumu != "tutarsiz" and personel_durumu != "tutarsiz"
                   and kart_durumu != "tutarsiz"),
        "tarih": gun.isoformat(),
        "bakiye_farklari": bakiye_farklari,
        "dengesiz_islemler": dengesiz,
        "hareketsiz_islemler": hareketsiz,
        "yetim_hareketler": yetim,
        "tutarsiz_ters_kayitlar": tutarsiz_ters,
        "tersi_olmayan_geri_alinanlar": tersi_olmayan,
        "belirsiz_teslimler": belirsiz_teslim,
        "politika": politika_durumu,
        "personel": personel_durumu,
        "kart": kart_durumu,
        "zincir": zincir,
        "uygulananlar": [
            {"tur": u["tur"], "adet": u["adet"], "toplam": _para(u["toplam"] or 0)} for u in uygulananlar
        ],
        "durumlar": {d["durum"]: d["adet"] for d in durumlar},
    }
