/*
  VAULTIX - RFID Banka + Cok Imzali Kasa - Terminal (Arduino Uno)

  Bu kod HICBIR karar vermez. Kart UID'sini ve tuslanan degerleri seri porttan
  Python koprusune yollar, cevaba gore ekrana yazar. PIN dogrulama, bakiye,
  politika ve onay kurallari sunucudadir (backend/).

  Yazilim mimarisi (bu dosya yalnizca ince bir "donanim sarmalayicisi"dir):
    donanim.h   : donanim arayuzu (HAL)       terminal.*  : bloklamayan durum makinesi (FSM)
    baglanti.*  : zaman asimi + yeniden iletim cerceve.* : CRC-16 cerceveleme (kopru ile ayni)
    metin.*     : String'siz, tasmaya dayanikli metin yardimcilari
  Cekirdek Arduino'dan bagimsizdir; PC'de test edilir: arduino/test/ (make -C arduino/test).

  HAT CERCEVESI (protokol v2, bridge/cerceve.py ile ayni):
    SS:yuk*CCCC\n     SS = sira no (2 hane onaltilik)   CCCC = "SS:yuk" icin CRC-16/CCITT-FALSE
    Terminal acilista "HELLO|2" yollar, kopru "HELLO|OK|2" ile surumu dogrular. Cevap yoksa ya da bozuksa ayni istek
    ayni sira no ile yeniden yollanir; kopru tekrar gelen istegi ISLEMEZ, onbellekten cevaplar (para iki kez cekilmez).
    Bos modda her 10 sn'de PING; cevap yoksa ekranda "Kopru yok!" yazar.

  Seri protokol - YUK (alanlar '|' ile ayrilir, 9600 baud). Hatta CERCEVE icinde gider, asagiya bak:

    Arduino -> PC                       PC -> Arduino
    KART|uid                            TIP|MUSTERI | PERSONEL | BLOKE | BILINMIYOR | IMZASIZ
                                        (IMZASIZ: imzali kart simulasyonunda klon/tekrar; bu firmware imza uretmez,
                                         RC522 NTAG 424 DNA'nin AES'ini yapamaz. Simulator KART|uid|sayac|mac yollar)
    PIN|uid|pin                         GIRIS|token|ad        veya HATA|mesaj
    PINDEGIS|token|eskiPin|yeniPin      PINDEGIS|TAMAM        (menude #: musteri kendi PIN'ini degistirir)
    BAKIYE|token                        BAKIYE|1234.50
    ISLEM|token|C/Y/H|tutar|hedef|anahtar  TAMAM|yeniBakiye|islemId|plan | BEKLE|talepId|gereken | HATA|mesaj   (plan: nakit cekmede banknotlar, orn. 200x8; bu firmware plani kullanmaz)
    KASAAC|uid|pin                      BEKLE|talepId|gereken | HATA|mesaj   (kasa dairesi acma talebi)
    BEKLEYEN                            BEKLEYEN|YOK | BEKLEYEN|id|tur|tutar|verilen|gereken
    DURUM|talepId                       DURUM|verilen|gereken|durum|kalanSn|ustYetkili(0/1)|tur|islemId|plan
    TESLIM|islemId|A/H                  TESLIM|ALINDI|iade | TESLIM|GERI|iade   (para teslimi; H = alinmadi, ters kayit). Sunucu ayrica S (sikisti) ve K|banknotlar (eksik verildi) kabul eder; Uno'da dagitici olmadigi icin bu firmware yalnizca A/H yollar
    ONAY|talepId|uid|pin|E/H            ONAY|verilen|gereken|durum | HATA|mesaj

  Pinler (Uno):
    RC522 : SDA/SS=10, RST=9, MOSI=11, MISO=12, SCK=13, 3.3V (5V DEGIL!)
    Keypad: satirlar 2,3,4,5  sutunlar 6,7,8,A0
    LCD   : I2C  SDA=A4, SCL=A5  (adres 0x27, calismazsa 0x3F dene)
    Buzzer: A1      Para LED'i: A2      Kasa kilidi roleisi (IN): A3

  Para teslimi: nakit cekmede LED yanar, A tusu "para alindi" sensorunu simule eder. 8 sn icinde
  basilmazsa para geri cekilmis sayilir ve sunucu islemi TERS KAYDEDER (gercek ATM'de bunu dagitici
  sensoru bildirir). Sunucu cevap vermezse ayni bildirim tekrar denenir.

  Tutar kurus icerebilir: klavyede C tusu ondalik noktasi koyar (ornek 100.50).

  Idempotency: ISLEM'in son alani rastgele bir anahtardir. Cevap gelmezse AYNI anahtarla
  yeniden denenir; sunucu ayni islemi ikinci kez uygulamaz.

  Guvenlik / saglamlik:
    * Kasa roleisi YALNIZCA kasa acma talebi UYGULANDI olunca 5 sn acilir; baska hicbir durumda acilamaz (her turda denetlenir).
    * PIN ve oturum jetonu kullanilir kullanilmaz RAM'den silinir. Hicbir yerde String/delay/bloklayan okuma yok.
    * Watchdog (BEKCI_ETKIN): donma olursa 2 sn'de reset; role reset sirasinda kapali kalir.
      Eski/klon kartlardaki bootloader watchdog resetinde dongüye girebilir: sorun olursa BEKCI_ETKIN 0 yapin.

  Kutuphaneler (Library Manager): MFRC522, Keypad, LiquidCrystal I2C
*/

