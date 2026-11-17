# Donanım hazırlık kontrol listesi

Donanım henüz alınmadı. Bu liste, parçalar gelince **neyin sırayla, neyle doğrulanacağını** baştan belirler; böylece bir şey çalışmazsa
sorunun parçada mı, kablolamada mı, firmware'de mi, köprüde mi olduğu hızlı ayrılır. Pin haritası, röle şeması ve protokol için
README'deki "Bağlantılar (Uno)" ve "Protokol" bölümlerine bak; burada tekrar etmiyorum.

## 1. Almadan önce

| Parça | Not |
|---|---|
| Arduino Uno | Klonlarda USB çipi genelde CH340'tır; Windows'ta sürücü gerekebilir. Port adı `COM3` gibi görünür |
| RC522 modülü + MIFARE Classic 1K kart/anahtarlık | Modül **3,3 V** ile beslenir (5 V'a bağlama). Uno'nun 5 V'luk MOSI/SCK/SDA/RST çıkışları modülün 3,3 V'luk girişlerine doğrudan bağlanır; yaygın uygulama budur ama kalıcı kullanımda 1 kΩ/2 kΩ bölücü ya da seviye çevirici daha güvenlidir |
| 4x4 membran keypad | Satır/sütun sırası ters takılırsa tuşlar yanlış çıkar (bkz. adım 3) |
| 16x2 I2C LCD (PCF8574) | Adres `0x27` ya da `0x3F`; arka yüzdeki kontrast trimpotu yanlışsa ekran boş görünür |
| Buzzer (aktif), LED + 220 Ω | Buzzer A1, "para çıkışı" LED'i A2 |
| 5 V röle modülü | `IN` → A3; çoğu modül LOW'da çeker (`ROLE_ACIK`/`ROLE_KAPALI` ile ters çevrilir) |
| 12 V adaptör + elektromanyetik/solenoid kilit, **1N4007 diyot** | Kilit ayrı adaptörden beslenir; diyot kilidin uçlarına ters yönde. İlk denemede kilit yerine LED/röle "klik" sesi kullan |
| Breadboard, jumper kablo, USB-A/B kablo | |

Karar bekleyen: kart olarak yalnızca RC522'nin okuyabildiği MIFARE Classic yeterli. **İmzalı kart** (NTAG 424 DNA) için PN532 gerekir; RC522 ile imza üretilemez
(README "İmzalı kart"). İlk bring-up'ta imzasız kartla ilerlenir.

## 2. Masa başı hazırlık (donanım gelmeden yapılabilir)

- [ ] Arduino IDE'de kütüphaneler: **MFRC522**, **Keypad**, **LiquidCrystal I2C** (README'deki adlarla)
- [ ] `atm_terminal.ino` IDE'de derleniyor mu? Beklenen: flash ≈ 23 KB (%70,7), RAM ≈ 1,2 KB (%60,8) (ölçüm `arduino/araclar/avr_derle.sh` ile, avr-gcc 7.3)
- [ ] Köprü bilgisayarında Python + `pip install -r bridge/requirements.txt` (pyserial dahil)
- [ ] Sunucu ayakta: `docker compose up -d`, `python manage.py migrate`, `seed_demo`, `runserver`
- [ ] Seri portu aynı anda yalnızca bir program açar: **Arduino IDE Serial Monitor'ü köprüyü başlatmadan önce kapat**
- [ ] Köprü `--konsol` modunda sunucuyla konuşuyor (donanımsız referans; bkz. README "Arduino olmadan deneme")

## 3. Aşamalı bring-up (her adım bir öncekine dayanır)

Hepsini tek seferde bağlama. Her adımda yalnızca o parçanın küçük bir deneme taslağı yüklenir; **gerçek firmware en son** yüklenir.

