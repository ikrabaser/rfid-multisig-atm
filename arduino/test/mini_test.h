// Bagimlilik gerektirmeyen minik test cercevesi (g++ yeterli).
#pragma once
#include <cstdio>
#include <string>
#include <vector>

struct TestKaydi {
  const char *ad;
  void (*f)();
};
inline std::vector<TestKaydi> &kayitlar() {
  static std::vector<TestKaydi> k;
  return k;
}
inline int &hata_sayaci() {
  static int n = 0;
  return n;
}
inline const char *&gecerli_test() {
  static const char *ad = "";
  return ad;
}
struct Kayit {
  Kayit(const char *ad, void (*f)()) { kayitlar().push_back({ad, f}); }
};
inline void basarisiz(const char *dosya, int satir, const std::string &ne) {
  hata_sayaci()++;
  std::printf("    HATA %s:%d [%s] %s\n", dosya, satir, gecerli_test(), ne.c_str());
}

#define TEST(ad)                  \
  static void ad();               \
  static Kayit kayit_##ad(#ad, ad); \
  static void ad()
#define KONTROL(x) \
  do { if (!(x)) basarisiz(__FILE__, __LINE__, #x); } while (0)
#define KONTROL_ESIT(a, b)                                                                  \
  do {                                                                                      \
    std::string _a = (a), _b = (b);                                                         \
    if (_a != _b) basarisiz(__FILE__, __LINE__, std::string(#a " = \"") + _a + "\" beklenen \"" + _b + "\""); \
  } while (0)
