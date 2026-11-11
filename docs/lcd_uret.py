"""docs/lcd-ekranlari.svg dosyasını üretir (16x2 LCD görünümü). Kullanım: python docs/lcd_uret.py

Ekran metinleri firmware/simülatördeki ekran() çağrılarıyla aynıdır (LCD'de Türkçe karakter yok).
"""
from pathlib import Path
from xml.sax.saxutils import escape

EKRANLAR = [
    ("Bosta", "Karti okutun", "Mini ATM / Kasa"),
    ("PIN girisi", "PIN girin:", "PIN: ****"),
    ("Menu", "A:Bakiye B:Cek", "C:Yatir D:Havale"),
    ("Bakiye", "Bakiye:", "999000.00 TL"),
    ("Onay bekleniyor", "Onay 1/3 22sn", "Yetkili kart oku"),
    ("Ust rol gerekli", "Onay 3/3 9sn", "Ust rol gerekli"),
    ("Yetkili karari", "A:Onayla B:Red", "*:Iptal"),
    ("Sonuc", "Islem onaylandi", "Tamamlandi"),
    ("Kasa acma talebi", "Talep yok", "A:Kasa ac *:Cik"),
    ("Kasa acildi (role)", "Kasa acildi", "5 sn acik"),
    ("Menu, 2. sayfa (3 sn'de bir)", "#:PIN degistir", "*:Cikis"),
    ("PIN degistirme", "Yeni PIN tekrar:", "PIN: ****"),
    ("PIN degisti", "PIN degisti", "Yeni PIN gecerli"),
    # --- para dagitici (simulatorde oynatilir; Arduino'da dagitici donanimi yok)
    ("Para sayiliyor", "Para sayiliyor", "2000 TL"),
    ("Parayi alin", "Parayi alin", "A: Aldim"),
    ("Sikisma (dagitici)", "Para sikisti!", "Bekleyin..."),
    ("Sikisma sonrasi iade", "Para sikisti", "Hesaba iade"),
    ("Eksik verme", "Eksik verildi", "200 TL iade"),
    ("Baglanti koptu", "Baglanti koptu", "Sonuc belirsiz"),
    ("Para alinmadi", "Para alinmadi", "Islem iptal"),
    ("Kart bloke", "Kart bloke", "Subeye basvurun"),
    ("Klon / tekrar oynatma", "Kart reddedildi", "Klon suphesi"),
]
SOL, SAG, ILK_Y, ADIM = 28, 344, 72, 142
YAZI = 'font-family="Segoe UI, Arial, sans-serif"'
MONO = 'font-family="Consolas, Courier New, monospace" font-size="24" font-weight="700" fill="#1f2d10"'


def lcd(x, y, satirlar):
    cikti = [f'<rect x="{x}" y="{y}" width="288" height="88" rx="10" fill="#2b2f33"/>',
             f'<rect x="{x + 10}" y="{y + 10}" width="268" height="68" rx="4" fill="#9cb86b"/>']
    for satir in (0, 1):
        for sutun in range(16):
            cikti.append(f'<rect x="{x + 16 + sutun * 16}" y="{y + 16 + satir * 30}" width="14" height="26" '
                         f'fill="#1f2d10" opacity="0.06"/>')
    for satir, metin in enumerate(satirlar):
        for sutun, harf in enumerate(metin[:16]):
            if harf != " ":
                cikti.append(f'<text x="{x + 16 + sutun * 16}" y="{y + 36 + satir * 30}" {MONO}>{escape(harf)}</text>')
    return cikti


def uret():
    satir_sayisi = (len(EKRANLAR) + 1) // 2
    yukseklik = ILK_Y + (satir_sayisi - 1) * ADIM + 94 + 28
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="660" height="{yukseklik}" viewBox="0 0 660 {yukseklik}" '
         f'role="img" aria-label="Terminal LCD ekranlari">',
         '<rect width="100%" height="100%" fill="#f6f6f4"/>',
         f'<text x="28" y="34" {YAZI} font-size="20" font-weight="600" fill="#1c1c1c">Terminal ekranları (16x2 LCD)</text>']
    for i, (baslik, l1, l2) in enumerate(EKRANLAR):
        x = SOL if i % 2 == 0 else SAG
        y = ILK_Y + (i // 2) * ADIM
        o.append(f'<text x="{x}" y="{y}" {YAZI} font-size="14" fill="#333">{i + 1}. {escape(baslik)}</text>')
        o.extend(lcd(x, y + 10, (l1, l2)))
    o.append("</svg>")
    return "\n".join(o) + "\n"


if __name__ == "__main__":
    hedef = Path(__file__).with_name("lcd-ekranlari.svg")
    hedef.write_text(uret(), encoding="utf-8")
    print(f"{hedef} yazildi ({len(EKRANLAR)} ekran)")
