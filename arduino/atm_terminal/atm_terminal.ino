/*
  RFID Banka + Cok Imzali Kasa - Terminal (Arduino)

  Bu kod HICBIR karar vermez. Kart UID'sini ve tuslanan degerleri seri porttan
  Python koprusune yollar, cevaba gore ekrana yazar. PIN dogrulama, bakiye,
  politika ve onay kurallari sunucudadir (backend/).

  Seri protokol (satir satir, alanlar '|' ile ayrilir, 9600 baud):

    Arduino -> PC                       PC -> Arduino
    KART|uid                            TIP|MUSTERI | PERSONEL | BLOKE | BILINMIYOR
    PIN|uid|pin                         GIRIS|token|ad        veya HATA|mesaj
    BAKIYE|token                        BAKIYE|1234.50
    ISLEM|token|C/Y/H|tutar|hedef|anahtar  TAMAM|yeniBakiye | BEKLE|talepId|gereken | HATA|mesaj
    KASAAC|uid|pin                      BEKLE|talepId|gereken | HATA|mesaj   (kasa dairesi acma talebi)
    BEKLEYEN                            BEKLEYEN|YOK | BEKLEYEN|id|tur|tutar|verilen|gereken
    DURUM|talepId                       DURUM|verilen|gereken|durum|kalanSn|ustYetkili(0/1)|tur
    ONAY|talepId|uid|pin|E/H            ONAY|verilen|gereken|durum | HATA|mesaj

  Pinler (Uno):
    RC522 : SDA/SS=10, RST=9, MOSI=11, MISO=12, SCK=13, 3.3V (5V DEGIL!)
    Keypad: satirlar 2,3,4,5  sutunlar 6,7,8,A0
    LCD   : I2C  SDA=A4, SCL=A5  (adres 0x27, calismazsa 0x3F dene)
    Buzzer: A1      Para LED'i: A2      Kasa kilidi roleisi (IN): A3

  Idempotency: ISLEM'in son alani rastgele bir anahtardir. Cevap gelmezse AYNI anahtarla
  yeniden denenir; sunucu ayni islemi ikinci kez uygulamaz.

  Kutuphaneler (Library Manager): MFRC522, Keypad, LiquidCrystal I2C
*/

#include <SPI.h>
#include <MFRC522.h>
#include <Keypad.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>

#define RST_PIN 9
#define SS_PIN 10
#define BUZZER_PIN A1
#define PARA_LED_PIN A2
#define ROLE_PIN A3        // kasa kilidi roleisi (cogu modulde LOW = role ceker)
#define ROLE_ACIK LOW
#define ROLE_KAPALI HIGH
#define KILIT_ACIK_MS 5000UL

// Metinleri flash'ta tutar (Uno'nun 2KB RAM'i icin onemli)
#define S(x) String(F(x))

MFRC522 mfrc522(SS_PIN, RST_PIN);
LiquidCrystal_I2C lcd(0x27, 16, 2);

const byte SATIR = 4;
const byte SUTUN = 4;
char tuslar[SATIR][SUTUN] = {
  {'1', '2', '3', 'A'},
  {'4', '5', '6', 'B'},
  {'7', '8', '9', 'C'},
  {'*', '0', '#', 'D'}
};
byte satirPin[SATIR] = {2, 3, 4, 5};
byte sutunPin[SUTUN] = {6, 7, 8, A0};
Keypad keypad = Keypad(makeKeymap(tuslar), satirPin, sutunPin, SATIR, SUTUN);

bool bostaEkran = false;

// ---------------------------------------------------------------- ekran / ses

void satirYaz(byte satir, String s) {
  while (s.length() < 16) s += ' ';
  lcd.setCursor(0, satir);
  lcd.print(s.substring(0, 16));
}

void ekran(String a, String b = String()) {
  satirYaz(0, a);
  satirYaz(1, b);
}

void bip(bool basarili) {
  if (basarili) {
    tone(BUZZER_PIN, 1800, 120);
  } else {
    tone(BUZZER_PIN, 400, 400);
  }
}

void paraCikis() {
  // Para verildi simulasyonu: LED 2 sn yanar
  digitalWrite(PARA_LED_PIN, HIGH);
  delay(2000);
  digitalWrite(PARA_LED_PIN, LOW);
}

