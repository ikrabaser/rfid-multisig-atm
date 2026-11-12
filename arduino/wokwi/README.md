# VAULTIX terminali Wokwi'de

Gerçek firmware (ATmega328P) Wokwi devresinde çalışır: Uno + MFRC522 + keypad + I2C LCD + röle + buzzer + LED.
Pin haritası `atm_terminal.ino` ile aynıdır. **Bu klasör donanımsız doğrulama içindir; burada denenmemiş parçalar README'de işaretlidir.**

## 1) Tek başına deneme (köprüsüz)
1. `python arduino/araclar/wokwi_hazirla.py` → `arduino/wokwi/proje/` oluşur (commitlenmez).
2. wokwi.com → yeni **Arduino Uno** projesi. `proje/` içindeki dosyaları yükle (ya da yapıştır); `diagram.json` ve `libraries.txt` dahil.
3. ▶ ile başlat. Beklenen: LCD'de açılış ekranı, ~10 sn sonra köprü olmadığı için "Kopru yok!".

## 2) Köprüyle (Wokwi for VS Code + RFC2217; doğrulanmadı)
1. `avr_derle.sh` ile `firmware.hex/.elf` üret ya da VS Code uzantısıyla derle; bu klasöre koy.
2. VS Code'da bu klasörü aç, simülasyonu başlat (`wokwi.toml` seri portu `localhost:4000` olarak açar).
3. `python bridge/serial_bridge.py --port rfc2217://localhost:4000` (backend açık olmalı).

## Kartlar
MFRC522 parçasında yalnızca **Blue Card**'ın UID'si değiştirilebilir (`diagram.json` → `rfid.attrs.uid`, şu an `a0:a0:a0:01` = demo müşteri Ali).
Diğer kartlar sabit UID'lidir (11:22:33:44, 55:66:77:88, AA:BB:CC:DD, ...) ve demo veritabanında kayıtlı değildir.
Yetkili kartıyla onay denemek için Blue Card UID'sini `b0:b0:b0:01` gibi bir yetkili UID'ye çevirip yeniden başlat.

## Bilinen belirsizlikler
- Röle `transistor: "pnp"` (aktif-LOW) seçildi; Wokwi dokümanı yönü tutarsız anlatıyor. Kilit açılınca röle çekmiyorsa özelliği sil.
- Wokwi'de simüle edilen WDT/EEPROM davranışı denenmedi.
