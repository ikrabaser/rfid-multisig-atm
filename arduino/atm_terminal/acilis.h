// Acilis sayaci: her (yeniden) baslatmada artan, kalici (EEPROM) 16-bit sayi.
//
// Neden: islem anahtari (idempotency) acilis sayaci + millis + rastgele'den uretilir. Yalniz millis/random kullanilirsa
// ve rastgele tohum her acilista ayni cikarsa (yuzen ADC pini hep ayni degeri okuyabilir), yeniden basladiktan sonraki ILK
// islem eski bir islemle ayni anahtari uretebilir; sunucu bunu "tekrar" sanip parayi dusmeden TAMAM doner.
//
// EEPROM omru (~100 bin yazma): her acilista yeni deger 16 yuvadan SIRAYLA birine yazilir (n % 16); watchdog reset
// dongusunde bile omur 16 kat uzar. Yazma sirasinda elektrik giderse yari yazilmis deger yuva dogrulamasindan (v % 16 == yuva)
// gecemez ve yoksayilir; sayac bir onceki degerden devam eder.
#pragma once
#include "donanim.h"

namespace vx {

enum { KALICI_YUVA = 16 };

// Sayaci bir artirir, kalici yazar ve yeni degeri dondurur (1..0xFFFE; tasinca 1'e doner; 0xFFFF "bos" isaretidir).
uint16_t acilis_sayaci_ilerlet(Donanim &d);

}  // namespace vx