// Kasa dairesi kilidini role ile birkac saniye acar
void kilitAc() {
  ekran(S("Kasa acildi"), S("5 sn acik"));
  bip(true);
  digitalWrite(ROLE_PIN, ROLE_ACIK);
  delay(KILIT_ACIK_MS);
  digitalWrite(ROLE_PIN, ROLE_KAPALI);
}

// ---------------------------------------------------------------- seri protokol

String komut(const String &satir, unsigned long beklemeMs = 4000) {
  while (Serial.available()) Serial.read();  // eski cevaplari temizle
  Serial.println(satir);
  Serial.setTimeout(beklemeMs);
  String cevap = Serial.readStringUntil('\n');
  cevap.trim();
  if (cevap.length() == 0) return S("HATA|Sunucu yanit vermedi");
  return cevap;
}

// "A|B|C" icinden n. alani (0'dan baslar) dondurur
String alan(const String &s, byte n) {
  int bas = 0;
  for (byte i = 0; i < n; i++) {
    bas = s.indexOf('|', bas);
    if (bas < 0) return String();
    bas++;
  }
  int son = s.indexOf('|', bas);
  if (son < 0) son = s.length();
  return s.substring(bas, son);
}

void hataGoster(const String &cevap) {
  String m = alan(cevap, 1);
  if (m.length() == 0) m = cevap;
  ekran(m.substring(0, 16), m.substring(16));
  bip(false);
  delay(2500);
}

// ---------------------------------------------------------------- kart / tus

String uidYazi() {
  String s = "";
  for (byte i = 0; i < mfrc522.uid.size; i++) {
    if (mfrc522.uid.uidByte[i] < 0x10) s += "0";
    s += String(mfrc522.uid.uidByte[i], HEX);
  }
  return s;  // kucuk harfli, sifir dolgulu
}

bool kartOku(String &uid) {
  if (!mfrc522.PICC_IsNewCardPresent()) return false;
  if (!mfrc522.PICC_ReadCardSerial()) return false;
  uid = uidYazi();
  mfrc522.PICC_HaltA();
  return true;
}

// 4 haneli PIN alir. Bos string: iptal ya da zaman asimi.
String pinAl(const String &baslik) {
  String pin = "";
  String yildiz = "";
  ekran(baslik, S("PIN: "));
  unsigned long bas = millis();
  while (pin.length() < 4) {
    if (millis() - bas > 20000UL) return String();
    char t = keypad.getKey();
    if (!t) continue;
    if (t >= '0' && t <= '9') {
      pin += t;
      yildiz += '*';
      satirYaz(1, S("PIN: ") + yildiz);
    } else if (t == '*') {
      return String();
    }
  }
  return pin;
}

// Rakam alir. #: onay, *: iptal, D: geri sil. Bos string: iptal.
String sayiAl(const String &baslik, byte maksUzunluk) {
  String s = "";
  ekran(baslik, "");
  unsigned long bas = millis();
  while (true) {
    if (millis() - bas > 30000UL) return String();
    char t = keypad.getKey();
    if (!t) continue;
    bas = millis();
    if (t >= '0' && t <= '9') {
      if (s.length() < maksUzunluk) s += t;
    } else if (t == '#') {
      if (s.length() > 0) return s;
    } else if (t == '*') {
      return String();
    } else if (t == 'D') {
      if (s.length() > 0) s.remove(s.length() - 1);
    }
    satirYaz(1, s);
  }
}

// ---------------------------------------------------------------- yetkili (personel)

void personelKarar(const String &talepId, const String &uid) {
  String c = komut(S("KART|") + uid);
  if (alan(c, 1) != S("PERSONEL")) {
    ekran(S("Yetkili karti"), S("degil"));
    bip(false);
    delay(1200);
    return;
  }

  ekran(S("A:Onayla B:Red"), S("*:Iptal"));
  char t = 0;
  unsigned long bas = millis();
  while (millis() - bas < 10000UL) {
    t = keypad.getKey();
    if (t == 'A' || t == 'B' || t == '*') break;
    t = 0;
  }
  if (t != 'A' && t != 'B') return;

  String pin = pinAl(S("Yetkili PIN:"));
  if (pin.length() < 4) return;

  ekran(S("Kontrol ediliyor"));
  String r = komut(S("ONAY|") + talepId + "|" + uid + "|" + pin + "|" + (t == 'A' ? "E" : "H"), 5000);
  pin = "";

  if (alan(r, 0) == S("ONAY")) {
    ekran(S("Oyunuz alindi"), alan(r, 1) + "/" + alan(r, 2));
    bip(true);
    delay(1200);
  } else {
    hataGoster(r);
  }
}

