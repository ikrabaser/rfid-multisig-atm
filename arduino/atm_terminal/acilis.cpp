#include "acilis.h"

namespace vx {

uint16_t acilis_sayaci_ilerlet(Donanim &d) {
  uint16_t deger[KALICI_YUVA];
  bool gecerli[KALICI_YUVA];
  for (uint8_t i = 0; i < KALICI_YUVA; i++) {
    deger[i] = d.kalici_oku(i);
    gecerli[i] = deger[i] != 0xFFFF && deger[i] != 0 && (deger[i] % KALICI_YUVA) == i;  // yari yazilmis/bozuk degerler elenir
  }
  // En yeni gecerli deger: kendisinden "ileride" (mod 65536 ilk yarida) baska gecerli deger olmayan
  uint16_t son = 0;
  bool var = false;
  for (uint8_t i = 0; i < KALICI_YUVA; i++) {
    if (!gecerli[i]) continue;
    bool en_yeni = true;
    for (uint8_t j = 0; j < KALICI_YUVA; j++) {
      if (!gecerli[j] || j == i) continue;
      uint16_t fark = (uint16_t)(deger[j] - deger[i]);
      if (fark != 0 && fark < 0x8000) en_yeni = false;
    }
    if (en_yeni) {
      son = deger[i];
      var = true;
    }
  }
  uint16_t yeni = var ? (uint16_t)(son + 1) : 1;
  if (yeni == 0 || yeni == 0xFFFF) yeni = 1;
  d.kalici_yaz((uint8_t)(yeni % KALICI_YUVA), yeni);
  return yeni;
}

}  // namespace vx
