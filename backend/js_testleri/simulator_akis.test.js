"use strict";
/*
  Web ATM simulatorunun akis testi (Node, ekstra paket gerekmez):
      node backend/js_testleri/simulator_akis.test.js

  Gercek tarayici/DOM yok: ATMSimulatoru'na sahte bir "arayuz" ve protokolu konusan sahte bir sunucu verilir.
  Zamanlar kucultulur (sureCarpani). Her test, firmware ile ayni LCD metinlerinin ayni sirayla gorundugunu dogrular.
*/
const assert = require("node:assert/strict");

// KABA_ZAMANLAYICI=1: Windows'un ~16 ms'lik zamanlayici adimini taklit eder (testin sure varsayimlarini sinamak icin)
if (process.env.KABA_ZAMANLAYICI) {
  const gercek = global.setTimeout;
  global.setTimeout = (fn, ms, ...a) => gercek(fn, Math.ceil((ms || 0) / 16) * 16, ...a);
}
const { ATMSimulatoru } = require("../banka/static/banka/simulator.js");

const uyu = (ms) => new Promise((coz) => setTimeout(coz, ms));
// Bekleme dongulerinde zamanlayici yerine setImmediate: Windows'un ~16 ms'lik zamanlayici adimindan etkilenmez
const tik = () => new Promise((coz) => setImmediate(coz));
const hata = (m) => "HATA|" + m.slice(0, 32);

// ---------------------------------------------------------------- sahte sunucu (protokolun sade bir uygulamasi)
function sahteSunucu() {
  const musteriler = { a0a0a001: { ad: "Ali Yilmaz", pin: "1234", bloke: false }, a0a0a009: { ad: "Bloke", pin: "0000", bloke: true } };
  const yetkililer = {
    b0b0b001: { ad: "Ayse", rol: 1, pin: "1111" }, b0b0b002: { ad: "Burak", rol: 1, pin: "2222" },
    b0b0b003: { ad: "Cem", rol: 2, pin: "3333" }, b0b0b004: { ad: "Deniz", rol: 2, pin: "4444" },
    b0b0b005: { ad: "Elif", rol: 3, pin: "5555" },
  };
  const s = { bakiye: 1000000, komutlar: [], kapali: false, gecikme: 0, talepler: {}, sonTalep: 0, sonIslem: 0, islemler: {} };

  function uygula(t) {
    if (t.tur === "CEKME") {
      s.bakiye -= t.tutar;
      s.islemler[t.islemNo] = { tutar: t.tutar, teslim: "BEKLIYOR" };
    }
  }

  s.komut = async (satir) => {
    s.komutlar.push(satir);
    if (s.kapali) throw new Error("sunucu kapali");
    if (s.gecikme) await uyu(s.gecikme);
    const p = satir.split("|");
    switch (p[0]) {
      case "KART":
        if (musteriler[p[1]]) return musteriler[p[1]].bloke ? "TIP|BLOKE" : "TIP|MUSTERI";
        return yetkililer[p[1]] ? "TIP|PERSONEL" : "TIP|BILINMIYOR";
      case "PIN": {
        const m = musteriler[p[1]];
        if (!m || m.pin !== p[2]) return hata("Yanlis PIN, kalan hak: 2");
        return `GIRIS|tok123456|${m.ad}`;
      }
      case "BAKIYE":
        return `BAKIYE|${s.bakiye.toFixed(2)}`;
      case "ISLEM": {
        const tutar = Number(p[3]);
        const islemNo = ++s.sonIslem;
        if (tutar > 5000) {
          const id = ++s.sonTalep;
          s.talepler[id] = { id, tur: "CEKME", tutar, gereken: 2, verilen: 0, oy: new Set(), baslatan: "musteri", durum: "BEKLIYOR", islemNo };
          return `BEKLE|${id}|2`;
        }
        uygula({ tur: "CEKME", tutar, islemNo });
        return `TAMAM|${s.bakiye.toFixed(2)}|${islemNo}`;
      }
      case "KASAAC": {
        const y = yetkililer[p[1]];
        if (!y || y.pin !== p[2]) return hata("Yanlis PIN, kalan hak: 2");
        if (y.rol < 2) return hata("Bu rol kasa acma talebi baslatamaz");
        const id = ++s.sonTalep;
        s.talepler[id] = { id, tur: "KASA_ACMA", tutar: 0, gereken: 3, verilen: 0, oy: new Set(), baslatan: p[1], durum: "BEKLIYOR", islemNo: ++s.sonIslem };
        return `BEKLE|${id}|3`;
      }
      case "BEKLEYEN": {
        const t = Object.values(s.talepler).find((x) => x.durum === "BEKLIYOR");
        if (!t) return "BEKLEYEN|YOK";
        return `BEKLEYEN|${t.id}|${t.tur === "KASA_ACMA" ? "Kasa" : "Cek"}|${t.tur === "KASA_ACMA" ? "ac" : t.tutar}|${t.verilen}|${t.gereken}`;
      }
      case "DURUM": {
        const t = s.talepler[p[1]];
        if (!t) return hata("Talep bulunamadi");
        return `DURUM|${t.verilen}|${t.gereken}|${t.durum}|25|0|${t.tur}|${t.islemNo}`;
      }
      case "ONAY": {
        const t = s.talepler[p[1]];
        const y = yetkililer[p[2]];
        if (!t) return hata("Talep bulunamadi");
        if (!y || y.pin !== p[3]) return hata("Yanlis PIN, kalan hak: 2");
        if (t.baslatan === p[2]) return hata("Kendi islemini onaylayamaz");
        if (t.oy.has(p[2])) return hata("Bu yetkili zaten oy kullandi");
        if (t.durum === "BEKLIYOR") {
          t.oy.add(p[2]);
          if (p[4] === "H") t.durum = "REDDEDILDI";
          else {
            t.verilen += 1;
            if (t.verilen >= t.gereken) { t.durum = "UYGULANDI"; uygula(t); }
          }
        }
        return `ONAY|${t.verilen}|${t.gereken}|${t.durum}`;
      }
      case "TESLIM": {
        const i = s.islemler[p[1]];
        if (!i) return hata("Islem bulunamadi");
        if (p[2] === "A") { i.teslim = "ALINDI"; return "TESLIM|ALINDI"; }
        s.bakiye += i.tutar;
        i.teslim = "GERI_ALINDI";
        return "TESLIM|GERI";
      }
      default:
        return hata("Bilinmeyen komut");
    }
  };
  return s;
}