void onayBekle(const String &talepId, const String &etiket);  // asagida tanimli

// Bekleyen talep yokken yetkili, kasa dairesini acmak icin onay talebi baslatabilir
void kasaAcmaTalebi(const String &uid) {
  ekran(S("Talep yok"), S("A:Kasa ac *:Cik"));
  char t = 0;
  unsigned long bas = millis();
  while (millis() - bas < 8000UL) {
    t = keypad.getKey();
    if (t == 'A' || t == '*') break;
    t = 0;
  }
  if (t != 'A') return;

  String pin = pinAl(S("Yetkili PIN:"));
  if (pin.length() < 4) return;

  ekran(S("Kontrol ediliyor"));
  String c = komut(S("KASAAC|") + uid + "|" + pin, 5000);
  pin = "";

  if (alan(c, 0) == S("BEKLE")) {
    onayBekle(alan(c, 1), S("Kasa acma"));
  } else {
    hataGoster(c);
  }
}

// Bos moddayken yetkili karti okutulursa bekleyen talebi gosterir
void personelModu(const String &uid) {
  String c = komut(S("BEKLEYEN"));
  if (alan(c, 0) != S("BEKLEYEN")) {
    hataGoster(c);
    return;
  }
  if (alan(c, 1) == S("YOK")) {
    kasaAcmaTalebi(uid);
    return;
  }
  String talepId = alan(c, 1);
  ekran(S("Talep #") + talepId, alan(c, 2) + " " + alan(c, 3));
  delay(2000);
  personelKarar(talepId, uid);
}

// ---------------------------------------------------------------- musteri

// Onay bekler. Sonuc ekranda gosterilir.
void onayBekle(const String &talepId, const String &etiket) {
  ekran(S("Onay gerekli"), etiket);
  delay(1500);
  unsigned long sonSorgu = 0;
  String uid;
  while (true) {
    if (millis() - sonSorgu > 1000UL) {
      sonSorgu = millis();
      String c = komut(S("DURUM|") + talepId);
      if (alan(c, 0) != S("DURUM")) {
        hataGoster(c);
        return;
      }
      String verilen = alan(c, 1);
      String gereken = alan(c, 2);
      String durum = alan(c, 3);
      String kalan = alan(c, 4);
      bool ust = (alan(c, 5) == S("1"));

      if (durum == S("UYGULANDI")) {
        ekran(S("Islem onaylandi"), S("Tamamlandi"));
        bip(true);
        if (alan(c, 6) == S("KASA_ACMA")) {
          kilitAc();
        } else {
          paraCikis();
        }
        delay(1500);
        return;
      }
      if (durum == S("REDDEDILDI")) {
        ekran(S("Islem reddedildi"));
        bip(false);
        delay(2500);
        return;
      }
      if (durum == S("SURESI_DOLDU")) {
        ekran(S("Sure doldu"), S("Islem iptal"));
        bip(false);
        delay(2500);
        return;
      }
      if (durum == S("BASARISIZ")) {
        ekran(S("Islem basarisiz"), S("Subeye basvurun"));
        bip(false);
        delay(2500);
        return;
      }
      satirYaz(0, S("Onay ") + verilen + "/" + gereken + " " + kalan + "sn");
      satirYaz(1, ust ? S("Ust rol gerekli") : S("Yetkili kart oku"));
    }
    if (kartOku(uid)) {
      personelKarar(talepId, uid);
      sonSorgu = 0;  // ekrani hemen guncelle
    }
  }
}

void bakiyeGoster(const String &token) {
  String c = komut(S("BAKIYE|") + token);
  if (alan(c, 0) == S("BAKIYE")) {
    ekran(S("Bakiye:"), alan(c, 1) + " TL");
    bip(true);
    delay(3500);
  } else {
    hataGoster(c);
  }
}

