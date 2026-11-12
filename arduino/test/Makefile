# PC'de firmware cekirdegi testleri:  make        (hizli)
#                                      make sanitize (ASan + UBSan: bellek tasmasi / tanimsiz davranis denetimi)
CXX ?= g++
CEKIRDEK = ../atm_terminal
KAYNAK = $(CEKIRDEK)/acilis.cpp $(CEKIRDEK)/metin.cpp $(CEKIRDEK)/cerceve.cpp $(CEKIRDEK)/baglanti.cpp $(CEKIRDEK)/terminal.cpp
TESTLER = main.cpp $(wildcard test_*.cpp)
TESTLER_HARIC_MAIN = $(wildcard test_*.cpp)
BAYRAK = -std=c++11 -Wall -Wextra -Wno-unused-parameter -O1 -g

test: derle
	./test_calistir

derle:
	$(CXX) $(BAYRAK) -o test_calistir $(TESTLER) $(KAYNAK)

sanitize:
	$(CXX) $(BAYRAK) -fsanitize=address,undefined -fno-sanitize-recover=undefined -o test_sanitize $(TESTLER) $(KAYNAK)
	./test_sanitize

# Satir kapsami (gcov): hangi cekirdek satirlari testlerce hic calistirilmadi
kapsam:
	rm -rf kapsam_cikti && mkdir kapsam_cikti
	$(CXX) $(BAYRAK) --coverage -o kapsam_cikti/t main.cpp $(TESTLER_HARIC_MAIN) $(KAYNAK)
	./kapsam_cikti/t > /dev/null
	cd kapsam_cikti && gcov -o . t-*.gcda 2>/dev/null | grep -A1 "File '.*atm_terminal/\(terminal\|baglanti\|cerceve\|metin\|acilis\)" | grep -v "^--"

sim:
	$(CXX) $(BAYRAK) -o sim_calistir sim_main.cpp $(KAYNAK)

temizle:
	rm -f test_calistir test_sanitize sim_calistir
.PHONY: test derle sanitize kapsam sim temizle