#include <SPI.h>
#include <MFRC522.h>
#include <Keypad.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <EEPROM.h>

#include "terminal.h"

// Watchdog: Uno (optiboot) icin acik. Eski bootloader'li kartlarda sorun cikarirsa 0 yapin.
#ifndef BEKCI_ETKIN
#if defined(ARDUINO_AVR_UNO)
#define BEKCI_ETKIN 1
#else
#define BEKCI_ETKIN 0
#endif
#endif

#if BEKCI_ETKIN
#include <avr/wdt.h>
// Watchdog resetinden sonra bekcinin hemen kapatilmasi gerekir (yoksa reset dongusu); reset nedeni saklanir.
uint8_t mcusr_kopya __attribute__((section(".noinit")));
void bekci_ilk_kapat(void) __attribute__((naked, used, section(".init3")));
void bekci_ilk_kapat(void) {
  mcusr_kopya = MCUSR;
  MCUSR = 0;
  wdt_disable();
}
#else
static uint8_t mcusr_kopya = 0;
#endif

#define RST_PIN 9
#define SS_PIN 10
#define BUZZER_PIN A1
#define PARA_LED_PIN A2
#define ROLE_PIN A3        // kasa kilidi roleisi (cogu modulde LOW = role ceker)
#define ROLE_ACIK LOW
#define ROLE_KAPALI HIGH

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

// Gercek donanim: cekirdegin gordugu tek sey bu sinifin arayuzudur.
class ArduinoDonanim : public vx::Donanim {
 public:
  uint32_t simdi_ms() { return millis(); }
  char tus() { return keypad.getKey(); }

  bool kart_oku(char *uid_hex, size_t kap) {
    if (!mfrc522.PICC_IsNewCardPresent()) return false;
    if (!mfrc522.PICC_ReadCardSerial()) return false;
    size_t n = 0;
    for (byte i = 0; i < mfrc522.uid.size && n + 2 < kap; i++) {
      byte b = mfrc522.uid.uidByte[i];
      uid_hex[n++] = "0123456789abcdef"[b >> 4];
      uid_hex[n++] = "0123456789abcdef"[b & 0xF];
    }
    uid_hex[n] = 0;
    mfrc522.PICC_HaltA();
    return true;
  }

  void lcd_satir(uint8_t satir, const char *metin16) {
    lcd.setCursor(0, satir);
    lcd.print(metin16);
  }

  void bip(bool basarili) {
    if (basarili) tone(BUZZER_PIN, 1800, 120);
    else tone(BUZZER_PIN, 400, 400);
  }

  void role(bool ac) { digitalWrite(ROLE_PIN, ac ? ROLE_ACIK : ROLE_KAPALI); }
  void para_led(bool yak) { digitalWrite(PARA_LED_PIN, yak ? HIGH : LOW); }

  int seri_oku() { return Serial.read(); }
  int seri_yazilabilir() { return Serial.availableForWrite(); }
  void seri_yaz(const char *veri, size_t n) { Serial.write(reinterpret_cast<const uint8_t *>(veri), n); }

  uint16_t rastgele() { return (uint16_t)random(65536L); }

  void bekci_besle() {
#if BEKCI_ETKIN
    wdt_reset();
#endif
  }
  // Kalici bellek: 16 yuva x 2 bayt (EEPROM 0..31). put() yalnizca degisen baytlari yazar.
  uint16_t kalici_oku(uint8_t yuva) {
    uint16_t v;
    EEPROM.get(yuva * 2, v);
    return v;
  }
  void kalici_yaz(uint8_t yuva, uint16_t deger) { EEPROM.put(yuva * 2, deger); }

  bool bekci_resetiydi() {
#if BEKCI_ETKIN
    return (mcusr_kopya & _BV(WDRF)) != 0;
#else
    return false;
#endif
  }
};

ArduinoDonanim donanim;
vx::Terminal terminal(donanim);

void setup() {
  randomSeed(analogRead(A3));            // bos pin okumasi: rastgele tohum (sira no ve idempotency anahtari icin)
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
#if BEKCI_ETKIN
  wdt_enable(WDTO_2S);
#endif
  terminal.baslat();
}

void loop() {
  terminal.tick();
}