| # | Adım | Nasıl | Beklenen | Olmazsa |
|---|---|---|---|---|
| 0 | Güç öncesi kontrol | Kabloları pin haritasıyla karşılaştır; RC522 VCC 3,3 V mu; röle/12 V henüz bağlı **değil** | Kısa devre yok | USB takmadan düzelt |
| 1 | LCD | I2C tarayıcı taslağı, sonra "Merhaba" | Adres `0x27` ya da `0x3F` görünür, metin okunur | Trimpotu çevir; adres `0x3F` ise `atm_terminal.ino`'daki `LiquidCrystal_I2C lcd(0x27,…)` değerini değiştir; SDA=A4, SCL=A5 |
| 2 | Keypad | Basılan tuşu seri monitöre yazan taslak | 16 tuşun hepsi doğru çıkar | Satır (2,3,4,5) ve sütun (6,7,8,A0) sırasını kontrol et |
| 3 | RC522 | MFRC522 kütüphanesinin `DumpInfo` örneği | Sürüm `0x91` ya da `0x92`; kartta UID görünür | Sürüm `0x00`/`0xFF` ise kablolama ya da 3,3 V; SS=10, RST=9 |
| 4 | Buzzer, LED | `tone`/`digitalWrite` denemesi | Ses duyulur, LED yanar | Polarite; buzzer aktif mi |
| 5 | Röle (yüksüz) | A3'ü 1 sn LOW/HIGH yap | Rölenin klik sesi, modül LED'i | Çekme seviyesi ters: `ROLE_ACIK`/`ROLE_KAPALI` yer değiştir |
| 6 | Gerçek firmware, köprüsüz | `atm_terminal.ino` yükle, seri monitör 9600 | LCD bekleme ekranı; monitörde `SS:HELLO\|2*CCCC` benzeri çerçeveler; LCD'de "Kopru yok!" | Beklenen davranış: köprü yok. Sürekli yeniden başlıyorsa güç/kablo (WDT 2 sn) |
| 7 | Köprü + sunucu | Monitörü kapat; `python serial_bridge.py --port COM3 --terminal ATM-01` | "Arduino'nun açılması bekleniyor" (port açılınca Uno resetlenir, ≈ 2 sn), sonra "Kopru yok!" kaybolur | Port meşgul/yanlış COM; `--terminal` kodu sunucuda tanımlı mı (`ATM-01`) |
| 8 | Kart tanıma | Kartı okut | Köprü `KART\|uid -> TIP` yazar; kart yeni ise `BILINMIYOR` | UID'yi admin panelinden bir müşteriye ata (README "Müşteri kartları") |
| 9 | Müşteri akışı | Kart → PIN → bakiye → çekme | Tutar doğru düşer, "Para çıkışı" LED'i yanar | Sunucu günlüğü ve köprü satırları (CRC/NAK sayacı) |
| 10 | Kasa açma | Müdür/müdür yardımcısı kartıyla `A:Kasa ac`, ardından 3 onay (en az biri müdür; başlatan kendi talebini onaylayamaz); önce LED, sonra röle (yüksüz), en son kilit + 12 V | Son onayda röle **tam 5 sn** açık kalır, sonra kapanır | Röle yalnızca `D_KILIT` durumunda açılır; yeterli başka yetkili yoksa talep hiç açılmaz (beklenen, README "Kasa dairesini açma") |

## 4. Uçtan uca kabul listesi

- [ ] Normal çekme, bakiye sorgu, havale (yeni alıcı) başarılı
- [ ] Üç yanlış PIN kartı kilitler
- [ ] USB'yi çek → Command Center'da terminal **çevrimdışı** olur (varsayılan `TERMINAL_CEVRIMDISI_SN=45`), bir **arıza vakası** açılır ve görevliye atanır; USB'yi tak → vaka kendiliğinden **çözüldü**
- [ ] Köprü çalışırken sunucuyu durdur → terminal sunucu hatası gösterir (`HATA|Sunucu…`, terminal en çok 2 kez yeniden dener), para çıkmaz; sunucu dönünce aynı idempotency anahtarıyla tekrar denendiğinde işlem ya yeni uygulanır ya da önceki sonuç döner, hiçbir durumda iki kez çıkmaz
- [ ] Terminalin gücünü çekip tak (işlem ortasında) → açılış sayacı sayesinde eski idempotency anahtarı yeniden üretilmez
- [ ] Gürültü: uzun/gevşek kablo ya da USB hub ile CRC hataları → köprü `NAK|CRC` sayar, terminal yeniden dener, para iki kez çıkmaz
- [ ] ML gölge modu açıkken (`ML_RISK_MODU=golge`) gerçek işlemlerin `ml_puan` değerleri doluyor (kural kararı değişmiyor)

## 5. Donanım gününde ölçülecekler (kayda geçir)

1. **Yığın (stack) payı.** Statik RAM ≈ 1,2 KB / 2 KB olduğundan yığına ≈ 800 B kalır; en kötü derinlik ölçülmedi. Yöntem (**taslak, derlenmedi/denenmedi**): açılışta
   `__heap_start` ile `SP-32` arası `0xAA` ile boyanır, sonra en çok işlem yapılan akışlardan (PIN, onay beklemesi, uzun cevap ayrıştırma) sonra `__heap_start`'tan itibaren
   bozulmamış bayt sayısı okunur (en yüksek su çizgisi). 100 B'ın altında pay kalırsa tamponları küçült ya da Mega'ya geç.
2. **Gerçek gecikmeler:** kart okutma → LCD'de sonuç; çekme cevabı (köprü + sunucu dahil). PC benzetimindekiyle karşılaştır.
3. **Hat kalitesi:** 1 saatlik boşta bırakma sonunda köprüdeki `nabiz_hatasi`, NAK ve yeniden iletim sayısı.
4. **Röle/kilit:** kilit 5 sn açık kalıyor mu (`KILIT_ACIK_MS`); açılışta rölenin tıkırdamaması.
5. **Güç kesintisi:** 20 kez ardışık güç kesme/verme sonrası açılış sayacının tutarlı ilerlemesi (EEPROM halkası).

## 6. Bilinen donanım riskleri (kabul edilmiş)

- UID klonlanabilir (MIFARE Classic); PIN bu yüzden zorunlu. Gerçek güvenlik için PN532 + imzalı kart.
- Arduino ↔ köprü arası USB'de PIN düz metindir (aynı bilgisayar varsayımı).
- Firmware gerçek donanımda **hiç çalıştırılmadı**; bu liste ilk doğrulamanın kendisidir. Beklenmedik her sapma README'ye ve ilgili teste geri yazılmalı.
- Röle modülleri ve I2C LCD'ler üreticiye göre farklı davranır (çekme seviyesi, adres); bu yüzden ilk denemede kilit yerine LED kullanılır.
