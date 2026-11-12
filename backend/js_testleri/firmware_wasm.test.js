"use strict";
/*
  Firmware Laboratuvari'nin WebAssembly cekirdegi (Node, ekstra paket gerekmez):
      node backend/js_testleri/firmware_wasm.test.js

  GERCEK terminal cekirdegi (arduino/atm_terminal) wasm olarak yuklenir; kopru yerine bu dosyada ufak bir sahte kopru vardir.
  Dogrulananlar: acilis, CRC'li HELLO, cevap yokken "Kopru yok!", cevapla toparlanma, yeniden iletimde ayni sira no,
  kalici acilis sayaci (EEPROM) ve yeniden baslatmada anahtar degisimi, rolenin kendiliginden acilmamasi.
*/
const assert = require("node:assert/strict");
const VxModul = require("../banka/static/banka/firmware-lab/vx_firmware.js");

function crc16(metin) {
  let c = 0xffff;
  for (const ch of metin) {
    c ^= ch.charCodeAt(0) << 8;
    for (let k = 0; k < 8; k++) c = c & 0x8000 ? ((c << 1) ^ 0x1021) & 0xffff : (c << 1) & 0xffff;
  }
  return c;
}
assert.equal(crc16("123456789"), 0x29b1);                       // CRC-16/CCITT-FALSE kontrol degeri (Python tarafiyla ayni)
const kodla = (sira, yuk) => { const g = sira.toString(16).toUpperCase().padStart(2, "0") + ":" + yuk; return g + "*" + crc16(g).toString(16).toUpperCase().padStart(4, "0"); };
const coz = (s) => { const m = /^([0-9A-F]{2}):(.*)\*([0-9A-F]{4})$/.exec(s); if (!m) return null; return { sira: parseInt(m[1], 16), yuk: m[2], gecerli: crc16(m[1] + ":" + m[2]) === parseInt(m[3], 16) }; };

async function yukle() {
  const m = await VxModul();
  const f = (ad, d, a) => m.cwrap(ad, d, a);
  const api = {
    baslat: f("vx_baslat", null, ["number", "number"]), adim: f("vx_adim", null, ["number"]), cikti: f("vx_cikti", "string", []),
    kart: f("vx_kart", null, ["string"]), tus: f("vx_tus", null, ["number"]), seriVer: f("vx_seri_ver", null, ["string"]),
    ist: f("vx_istatistik", "string", []), eepromAl: f("vx_eeprom_al", "number", ["number"]), eepromVer: f("vx_eeprom_ver", null, ["number", "number"]), zaman: f("vx_zaman", "number", []),
    gurultu: f("vx_gurultu", null, ["number", "number", "number"]), role: f("vx_role", "number", []),
  };
  api.lcd = [""]; api.tx = []; api.roleGecmisi = [];
  // olaylari biriktirir; sahte kopru fonksiyonu verilirse her TX'e cevap yazar
  api.ilerle = (ms, kopru) => {
    for (let i = 0; i < ms; i++) {
      api.adim(1);
      for (const ham of api.cikti().split("\n")) {
        if (!ham) continue;
        const [t, tur, ...r] = ham.split(" "), deger = r.join(" ");
        if (tur === "LCD") api.lcd = deger.split("|");
        else if (tur === "ROLE") api.roleGecmisi.push(deger);
        else if (tur === "TX") { api.tx.push(deger); const c = coz(deger); if (kopru && c && c.gecerli) { const cevap = kopru(c.yuk); if (cevap !== null) api.seriVer(kodla(c.sira, cevap)); } }
      }
    }
  };
  api.durumAl = () => Object.fromEntries(api.ist().split(" ").map((p) => p.split("=")).map(([k, v]) => [k, Number(v)]));
  return api;
}

const testler = [];
const test = (ad, fn) => testler.push([ad, fn]);

test("acilis: ekran ve ilk HELLO cerceveli, CRC gecerli", async () => {
  const a = await yukle(); a.baslat(1, 1000); a.ilerle(3000);
  assert.deepEqual(a.lcd.map((s) => s.trim()), ["Karti okutun", "Mini ATM / Kasa"]);
  const c = coz(a.tx[0]);
  assert.ok(c && c.gecerli, a.tx[0]);
  assert.equal(c.yuk, "HELLO|2");
});

test("kopru cevap vermezse ayni cerceve 3 kez gider, sonra yeni sira no ile yeni HELLO; ekranda 'Kopru yok!'", async () => {
  const a = await yukle(); a.baslat(1, 1000); a.ilerle(25000);
  const cerceveler = a.tx.map(coz);
  assert.ok(cerceveler.every((c) => c && c.gecerli));
  const siralar = [...new Set(cerceveler.map((c) => c.sira))];
  assert.ok(siralar.length >= 2, "yeni tur yeni sira no kullanmali");
  const ilk = a.tx.filter((s) => coz(s).sira === siralar[0]);
  assert.equal(ilk.length, 3, "ayni sira no ile 3 deneme");
  assert.equal(new Set(ilk).size, 1, "yeniden iletim birebir ayni cerceve");
  assert.equal(siralar[1], (siralar[0] + 1) & 0xff);
  assert.ok(a.lcd.join("|").includes("Kopru yok!"), a.lcd.join("|"));
});