// tur: 'C' cekme, 'Y' yatirma, 'H' havale
void islemYap(const String &token, char tur) {
  String hedef = "";
  if (tur == 'H') {
    hedef = sayiAl(S("Hesap no(6hane)"), 6);
    if (hedef.length() != 6) {
      ekran(S("Iptal edildi"));
      delay(1000);
      return;
    }
  }
  String tutar = sayiAl(S("Tutar TL (#:ok)"), 8);
  if (tutar.length() == 0) {
    ekran(S("Iptal edildi"));
    delay(1000);
    return;
  }

  ekran(S("Isleniyor..."));
  // Idempotency anahtari: ayni istek tekrarlanirsa AYNI anahtar gider, para iki kez cekilmez
  String anahtar = String(millis(), HEX) + String(random(0xFFFF), HEX);
  String satir = S("ISLEM|") + token + "|" + String(tur) + "|" + tutar + "|" + hedef + "|" + anahtar;
  String c = komut(satir, 6000);
  for (byte deneme = 0; deneme < 2 && c.startsWith(S("HATA|Sunucu")); deneme++) {
    ekran(S("Tekrar deneniyor"));
    c = komut(satir, 6000);
  }
  String a0 = alan(c, 0);

  if (a0 == S("TAMAM")) {
    ekran(S("Islem tamam"), S("Bak: ") + alan(c, 1));
    bip(true);
    if (tur == 'C') paraCikis();
    delay(3000);
  } else if (a0 == S("BEKLE")) {
    onayBekle(alan(c, 1), tutar + " TL");
  } else {
    hataGoster(c);
  }
}

char menuSec() {
  ekran(S("A:Bakiye B:Cek"), S("C:Yatir D:Havale"));
  unsigned long bas = millis();
  while (millis() - bas < 30000UL) {
    char t = keypad.getKey();
    if (t == 'A' || t == 'B' || t == 'C' || t == 'D' || t == '*') return t;
  }
  return '*';  // zaman asimi: cikis
}

void musteriOturumu(const String &uid) {
  String pin = pinAl(S("PIN girin:"));
  if (pin.length() < 4) {
    ekran(S("Iptal edildi"));
    delay(1000);
    return;
  }
  ekran(S("Kontrol ediliyor"));
  String c = komut(S("PIN|") + uid + "|" + pin);
  pin = "";

  if (alan(c, 0) != S("GIRIS")) {
    hataGoster(c);
    return;
  }
  String token = alan(c, 1);
  ekran(S("Merhaba"), alan(c, 2));
  bip(true);
  delay(1200);

  while (true) {
    char sec = menuSec();
    if (sec == '*') break;
    if (sec == 'A') bakiyeGoster(token);
    if (sec == 'B') islemYap(token, 'C');
    if (sec == 'C') islemYap(token, 'Y');
    if (sec == 'D') islemYap(token, 'H');
  }
  token = "";
  ekran(S("Cikis yapildi"));
  delay(1000);
}

// ---------------------------------------------------------------- ana program

void setup() {
  randomSeed(analogRead(A3));            // bos pin okumasi: rastgele tohum (idempotency anahtari icin)
  digitalWrite(ROLE_PIN, ROLE_KAPALI);   // cikis yapmadan once kapali seviyeyi hazirla (role tikirdamasin)
  pinMode(ROLE_PIN, OUTPUT);
  Serial.begin(9600);
  SPI.begin();
  mfrc522.PCD_Init();
  lcd.init();
  lcd.backlight();
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(PARA_LED_PIN, OUTPUT);
  digitalWrite(PARA_LED_PIN, LOW);
  ekran(S("Baslatiliyor..."));
  delay(1000);
}

void loop() {
  if (!bostaEkran) {
    ekran(S("Karti okutun"), S("Mini ATM / Kasa"));
    bostaEkran = true;
  }

  String uid;
  if (!kartOku(uid)) return;
  bostaEkran = false;

  ekran(S("Kart okundu"), S("Bekleyin..."));
  String c = komut(S("KART|") + uid);

  if (alan(c, 0) != S("TIP")) {
    hataGoster(c);
    return;
  }
  String tip = alan(c, 1);
  if (tip == S("MUSTERI")) {
    musteriOturumu(uid);
  } else if (tip == S("PERSONEL")) {
    personelModu(uid);
  } else if (tip == S("BLOKE")) {
    ekran(S("Kart bloke"), S("Subeye basvurun"));
    bip(false);
    delay(2500);
  } else {
    ekran(S("Kart taninmadi"));
    bip(false);
    delay(1800);
  }
}