// ---------------------------------------------------------------- test duzenegi
function kur() {
  const sunucu = sahteSunucu();
  const gecmis = [];
  const olay = { led: [], kilit: [], bip: [] };
  let ekran = ["", ""];
  let okunan = 0;
  const sim = ATMSimulatoru({
    sureCarpani: 0.02, // 20 sn PIN suresi -> 400 ms: Windows'ta bile tuslari gondermeye yeter, testler yine de hizli
    ekranYaz: (a, b) => { ekran = [a, b]; gecmis.push([a, b]); },
    led: (v) => olay.led.push(v),
    kilit: (v) => olay.kilit.push(v),
    bip: (v) => olay.bip.push(v),
    komut: (s) => sunucu.komut(s),
    rastgele: () => "abc123def456",
  });
  sim.baslat();

  // Gecmiste (okunandan sonra) bu metinle baslayan bir ekran gorunene kadar bekler; gorunce okunani ilerletir.
  async function ekranda(ust, alt) {
    const bitis = Date.now() + 3000;
    for (;;) {
      for (let i = okunan; i < gecmis.length; i++) {
        if (gecmis[i][0].startsWith(ust) && (alt === undefined || gecmis[i][1].startsWith(alt))) {
          okunan = i + 1;
          return gecmis[i];
        }
      }
      if (Date.now() > bitis) {
        throw new Error(`Ekran gorunmedi: "${ust}"${alt !== undefined ? ` / "${alt}"` : ""}\nSon ekranlar:\n` +
          gecmis.slice(-8).map((e) => `  [${e[0]}] [${e[1]}]`).join("\n"));
      }
      await tik();
    }
  }
  async function gonder(o) {
    const bitis = Date.now() + 3000;
    while (!sim.bekliyor()) {
      if (Date.now() > bitis) throw new Error("Terminal girdi beklemiyor");
      await tik();
    }
    sim.girdiVer(o);
  }
  const kart = (uid) => gonder({ tur: "kart", uid });
  async function tuslar(metin) { for (const t of metin) await gonder({ tur: "tus", tus: t }); }

  return { sim, sunucu, olay, gecmis, ekranda, gonder, kart, tuslar, ekran: () => ekran.slice() };
}

