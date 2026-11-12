# VAULTIX: RFID Banka ATM'i ve Çok İmzalı Kasa: proje özeti

**Tek cümle:** Arduino (RC522 kart okuyucu, keypad, LCD, röle) ile çalışan bir mini ATM; sunucu tarafında çift taraflı defter,
N-of-M yetkili onayı, hash zincirli denetim logu, banknot stoğu ve eşzamanlılık güvenceleriyle bir bankanın çekirdek
kurallarını küçük ölçekte gösteren bir simülasyon. Para sanaldır; RC522 gerçek banka kartlarını okuyamaz, yalnızca UID görür.

## Ne yapar

- Müşteri kartını okutur, PIN girer, para çeker / yatırır / havale eder. Küçük işlemler direkt geçer, büyük işlemler
  (tutara ve mesaiye göre) **yetkili onayı** ister; kasa dairesini açmak için 3 yetkili gerekir ve kilit röleyle açılır.
- Para çekerken tutar **eldeki banknotlarla** verilebilmelidir; plan rezerve edilir, sıkışma, eksik verme ve bağlantı kopması
  para kaybı ya da çift ödeme olmadan yönetilir.
- Tarayıcıda çalışan **Web ATM simülatörü** (sanal LCD, tuş takımı, kartlar, canlı şerit) tüm akışı Arduino olmadan gösterir.

## Mimari

{F}
Arduino terminal --(seri, düz metin)--> Python köprüsü --(HTTPS/JSON)--> Django backend --> PostgreSQL
                                                                         \--> panel, admin, web ATM simülatörü
{F}

Terminal **karar vermez**: PIN'ler, bakiyeler ve kurallar yalnızca sunucuda durur. Köprü çevrimdışı kalırsa para işlemi
yapılmaz, yalnızca denetim günlüğü biriktirilir ve bağlantı gelince yüklenir.

## Öne çıkan mühendislik kararları

| Konu | Karar |
|---|---|
| Para doğruluğu | Çift taraflı defter; `gun_sonu_mutabakat` defteri, bakiyeleri, işlemleri, ters kayıtları, kaset rezervlerini ve hash zincirini karşılaştırır. |
| Tekrar isteği | Idempotency anahtarı: ağ kopup istek iki kez gelse bile para bir kez düşer. |
| Denetim | HMAC'li hash zinciri; kayıt değişirse ya da silinirse fark edilir. Zincirin son hash'i dışarı yazılarak kuyruğun silinmesi de yakalanır. |
| Yetki | N-of-M onay, görev ayrımı (RBAC), hassas yönetim işlemlerinde maker-checker (dört göz), onaysız veri değişikliğini yakalayan bütünlük mühürleri. |
| Anomali | Kural tabanlı tarama: PIN yağmuru, imkânsız seyahat, çok terminal, dağıtıcı arızası, düşük banknot stoğu vb. |
| Banknot stoğu | En az banknotlu plan, rezervasyon, sıkışmada tam iade + dağıtıcı kapanır, eksik vermede yalnız fark iade, belirsiz sonuçta otomatik iade **yok** (operatör sayımı). |
| Eşzamanlılık | PostgreSQL; zincir danışma kilidi, hesap ve kaset satır kilitleri, sabit kilit sırası, deadlock'ta yeniden deneme, PIN deneme sayacı kilitli satırdan. |
| PIN gizliliği | PIN yalnızca SMS sağlayıcıya verilir; log, veritabanı, panel ve e-postada görünmez. |

## Nasıl doğrulandı

- **443 Python testi** (52'si operasyon simülasyonu) (PostgreSQL'de çalışan 8 gerçek-thread eşzamanlılık testi dahil: paralel çekimde bakiye, günlük limit,
  banknot stoğu, idempotency ve 3 yanlış PIN sınırı aşılamıyor, zincir bozulmuyor).
- **27 JavaScript akış testi** (simülatörün ekran akışı, sahte sunucuya karşı, Windows zamanlayıcısı taklit edilerek de).
- Banknot bölme algoritması, rastgele stok ve tutarlarda kaba kuvvet çözümüyle karşılaştırıldı.

## Dürüst sınırlar

- UID kopyalanabilir (MIFARE Classic); gerçek güvenlik için imzalı kart (NTAG 424 DNA, DESFire) gerekir.
- Para dağıtıcı donanımı yoktur; sıkışma ve eksik verme simülatörde oynatılır, sunucu terminalin bildirimine güvenir ve
  bunu kaset sayımına (mutabakat) bırakır.
- Hash zinciri tek sıralı olduğu için yazan işlemler sıraya girer: doğruluk için bilinçli bir takas, yatay ölçeklemeyi sınırlar.
- Simülatörün operatör düğmeleri ve `nakit_coz --dogrudan` tek kişilik geliştirme yoludur; gerçek yol dört gözlü `NAKIT_COZ` talebidir.
- Yük testi yapılmadı; eşzamanlılık testleri küçük ölçekli yarış testleridir.

## Çalıştırma

{F}bat
docker compose up -d                 :: PostgreSQL
cd backend
pip install -r requirements.txt
python manage.py migrate && python manage.py seed_demo && python manage.py createsuperuser
python manage.py runserver           :: sonra /admin/ ile giriş, ardından /simulator/
{F}

Ayrıntılar ve tüm kurallar için [README](../README.md).

## Özgeçmiş / portfolyo için kısa metin

**Türkçe**

- Arduino + Django ile kartlı ATM ve çok imzalı kasa simülasyonu geliştirdim: çift taraflı defter, idempotency, N-of-M onay,
  HMAC'li hash zincirli denetim logu, maker-checker ve RBAC.
- Nakit çekimde banknot stoğu, sıkışma/eksik verme/bağlantı kopması yönetimi ve PostgreSQL üzerinde eşzamanlılık güvenceleri
  (satır ve danışma kilitleri, idempotency, deadlock'ta yeniden deneme) tasarlayıp gerçek thread'li testlerle doğruladım.
- Panelden başlatılan banka operasyon simülasyonu: sanal müşterilerle yük üretir, ATM yoğunluğunu, kuyrukları, başarısız işlemleri ve onay gecikmelerini ölçer (ayrı sandbox veritabanı, Procrastinate görevleri, Centrifugo canlı akışı).
- 443 Python ve 27 JavaScript testi; tarayıcıda çalışan, hata enjeksiyonlu Web ATM simülatörü.

**English**

- Built a card-based ATM and multi-signature vault simulation with Arduino and Django: double-entry ledger, idempotency,
  N-of-M approvals, an HMAC hash-chained audit log, maker-checker and RBAC.
- Designed banknote inventory handling for cash withdrawals, including safe recovery from jams, short dispenses and lost
  connections, and PostgreSQL concurrency guarantees (row and advisory locks, idempotency, deadlock retry), verified with real-thread tests.
- Added a bank operations simulation started from the admin panel: virtual customers generate load while the system measures ATM load, queues, failed transactions and approval delays (isolated sandbox database, Procrastinate tasks, live Centrifugo stream).
- 443 Python and 27 JavaScript tests; a browser-based ATM simulator with fault injection.
