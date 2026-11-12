// Donanim soyutlama katmani (HAL). Terminal cekirdegi YALNIZCA bu arayuzle donanima dokunur:
//   * Uno'da  : atm_terminal.ino icindeki ArduinoDonanim (MFRC522, Keypad, LiquidCrystal_I2C, Serial, role ...)
//   * PC'de   : arduino/test/ icindeki sahte donanim (zaman, tus, kart, seri hat benzetimi ve gurultu enjeksiyonu)
#pragma once
#include "tasinabilir.h"

namespace vx {

class Donanim {
 public:
  virtual uint32_t simdi_ms() = 0;                        // millis(); 49 gunde basa sarar, cekirdek buna dayanikli
  virtual char tus() = 0;                                 // 0: tus yok
  virtual bool kart_oku(char *uid_hex, size_t kap) = 0;   // yeni kart varsa kucuk harf onaltilik UID yazar (RC522)
  virtual void lcd_satir(uint8_t satir, const char *metin16) = 0;  // tam 16 karakter + NUL (bosluk dolgulu)
  virtual void bip(bool basarili) = 0;
  virtual void role(bool ac) = 0;                         // kasa kilidi: true = kilit acik (guvenli durum: false)
  virtual void para_led(bool yak) = 0;
  virtual int seri_oku() = 0;                             // -1: bayt yok
  virtual int seri_yazilabilir() = 0;                     // simdi bloklamadan yazilabilecek bayt sayisi
  virtual void seri_yaz(const char *veri, size_t n) = 0;  // n <= seri_yazilabilir()
  virtual uint16_t rastgele() = 0;                        // 16 bit rastgele
  virtual void bekci_besle() = 0;                         // watchdog reset (yoksa bos)
  virtual bool bekci_resetiydi() = 0;                     // son acilis watchdog reseti miydi
  virtual uint16_t kalici_oku(uint8_t yuva) = 0;          // kalici bellek (EEPROM): 16 adet 16-bit yuva; yazilmamis = 0xFFFF
  virtual void kalici_yaz(uint8_t yuva, uint16_t deger) = 0;  // yalniz degisen bayt yazilmali (EEPROM omru)

 protected:
  ~Donanim() {}
};

}  // namespace vx