// Musteri girisi + menuye kadar
async function musteriGiris(t) {
  await t.ekranda("Karti okutun");
  await t.kart("a0a0a001");
  await t.ekranda("PIN girin:");
  await t.tuslar("1234");
  await t.ekranda("Merhaba", "Ali Yilmaz");
  await t.ekranda("A:Bakiye B:Cek", "C:Yatir D:Havale");
}

// ---------------------------------------------------------------- testler
const testler = [];
const test = (ad, fn) => testler.push([ad, fn]);

test("kucuk cekim: para alinir, bakiye ve teslim dogru", async () => {
  const t = kur();
  await musteriGiris(t);
  await t.tuslar("B");
  await t.ekranda("Tutar (C:. #:ok)");
  await t.tuslar("500#");
  await t.ekranda("Isleniyor");
  await t.ekranda("Parayi alin", "A: Aldim");
  assert.deepEqual(t.olay.led, [true]);
  await t.tuslar("A");
  const e = await t.ekranda("Islem tamam");
  assert.equal(e[1], "Bak: 999500.00");
  assert.ok(t.sunucu.komutlar.includes("TESLIM|1|A"));
  const islem = t.sunucu.komutlar.find((k) => k.startsWith("ISLEM|"));
  assert.equal(islem, "ISLEM|tok123456|C|500||abc123def456"); // 6 alan, idempotency anahtari sonda
  assert.deepEqual(t.olay.led, [true, false]);
  await t.ekranda("A:Bakiye B:Cek");
  await t.tuslar("*");
  await t.ekranda("Cikis yapildi");
  await t.ekranda("Karti okutun");
});

test("para alinmazsa zaman asiminda geri cekilir ve ters kaydedilir", async () => {
  const t = kur();
  await musteriGiris(t);
  await t.tuslar("B");
  await t.ekranda("Tutar");
  await t.tuslar("1000#");
  await t.ekranda("Parayi alin");
  // A'ya basilmaz
  await t.ekranda("Kaydediliyor");
  await t.ekranda("Para alinmadi", "Islem iptal");
  assert.ok(t.sunucu.komutlar.includes("TESLIM|1|H"));
  assert.equal(t.sunucu.bakiye, 1000000);
  assert.equal(t.olay.bip.at(-1), false);
});

test("PIN girerken D son rakami siler (bos PIN'de etkisiz)", async () => {
  const t = kur();
  const satir = (n) => ("PIN: " + "*".repeat(n)).padEnd(11) + "D:sil";
  await t.ekranda("Karti okutun");
  await t.kart("a0a0a001");
  await t.ekranda("PIN girin:");
  await t.tuslar("D");                 // bos PIN'de silme: hicbir sey olmaz
  await t.tuslar("129");               // yanlis rakam
  await t.tuslar("D");                 // sil
  await t.tuslar("34");                // dogrusu: 1234
  await t.ekranda("Merhaba", "Ali Yilmaz");
  const pinEkranlari = t.gecmis.filter((e) => e[0] === "PIN girin:").map((e) => e[1]);
  assert.deepEqual(pinEkranlari, [satir(0), satir(1), satir(2), satir(3), satir(2), satir(3), satir(4)]);
  assert.ok(t.sunucu.komutlar.includes("PIN|a0a0a001|1234"));
  assert.equal(pinEkranlari[0].length, 16);
});

test("kurus: C tusu ondalik nokta, en fazla 2 hane, D geri siler", async () => {
  const t = kur();
  await musteriGiris(t);
  await t.tuslar("B");
  await t.ekranda("Tutar");
  await t.tuslar("1C5C"); // ikinci C yok sayilir
  await t.tuslar("2");
  await t.tuslar("3");      // 3. ondalik hane yok sayilir
  const e = await t.ekranda("", "1.52");
  assert.equal(e[1], "1.52");
  await t.tuslar("D");
  await t.ekranda("", "1.5");
  await t.tuslar("#");
  await t.ekranda("Parayi alin");
  assert.ok(t.sunucu.komutlar.some((k) => k.startsWith("ISLEM|tok123456|C|1.5|")));
});

test("yanlis PIN hata ekrani ve basa donus; iptal (*) ve bilinmeyen/bloke kart", async () => {
  const t = kur();
  await t.ekranda("Karti okutun");
  await t.kart("a0a0a001");
  await t.ekranda("PIN girin:");
  await t.tuslar("0000");
  await t.ekranda("Yanlis PIN, kala", "n hak: 2");
  await t.ekranda("Karti okutun");
  await t.kart("a0a0a001");
  await t.ekranda("PIN girin:");
  await t.tuslar("*");
  await t.ekranda("Iptal edildi");
  await t.ekranda("Karti okutun");
  await t.kart("ffffffff");
  await t.ekranda("Kart taninmadi");
  await t.ekranda("Karti okutun");
  await t.kart("a0a0a009");
  await t.ekranda("Kart bloke", "Subeye basvurun");
});

