#include <cstring>
#include "mini_test.h"

int main(int argc, char **argv) {
  const char *filtre = argc > 1 ? argv[1] : nullptr;
  int calisan = 0, basarisiz_test = 0;
  for (auto &t : kayitlar()) {
    if (filtre && !std::strstr(t.ad, filtre)) continue;
    int once = hata_sayaci();
    gecerli_test() = t.ad;
    t.f();
    calisan++;
    bool ok = hata_sayaci() == once;
    if (!ok) basarisiz_test++;
    std::printf("  %s %s\n", ok ? "ok  " : "FAIL", t.ad);
  }
  std::printf("\n%d test, %d basarisiz, %d kontrol hatasi\n", calisan, basarisiz_test, hata_sayaci());
  return hata_sayaci() ? 1 : 0;
}