test("kopru HELLO'ya cevap verince 'Kopru yok!' cikmaz ve PING surer", async () => {
  const a = await yukle(); a.baslat(1, 1000);
  a.ilerle(35000, (yuk) => (yuk.startsWith("HELLO") ? "HELLO|OK|2" : yuk === "PING" ? "PONG" : null));
  assert.ok(!a.lcd.join("|").includes("Kopru yok!"), a.lcd.join("|"));
  assert.ok(a.tx.map((s) => coz(s).yuk).includes("PING"));
});

test("kart okutunca KART cercevesi gider (UID kucuk harf, CRC gecerli); kayitsiz kart role acmaz", async () => {
  const a = await yukle(); a.baslat(1, 1000);
  const kopru = (yuk) => (yuk.startsWith("HELLO") ? "HELLO|OK|2" : yuk.startsWith("KART|") ? "TIP|BILINMIYOR" : null);
  a.ilerle(2500, kopru); a.kart("04112233445566"); a.ilerle(3000, kopru);
  const kart = a.tx.map(coz).find((c) => c.yuk.startsWith("KART|"));
  assert.ok(kart && kart.gecerli);
  assert.equal(kart.yuk, "KART|04112233445566");
  assert.deepEqual(a.roleGecmisi, []);
  assert.equal(a.role(), 0);
});

test("kalici acilis sayaci: her guc verisinde artar, EEPROM disari alinip geri yuklenebilir", async () => {
  const a = await yukle(); a.baslat(1, 1000);
  assert.equal(a.durumAl().acilis, 1);
  a.baslat(2, 1000); assert.equal(a.durumAl().acilis, 2);
  a.baslat(3, 1000); assert.equal(a.durumAl().acilis, 3);
  const ep = Array.from({ length: 16 }, (_, i) => a.eepromAl(i));
  const b = await yukle();                                       // "sayfa yenilendi": bellek bos baslar, kayittan yuklenir
  assert.equal(b.eepromAl(0), 0xffff);
  ep.forEach((v, i) => b.eepromVer(i, v));
  b.baslat(9, 1000);
  assert.equal(b.durumAl().acilis, 4, "sayac kalici bellekten devam etmeli");
});

test("islem anahtari yeniden baslatmada degisir (ayni zaman, ayni tohum olsa bile)", async () => {
  const anahtarlar = [];
  const a = await yukle();
  for (let boot = 0; boot < 3; boot++) {
    a.tx.length = 0; a.baslat(1, 1000);
    const kopru = (yuk) => {
      if (yuk.startsWith("HELLO")) return "HELLO|OK|2";
      if (yuk.startsWith("KART|")) return "TIP|MUSTERI";
      if (yuk.startsWith("PIN|")) return "GIRIS|tok|Ali";
      if (yuk.startsWith("ISLEM|")) { anahtarlar.push(yuk.split("|").pop()); return "HATA|test"; }
      return null;
    };
    a.ilerle(2500, kopru); a.kart("a0a0a001"); a.ilerle(1500, kopru);
    for (const c of "1234") { a.tus(c.charCodeAt(0)); a.ilerle(200, kopru); }
    a.ilerle(6000, kopru);
    for (const c of ["B", "1", "0", "0", "#"]) { a.tus(c.charCodeAt(0)); a.ilerle(300, kopru); }
    a.ilerle(3000, kopru);
  }
  assert.equal(anahtarlar.length >= 3, true, JSON.stringify(anahtarlar));
  assert.equal(new Set(anahtarlar.slice(0, 3)).size, 3, "her acilista farkli anahtar: " + anahtarlar.join(","));
  assert.ok(anahtarlar.every((k) => k.length === 16));
});

test("gurultulu hatta bozuk satirlar uretilir ama terminal cokmez, role degismezi ihlali yok", async () => {
  const a = await yukle(); a.baslat(7, 1000); a.gurultu(0.004, 0.001, 0.001);
  a.ilerle(60000, (yuk) => (yuk.startsWith("HELLO") ? "HELLO|OK|2" : yuk === "PING" ? "PONG" : null));
  assert.ok(a.tx.map(coz).some((c) => c === null || !c.gecerli), "gurultu bozuk satir uretmeli");
  assert.equal(a.durumAl().ihlal, 0);
  assert.deepEqual(a.roleGecmisi, []);
});

(async () => {
  let basarisiz = 0;
  for (const [ad, fn] of testler) {
    try { await fn(); console.log("  ok  ", ad); }
    catch (e) { basarisiz++; console.log("  HATA", ad, "\n      ", e.message.split("\n")[0]); }
  }
  console.log(`\n${testler.length} test, ${basarisiz} basarisiz`);
  process.exit(basarisiz ? 1 : 0);
})();