test("onayli cekim: yetkili kartlari okutulur, oylar sayilir, sonra para verilir", async () => {
  const t = kur();
  await musteriGiris(t);
  await t.tuslar("B");
  await t.ekranda("Tutar");
  await t.tuslar("10000#");
  await t.ekranda("Onay gerekli", "10000 TL");
  await t.ekranda("Onay 0/2", "Yetkili kart oku");

  await t.kart("b0b0b001");                         // Ayse
  await t.ekranda("A:Onayla B:Red", "*:Iptal");
  await t.tuslar("A");
  await t.ekranda("Yetkili PIN:");
  await t.tuslar("1111");
  const oy = await t.ekranda("Oyunuz alindi");
  assert.equal(oy[1], "1/2");
  await t.ekranda("Onay 1/2");

  await t.kart("b0b0b002");                         // Burak
  await t.ekranda("A:Onayla B:Red");
  await t.tuslar("A");
  await t.ekranda("Yetkili PIN:");
  await t.tuslar("2222");
  await t.ekranda("Oyunuz alindi");
  await t.ekranda("Islem onaylandi", "Tamamlandi");
  await t.ekranda("Parayi alin");
  await t.tuslar("A");
  assert.ok(t.sunucu.komutlar.some((k) => /^TESLIM\|\d+\|A$/.test(k)));
  assert.equal(t.sunucu.bakiye, 990000);
});

test("onay bekleme: yanlis yetkili PIN'i, musteri karti yetkili sayilmaz, red", async () => {
  const t = kur();
  await musteriGiris(t);
  await t.tuslar("B");
  await t.ekranda("Tutar");
  await t.tuslar("20000#");
  await t.ekranda("Onay 0/2");
  await t.kart("a0a0a001");                         // musteri karti yetkili degil
  await t.ekranda("Yetkili karti", "degil");
  await t.ekranda("Onay 0/2");
  await t.kart("b0b0b001");
  await t.ekranda("A:Onayla");
  await t.tuslar("A");
  await t.ekranda("Yetkili PIN:");
  await t.tuslar("9999");                           // yanlis PIN
  await t.ekranda("Yanlis PIN, kala");
  await t.ekranda("Onay 0/2");
  await t.kart("b0b0b002");
  await t.ekranda("A:Onayla");
  await t.tuslar("B");                              // reddet
  await t.ekranda("Yetkili PIN:");
  await t.tuslar("2222");
  await t.ekranda("Oyunuz alindi");
  await t.ekranda("Islem reddedildi");
  assert.equal(t.sunucu.bakiye, 1000000);
});

test("kasa dairesi: mudur yrd. baslatir, 3 onay, kilit acilir ve kapanir", async () => {
  const t = kur();
  await t.ekranda("Karti okutun");
  await t.kart("b0b0b003");                         // Cem
  await t.ekranda("Talep yok", "A:Kasa ac *:Cik");
  await t.tuslar("A");
  await t.ekranda("Yetkili PIN:");
  await t.tuslar("3333");
  await t.ekranda("Onay gerekli", "Kasa acma");
  await t.ekranda("Onay 0/3");
  for (const [uid, pin] of [["b0b0b001", "1111"], ["b0b0b002", "2222"], ["b0b0b004", "4444"]]) {
    await t.kart(uid);
    await t.ekranda("A:Onayla");
    await t.tuslar("A");
    await t.ekranda("Yetkili PIN:");
    await t.tuslar(pin);
    await t.ekranda("Oyunuz alindi");
  }
  await t.ekranda("Islem onaylandi");
  await t.ekranda("Kasa acildi", "5 sn acik");
  await t.ekranda("Karti okutun");
  assert.deepEqual(t.olay.kilit, [true, false]);
  assert.equal(t.olay.led.length, 0);               // kasa acmada para LED'i yanmaz
});

