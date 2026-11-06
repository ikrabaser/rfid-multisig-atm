# RFID Banka + Çok İmzalı Kasa

Arduino + RC522 ile kartlı mini ATM. Küçük işlemler direkt geçer, büyük işlemler **N-of-M yetkili onayı** ister
(ör. 5 yetkiliden en az 3'ü, 30 saniye içinde, içlerinden biri müdür). Her olay **HMAC'li hash zincirine** yazılır;
geçmişte bir kayıt değiştirilirse ya da silinirse zincir bozulur ve fark edilir.

> Bu bir **simülasyon**. RC522 gerçek banka kartlarını (EMV) okuyamaz, yalnızca UID görür. Para sanaldır.

```
Arduino terminal  --seri (USB)-->  Python köprüsü  --HTTP/JSON-->  Django backend (SQLite)
 RC522+keypad+LCD                  bridge/serial_bridge.py          backend/
```

Arduino **hiçbir karar vermez**: kart UID'sini ve tuşları yollar, cevaba göre ekrana yazar. PIN doğrulama, bakiye,
politika, onay kuralları ve log tamamen sunucudadır.

## Klasör yapısı

| Yol | Ne var |
|---|---|
| `arduino/atm_terminal/atm_terminal.ino` | Terminal yazılımı (kart, PIN, LCD menü, onay bekleme ekranı) |
| `bridge/serial_bridge.py` | Seri port ↔ API köprüsü (`--konsol` ile Arduino'suz da denenir) |
| `backend/banka/models.py` | Müşteri, Hesap, Kart, Personel, Politika, İşlem, OnayTalebi, Onay, LogKaydi |
| `backend/banka/services.py` | PIN/bloke, oturum token'ı, politika motoru, onay akışı, atomik bakiye |
| `backend/banka/hashlog.py` | Hash zinciri ekleme ve doğrulama |
| `backend/banka/api.py` | Köprünün konuştuğu JSON API |
| `backend/banka/tests.py` | Politika, onay, süre, PIN, zincir testleri |

## Kurulum (Windows)

```bat
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

python manage.py makemigrations banka
python manage.py migrate
python manage.py seed_demo
python manage.py createsuperuser
python manage.py test
python manage.py runserver
```

Gereken Django sürümü **5.1 veya üstü**. `seed_demo` sahte UID'li demo müşteri/yetkili/politika üretir ve PIN'leri yazdırır
(müşteriler `1234` / `4321`, yetkililer `1111`…`5555`). Gerçek kartlarının UID'sini admin panelinden (`/admin/`) gir.

İkinci terminalde köprü:

```bat
cd bridge
pip install -r requirements.txt
python serial_bridge.py --port COM3
```

Bir kartın UID'sini öğrenmek için kartı terminale okut; köprü satırı `KART|c518cc4f -> TIP` diye yazdırır.
Seri port aynı anda tek programda açık olabilir, **Arduino IDE'nin Serial Monitor'unu kapat**.

Panel: `http://127.0.0.1:8000/` (staff girişi ister): bekleyen onaylar, son işlemler, hesaplar, zincir durumu.

## Bağlantılar (Uno)

| Parça | Pin |
|---|---|
| RC522 | SDA=10, RST=9, MOSI=11, MISO=12, SCK=13, **3.3V** |
| Keypad 4x4 | satırlar 2,3,4,5 — sütunlar 6,7,8,A0 |
| I2C LCD 16x2 | SDA=A4, SCL=A5 (adres `0x27`, olmazsa `0x3F`) |
| Buzzer | A1 |
| "Para çıkışı" LED'i | A2 |

Kütüphaneler (Library Manager): **MFRC522**, **Keypad**, **LiquidCrystal I2C**.
Uno'da RAM dar; metinler bu yüzden `F()` ile flash'ta tutuluyor. "Low memory" uyarısı alırsan Mega ya da ESP32 kullan.

## Kurallar (varsayılan politika, admin panelinden değişir)

| Tutar (çekme / havale) | Gereken onay |
|---|---|
| ≤ 5.000 | Onay yok |
| 5.000 – 50.000 | 1 yetkili |
| 50.000 – 500.000 | 2 yetkili, en az biri müdür yardımcısı veya üstü |
| > 500.000 | 3 yetkili, en az biri müdür |

- Yatırma onaysız geçer.
- Onaylar talep açıldıktan sonra `ONAY_PENCERESI_SN` (30 sn) içinde toplanmalı, yoksa talep düşer.
- Aynı yetkili iki kez sayılmaz. İşlemi başlatan kendi işlemini onaylayamaz. Herhangi bir yetkili reddedebilir.
- **Mesai dışı** (09–18 dışı ve hafta sonu) onay isteyen işlemlerde **+1 onay** ve **bir üst rol** gerekir.
  Gece denerken fazladan onay istemesini istemiyorsan: `set MESAI_DISI_KURALI=0` (sonra `runserver`).
- Aktif yetkili sayısı ya da gerekli rol yetmiyorsa işlem açılmaz (güvenli tarafta kalır).
- 3 yanlış PIN kartı bloke eder (müşteri ve yetkili). Blokeyi admin panelinden kaldır.

## Arduino olmadan deneme (köprü konsol modu)

```bat
python serial_bridge.py --konsol
```

```
KART|a0a0a001
PIN|a0a0a001|1234            <- GIRIS|<token>|Ali Yilmaz
ISLEM|<token>|C|600000|      <- BEKLE|1|3       (token 120 sn geçerli)
ONAY|1|b0b0b001|1111|E       <- ONAY|1|3|BEKLIYOR
ONAY|1|b0b0b002|2222|E
ONAY|1|b0b0b003|3333|E       <- 3 onay var ama müdür yok: BEKLIYOR, "üst yetkili lazım"
ONAY|1|b0b0b005|5555|E       <- UYGULANDI
```

## Hash zinciri

Her kayıt: `HMAC-SHA256(anahtar, önceki_hash | sıra | zaman | olay | veri)`. Doğrulama:

```bat
python manage.py log_dogrula
python manage.py log_dogrula --demir demir.txt                  :: son hash'i dışarı yaz
python manage.py log_dogrula --beklenen-son-hash <hash>         :: sondan silme var mı?
```

Dürüst sınırlar:
- Zincir, **ortadaki** bir kaydın değiştirilmesini/silinmesini yakalar. **Sondaki** kayıtların silinmesi tek başına
  fark edilmez; bunun için `--demir` ile son hash'i düzenli olarak dışarı (e-posta, ayrı disk) yazıp
  `--beklenen-son-hash` ile karşılaştır.
- `LOG_HMAC_ANAHTARI` ortam değişkenini veritabanının **dışında** tut. Anahtarı bilen biri zinciri baştan hesaplayabilir.
- Hash zinciri veritabanına erişimi engellemez, değişikliği **görünür** kılar.

## Protokol (Arduino ↔ köprü)

Satır satır metin, alanlar `|` ile ayrılır, 9600 baud. Uno'da JSON ayrıştırmak RAM yer; bu yüzden düz metin.
Tam liste `atm_terminal.ino` dosyasının başındaki yorumda.

## Bilinen sınırlar

- UID kopyalanabilir (MIFARE Classic UID'si klonlanır). Bu yüzden kart tek başına yetmez, PIN de isteniyor. Gerçek
  güvenlik için imzalı/şifreli kart (NTAG 424 DNA, DESFire) ve PN532 gerekir.
- PIN, Arduino → köprü → API arasında düz metin gider. Hepsi aynı bilgisayarda (USB + localhost) olduğu sürece kabul edilebilir;
  ağdan geçirirsen HTTPS kullan. Seri modda köprü PIN ve token'ları konsola yazmaz.
- Tutar klavyeden tam TL girilir (kuruş yok). Tek terminal hem müşteri hem yetkili modunda çalışır.
- SQLite geliştirme içindir. Varsayılan anahtarlar (`DJANGO_SECRET_KEY`, `KOPRU_API_ANAHTARI`, `LOG_HMAC_ANAHTARI`) sadece demo içindir.

## Fikir sırası

1. Terminal + köprü + backend çalışsın (bu repo).
2. Röle ile kapı kilidi: "kasa dairesi" onaylandığında `UYGULANDI` olunca röleyi çek.
3. Log üzerinde anomali tespiti (imkânsız seyahat, gece davranışı, tekrarlayan başarısız deneme).