test("kasa acma: gorevli baslatamaz, baslatan kendi talebini onaylayamaz, bekleyen talep varken menu cikmaz", async () => {
  const t = kur();
  await t.ekranda("Karti okutun");
  await t.kart("b0b0b001");                         // Ayse (gorevli)
  await t.ekranda("Talep yok");
  await t.tuslar("A");
  await t.ekranda("Yetkili PIN:");
  await t.tuslar("1111");
  await t.ekranda("Bu rol kasa acma", " talebi baslatam");
  await t.ekranda("Karti okutun");

  await t.kart("b0b0b003");                         // Cem baslatir
  await t.ekranda("Talep yok");
  await t.tuslar("A");
  await t.ekranda("Yetkili PIN:");
  await t.tuslar("3333");
  await t.ekranda("Onay 0/3");
  await t.kart("b0b0b003");                         // kendi talebini onaylamaya calisir
  await t.ekranda("A:Onayla");
  await t.tuslar("A");
  await t.ekranda("Yetkili PIN:");
  await t.tuslar("3333");
  await t.ekranda("Kendi islemini o", "naylayamaz");
});

test("bos modda okutulan yetkili karti bekleyen talebi gosterir", async () => {
  const t = kur();
  // talebi baska yoldan olustur: musteri 10000 TL ister, sonra terminal bosa doner (zaman asimi yerine * ile cikis olmaz),
  // bu yuzden talebi dogrudan sunucuda ac
  t.sunucu.talepler[1] = { id: 1, tur: "CEKME", tutar: 60000, gereken: 2, verilen: 1, oy: new Set(), baslatan: "x", durum: "BEKLIYOR", islemNo: 1 };
  t.sunucu.sonTalep = 1;
  await t.ekranda("Karti okutun");
  await t.kart("b0b0b001");
  await t.ekranda("Talep #1", "Cek 60000");
  await t.ekranda("A:Onayla B:Red");
  await t.tuslar("*");
  await t.ekranda("Karti okutun");
});

test("terminal mesgulken tus ve kart yok sayilir (donanimdaki gibi)", async () => {
  const t = kur();
  t.sunucu.gecikme = 40;                            // sunucu yavas
  await t.ekranda("Karti okutun");
  await t.kart("a0a0a001");
  await t.ekranda("Kart okundu", "Bekleyin...");
  assert.equal(t.sim.girdiVer({ tur: "tus", tus: "1" }), false);
  assert.equal(t.sim.girdiVer({ tur: "kart", uid: "b0b0b001" }), false);
  t.sunucu.gecikme = 0;
  await t.ekranda("PIN girin:");
});

test("sunucu kapaliyken ISLEM iki kez daha denenir, sonra hata gosterilir", async () => {
  const t = kur();
  await musteriGiris(t);
  await t.tuslar("B");
  await t.ekranda("Tutar");
  t.sunucu.kapali = true;
  await t.tuslar("100#");
  await t.ekranda("Isleniyor");
  await t.ekranda("Tekrar deneniyor");
  await t.ekranda("Sunucu kapali");
  assert.equal(t.sunucu.komutlar.filter((k) => k.startsWith("ISLEM|")).length, 3);
  // ayni idempotency anahtari ile
  const anahtarlar = new Set(t.sunucu.komutlar.filter((k) => k.startsWith("ISLEM|")).map((k) => k.split("|")[5]));
  assert.equal(anahtarlar.size, 1);
});

test("havale: 6 haneli hesap no sonra tutar; bakiye gosterimi", async () => {
  const t = kur();
  await musteriGiris(t);
  await t.tuslar("A");
  const b = await t.ekranda("Bakiye:");
  assert.equal(b[1], "1000000.00 TL");
  await t.ekranda("A:Bakiye B:Cek");
  await t.tuslar("D");
  await t.ekranda("Hesap no(6hane)");
  await t.tuslar("100002#");
  await t.ekranda("Tutar (C:. #:ok)");
  await t.tuslar("250#");
  await t.ekranda("Isleniyor");
  assert.ok(t.sunucu.komutlar.some((k) => k === "ISLEM|tok123456|H|250|100002|abc123def456"));
});

// ---------------------------------------------------------------- calistir
(async () => {
  let basarisiz = 0;
  for (const [ad, fn] of testler) {
    try {
      await Promise.race([fn(), uyu(8000).then(() => { throw new Error("zaman asimi"); })]);
      console.log("  ok   " + ad);
    } catch (e) {
      basarisiz += 1;
      console.log("  HATA " + ad + "\n       " + String(e.message || e).replace(/\n/g, "\n       "));
    }
  }
  console.log(`\n${testler.length - basarisiz}/${testler.length} test gecti`);
  process.exit(basarisiz ? 1 : 0);
})();
