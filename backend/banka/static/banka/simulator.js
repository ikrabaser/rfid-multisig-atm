"use strict";

/*
  Web ATM simulatoru: arduino/atm_terminal/atm_terminal.ino akisinin tarayicidaki karsiligi.

  Ayni satir protokolunu (KART|uid, PIN|uid|pin, ISLEM|... ) ayni sirayla ve ayni LCD metinleriyle kullanir.
  Donanimdaki gibi terminal MESGULKEN (sunucu cevabi beklerken, mesaj gosterirken) tus ve kart yok sayilir.
  Arayuz (DOM, ses, ag) disaridan verilir; bu sayede akis Node ile sahte bir sunucuya karsi test edilebilir.
*/
function ATMSimulatoru(arayuz) {
  const olcek = arayuz.sureCarpani === undefined ? 1 : arayuz.sureCarpani;
  const ms = (x) => Math.max(1, Math.round(x * olcek));
  const bekle = (x) => new Promise((coz) => setTimeout(coz, ms(x)));
  const PARA_ALMA_MS = 8000;
  const KILIT_ACIK_MS = 5000;

  // ------------------------------------------------------------------ girdi (tus ve kart olaylari)
  let bekleyen = null;

  function girdiVer(olay) {
    if (!bekleyen) return false; // terminal mesgul: donanimda oldugu gibi yok sayilir
    const b = bekleyen;
    bekleyen = null;
    if (b.zaman) clearTimeout(b.zaman);
    b.coz(olay);
    return true;
  }

  function girdiBekleHam(hamMs) {
    return new Promise((coz) => {
      const b = { coz, zaman: null };
      if (hamMs > 0) {
        b.zaman = setTimeout(() => {
          if (bekleyen === b) {
            bekleyen = null;
            coz(null);
          }
        }, hamMs);
      }
      bekleyen = b;
    });
  }

  // izinli: ornek "AB*" (yalniz bu tuslar) ya da null (her tus). Kartlar ve izinsiz tuslar yok sayilir.
  async function tusBekleBitis(bitis, izinli) {
    for (;;) {
      const kalan = bitis - Date.now();
      if (kalan <= 0) return null;
      const olay = await girdiBekleHam(kalan);
      if (olay === null) return null;
      if (olay.tur === "tus" && (!izinli || izinli.includes(olay.tus))) return olay.tus;
    }
  }
  const tusBekle = (sure, izinli) => tusBekleBitis(Date.now() + ms(sure), izinli);

  // ------------------------------------------------------------------ ekran, ses, sunucu
  let satirlar = ["", ""];
  function ekran(a, b = "") {
    satirlar = [String(a).slice(0, 16), String(b).slice(0, 16)];
    arayuz.ekranYaz(satirlar[0], satirlar[1]);
  }
  function satirYaz(i, metin) {
    satirlar[i] = String(metin).slice(0, 16);
    arayuz.ekranYaz(satirlar[0], satirlar[1]);
  }
  const bip = (basarili) => arayuz.bip(basarili);

  async function komut(satir) {
    try {
      return String(await arayuz.komut(satir));
    } catch (e) {
      return "HATA|Sunucu kapali";
    }
  }
  const alan = (s, n) => {
    const p = String(s).split("|");
    return n < p.length ? p[n] : "";
  };

  async function hataGoster(cevap) {
    let m = alan(cevap, 1);
    if (!m) m = String(cevap);
    ekran(m.slice(0, 16), m.slice(16, 32));
    bip(false);
    await bekle(2500);
  }

  // ------------------------------------------------------------------ para ve kilit
  async function kilitAc() {
    ekran("Kasa acildi", "5 sn acik");
    bip(true);
    arayuz.kilit(true);
    await bekle(KILIT_ACIK_MS);
    arayuz.kilit(false);
  }

  // Teslim sonucunu sunucuya bildirir. Baglanti hatasinda AYNI satir tekrar gider (sunucu bildirimi idempotenttir).
  async function teslimBildir(islemId, kod, verilen) {
    const satir = `TESLIM|${islemId}|${kod}` + (verilen ? `|${verilen}` : "");
    let c = await komut(satir);
    for (let d = 0; d < 2 && c.startsWith("HATA|Sunucu"); d++) c = await komut(satir);
    return c;
  }

  // Para dagitimi: once dagitici sayar/verir (donanimda sensor, simulatorde arayuzden gelen sonuc), sonra LED yanar,
  // musteri A tusuyla "aldim" der. Sonuc sunucuya bildirilir:
  //   normal       -> A: alindi,  H: alinmadi (zaman asimi, para geri cekildi: tam iade)
  //   SIKISTI      -> S: para verilmedi, tam iade, dagitici kapanir
  //   EKSIK        -> A'dan sonra K|verilen banknotlar: yalniz fark iade edilir (A yoksa H: hepsi geri cekilir)
  //   BAGLANTI_YOK -> sonuc bildirilemez; sunucu teslimi "belirsiz" isaretler, para operator sayimina kadar tutulur
  // true donerse islem geri alinmistir (cagiran "Islem tamam" yazmaz).
  async function paraVer(islemId, plan, tutar) {
    ekran("Para sayiliyor", tutar ? `${tutar} TL` : "");
    const d = arayuz.dagitici ? await arayuz.dagitici(plan || "", tutar) : { durum: "TAMAM" };
    await bekle(800);

    if (d.durum === "SIKISTI") {
      bip(false);
      ekran("Para sikisti!", "Bekleyin...");
      if (!islemId) return false;
      const c = await teslimBildir(islemId, "S");
      if (alan(c, 0) !== "TESLIM") {
        await hataGoster(c);
        return false;
      }
      ekran("Para sikisti", "Hesaba iade");
      await bekle(3000);
      return true;
    }

    arayuz.led(true);
    ekran("Parayi alin", d.durum === "EKSIK" ? `A:Aldim ${d.verilenTutar} TL` : "A: Aldim");
    const alindi = (await tusBekle(PARA_ALMA_MS, "A")) === "A";
    arayuz.led(false);
    if (!islemId) return false;

    if (d.durum === "BAGLANTI_YOK") {
      bip(false);
      ekran("Baglanti koptu", "Sonuc belirsiz");
      await bekle(3000);
      return false;
    }

    ekran("Kaydediliyor...");
    const kod = !alindi ? "H" : d.durum === "EKSIK" ? "K" : "A";
    const c = await teslimBildir(islemId, kod, kod === "K" ? d.verilen : "");
    if (alan(c, 0) !== "TESLIM") {
      await hataGoster(c);
      return false;
    }
    if (alan(c, 1) === "KISMI") {
      ekran("Eksik verildi", `${alan(c, 2)} TL iade`);
      bip(false);
      await bekle(3000);
      return false; // verilen kisim alindi; "Islem tamam" ekrani gosterilmez
    }
    if (alan(c, 1) === "GERI") {
      ekran("Para alinmadi", "Islem iptal");
      bip(false);
      await bekle(2500);
      return true;
    }
    return false;
  }

  // ------------------------------------------------------------------ tus girisi
  // PIN satiri: "PIN: ***    D:sil" (16 karakter; D tusu son rakami siler)
  const pinSatiri = (yildiz) => ("PIN: " + yildiz).padEnd(11, " ") + "D:sil";

  // 4 haneli PIN alir. D: son rakami siler, *: iptal (bos string), 20 sn zaman asimi.
  async function pinAl(baslik) {
    let pin = "";
    ekran(baslik, pinSatiri(""));
    const bitis = Date.now() + ms(20000);
    while (pin.length < 4) {
      const t = await tusBekleBitis(bitis, null);
      if (t === null) return "";
      if (t >= "0" && t <= "9") {
        pin += t;
        satirYaz(1, pinSatiri("*".repeat(pin.length)));
      } else if (t === "D") {
        if (pin.length > 0) {
          pin = pin.slice(0, -1);
          satirYaz(1, pinSatiri("*".repeat(pin.length)));
        }
      } else if (t === "*") {
        return "";
      }
    }
    return pin;
  }

  // #: onay, *: iptal, D: geri sil. ondalik: C tusu ondalik noktasi (en fazla 2 kurus hanesi).
  async function sayiAl(baslik, maks, ondalik) {
    let s = "";
    ekran(baslik, "");
    let bitis = Date.now() + ms(30000);
    for (;;) {
      const t = await tusBekleBitis(bitis, null);
      if (t === null) return "";
      bitis = Date.now() + ms(30000);
      if (t >= "0" && t <= "9") {
        const nokta = s.indexOf(".");
        const kurusDolu = nokta >= 0 && s.length - nokta > 2;
        if (!kurusDolu && s.length < maks) s += t;
      } else if (t === "C" && ondalik) {
        if (s.length > 0 && s.indexOf(".") < 0 && s.length < maks) s += ".";
      } else if (t === "#") {
        if (s.length > 0) {
          if (s.endsWith(".")) s = s.slice(0, -1);
          return s;
        }
      } else if (t === "*") {
        return "";
      } else if (t === "D") {
        if (s.length > 0) s = s.slice(0, -1);
      }
      satirYaz(1, s);
    }
  }

  // ------------------------------------------------------------------ yetkili (personel)
  async function personelKarar(talepId, uid) {
    const c = await komut(`KART|${uid}`);
    if (alan(c, 1) !== "PERSONEL") {
      ekran("Yetkili karti", "degil");
      bip(false);
      await bekle(1200);
      return;
    }
    ekran("A:Onayla B:Red", "*:Iptal");
    const t = await tusBekle(10000, "AB*");
    if (t !== "A" && t !== "B") return;

    const pin = await pinAl("Yetkili PIN:");
    if (pin.length < 4) return;

    ekran("Kontrol ediliyor");
    const r = await komut(`ONAY|${talepId}|${uid}|${pin}|${t === "A" ? "E" : "H"}`);
    if (alan(r, 0) === "ONAY") {
      ekran("Oyunuz alindi", `${alan(r, 1)}/${alan(r, 2)}`);
      bip(true);
      await bekle(1200);
    } else {
      await hataGoster(r);
    }
  }

  // Bekleyen talep yokken yetkili, kasa dairesini acmak icin talep baslatabilir
  async function kasaAcmaTalebi(uid) {
    ekran("Talep yok", "A:Kasa ac *:Cik");
    const t = await tusBekle(8000, "A*");
    if (t !== "A") return;

    const pin = await pinAl("Yetkili PIN:");
    if (pin.length < 4) return;

    ekran("Kontrol ediliyor");
    const c = await komut(`KASAAC|${uid}|${pin}`);
    if (alan(c, 0) === "BEKLE") {
      await onayBekle(alan(c, 1), "Kasa acma");
    } else {
      await hataGoster(c);
    }
  }

  async function personelModu(uid) {
    const c = await komut("BEKLEYEN");
    if (alan(c, 0) !== "BEKLEYEN") {
      await hataGoster(c);
      return;
    }
    if (alan(c, 1) === "YOK") {
      await kasaAcmaTalebi(uid);
      return;
    }
    const talepId = alan(c, 1);
    ekran("Talep #" + talepId, alan(c, 2) + " " + alan(c, 3));
    await bekle(2000);
    await personelKarar(talepId, uid);
  }

  // ------------------------------------------------------------------ musteri
  // Onay bekler; bu sirada okutulan yetkili kartlari oy verir. Sonuc ekranda gosterilir.
  async function onayBekle(talepId, etiket, tutar) {
    ekran("Onay gerekli", etiket);
    await bekle(1500);
    let sonSorgu = 0;
    for (;;) {
      if (Date.now() - sonSorgu >= ms(1000)) {
        sonSorgu = Date.now();
        const c = await komut(`DURUM|${talepId}`);
        if (alan(c, 0) !== "DURUM") {
          await hataGoster(c);
          return;
        }
        const verilen = alan(c, 1);
        const gereken = alan(c, 2);
        const durum = alan(c, 3);
        const kalan = alan(c, 4);
        const ust = alan(c, 5) === "1";

        if (durum === "UYGULANDI") {
          ekran("Islem onaylandi", "Tamamlandi");
          bip(true);
          if (alan(c, 6) === "KASA_ACMA") await kilitAc();
          else await paraVer(alan(c, 7), alan(c, 8), tutar);
          await bekle(1500);
          return;
        }
        if (durum === "REDDEDILDI") {
          ekran("Islem reddedildi");
          bip(false);
          await bekle(2500);
          return;
        }
        if (durum === "SURESI_DOLDU") {
          ekran("Sure doldu", "Islem iptal");
          bip(false);
          await bekle(2500);
          return;
        }
        if (durum === "BASARISIZ") {
          ekran("Islem basarisiz", "Subeye basvurun");
          bip(false);
          await bekle(2500);
          return;
        }
        satirYaz(0, `Onay ${verilen}/${gereken} ${kalan}sn`);
        satirYaz(1, ust ? "Ust rol gerekli" : "Yetkili kart oku");
      }
      const olay = await girdiBekleHam(ms(100));
      if (olay && olay.tur === "kart") {
        await personelKarar(talepId, olay.uid);
        sonSorgu = 0; // ekrani hemen guncelle
      }
    }
  }

  async function bakiyeGoster(token) {
    const c = await komut(`BAKIYE|${token}`);
    if (alan(c, 0) === "BAKIYE") {
      ekran("Bakiye:", alan(c, 1) + " TL");
      bip(true);
      await bekle(3500);
    } else {
      await hataGoster(c);
    }
  }

  // tur: 'C' cekme, 'Y' yatirma, 'H' havale
  async function islemYap(token, tur) {
    let hedef = "";
    if (tur === "H") {
      hedef = await sayiAl("Hesap no(6hane)", 6, false);
      if (hedef.length !== 6) {
        ekran("Iptal edildi");
        await bekle(1000);
        return;
      }
    }
    const tutar = await sayiAl("Tutar (C:. #:ok)", 11, true);
    if (tutar.length === 0) {
      ekran("Iptal edildi");
      await bekle(1000);
      return;
    }

    ekran("Isleniyor...");
    // Idempotency anahtari: ayni istek tekrarlanirsa AYNI anahtar gider, para iki kez cekilmez
    const satir = `ISLEM|${token}|${tur}|${tutar}|${hedef}|${arayuz.rastgele()}`;
    let c = await komut(satir);
    for (let d = 0; d < 2 && c.startsWith("HATA|Sunucu"); d++) {
      ekran("Tekrar deneniyor");
      c = await komut(satir);
    }
    const a0 = alan(c, 0);
    if (a0 === "TAMAM") {
      const geriAlindi = tur === "C" ? await paraVer(alan(c, 2), alan(c, 3), tutar) : false;
      if (!geriAlindi) {
        ekran("Islem tamam", "Bak: " + alan(c, 1));
        bip(true);
        await bekle(3000);
      }
    } else if (a0 === "BEKLE") {
      await onayBekle(alan(c, 1), tutar + " TL", tutar);
    } else {
      await hataGoster(c);
    }
  }

  async function menuSec() {
    ekran("A:Bakiye B:Cek", "C:Yatir D:Havale");
    const t = await tusBekle(30000, "ABCD*");
    return t === null ? "*" : t; // zaman asimi: cikis
  }

  async function musteriOturumu(uid) {
    const pin = await pinAl("PIN girin:");
    if (pin.length < 4) {
      ekran("Iptal edildi");
      await bekle(1000);
      return;
    }
    ekran("Kontrol ediliyor");
    const c = await komut(`PIN|${uid}|${pin}`);
    if (alan(c, 0) !== "GIRIS") {
      await hataGoster(c);
      return;
    }
    const token = alan(c, 1);
    ekran("Merhaba", alan(c, 2));
    bip(true);
    await bekle(1200);

    for (;;) {
      const sec = await menuSec();
      if (sec === "*") break;
      if (sec === "A") await bakiyeGoster(token);
      if (sec === "B") await islemYap(token, "C");
      if (sec === "C") await islemYap(token, "Y");
      if (sec === "D") await islemYap(token, "H");
    }
    ekran("Cikis yapildi");
    await bekle(1000);
  }

  // ------------------------------------------------------------------ ana dongu
  async function anaDongu() {
    for (;;) {
      ekran("Karti okutun", "Mini ATM / Kasa");
      let olay;
      do {
        olay = await girdiBekleHam(0); // sonsuz bekle
      } while (!olay || olay.tur !== "kart");

      ekran("Kart okundu", "Bekleyin...");
      const c = await komut(`KART|${olay.uid}`);
      if (alan(c, 0) !== "TIP") {
        await hataGoster(c);
        continue;
      }
      const tip = alan(c, 1);
      if (tip === "MUSTERI") {
        await musteriOturumu(olay.uid);
      } else if (tip === "PERSONEL") {
        await personelModu(olay.uid);
      } else if (tip === "BLOKE") {
        ekran("Kart bloke", "Subeye basvurun");
        bip(false);
        await bekle(2500);
      } else {
        ekran("Kart taninmadi");
        bip(false);
        await bekle(1800);
      }
    }
  }

  let calisiyor = false;
  function baslat() {
    if (calisiyor) return;
    calisiyor = true;
    anaDongu();
  }

  return {
    baslat,
    girdiVer,
    bekliyor: () => bekleyen !== null,
    ekranDurumu: () => satirlar.slice(),
  };
}

/* ====================================================================== tarayici baglantisi */
(function () {
  if (typeof document === "undefined" || !document.getElementById("atm")) return;

  const veri = JSON.parse(document.getElementById("simulator-veri").textContent);
  const $ = (id) => document.getElementById(id);
  const el = (etiket, sinif, metin) => {
    const e = document.createElement(etiket);
    if (sinif) e.className = sinif;
    if (metin !== undefined) e.textContent = metin;
    return e;
  };
  const csrf = () => {
    const m = document.cookie.match(/(?:^|; )csrftoken=([^;]*)/);
    return m ? decodeURIComponent(m[1]) : "";
  };

  // ---------------------------------------------------------------- LCD (2 x 16 hucre)
  const hucreler = [0, 1].map((i) => {
    const kap = $("lcd" + i);
    kap.textContent = "";
    return Array.from({ length: 16 }, () => {
      const h = el("span", "hucre", "\u00a0");
      kap.appendChild(h);
      return h;
    });
  });
  function ekranYaz(a, b) {
    [a, b].forEach((metin, i) => {
      const t = String(metin).padEnd(16, " ");
      hucreler[i].forEach((h, j) => {
        h.textContent = t[j] === " " ? "\u00a0" : t[j];
      });
    });
  }

  // ---------------------------------------------------------------- ses (buzzer)
  let ses = null;
  function bip(basarili) {
    if (!$("ses").checked) return;
    try {
      ses = ses || new (window.AudioContext || window.webkitAudioContext)();
      const o = ses.createOscillator();
      const g = ses.createGain();
      o.frequency.value = basarili ? 1800 : 400;
      g.gain.value = 0.05;
      o.connect(g);
      g.connect(ses.destination);
      o.start();
      o.stop(ses.currentTime + (basarili ? 0.12 : 0.4));
    } catch (e) {
      /* ses desteklenmiyor */
    }
  }

  // ---------------------------------------------------------------- protokol gunlugu (PIN ve token maskeli)
  // Yalnizca GIDEN satirlardaki PIN ve token maskelenir; gelen cevaplarda (ONAY|2|2|SURESI_DOLDU gibi) dokunulmaz.
  // Tek istisna: GIRIS cevabindaki oturum token'i kisaltilir.
  function maskele(yon, satir) {
    const p = satir.split("|");
    const k = p[0];
    if (yon === "→") {
      if (k === "PIN" && p.length > 2) p[2] = "****";
      if (k === "ONAY" && p.length > 3) p[3] = "****";
      if (k === "KASAAC" && p.length > 2) p[2] = "****";
      if ((k === "BAKIYE" || k === "ISLEM") && p[1]) p[1] = p[1].slice(0, 6) + "…";
    } else if (k === "GIRIS" && p[1]) {
      p[1] = p[1].slice(0, 6) + "…";
    }
    return p.join("|");
  }
  function protokolYaz(yon, satir) {
    const liste = $("protokol");
    const s = el("div", yon === "→" ? "giden" : "gelen", `${yon} ${maskele(yon, satir)}`);
    liste.prepend(s);
    while (liste.children.length > 14) liste.removeChild(liste.lastChild);
  }

  async function komutYolla(satir) {
    protokolYaz("→", satir);
    if ($("ariza").value === "yanit_kaybi" && satir.startsWith("ISLEM|")) {
      yanitKaybiBekliyor = true; // ISLEM sunucuda islenir, ama cevap bu istekte kaybolur
      $("ariza").value = "normal";
    }
    const r = await fetch(veri.komut_url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify({ satir, terminal: $("terminal").value }),
    });
    const j = await r.json();
    if (yanitKaybiBekliyor && satir.startsWith("ISLEM|")) {
      yanitKaybiBekliyor = false;
      protokolYaz("←", "(cevap kayboldu: baglanti koptu)");
      throw new Error("yanit kayboldu"); // terminal ayni anahtarla yeniden dener; sunucu ayni sonucu verir
    }
    protokolYaz("←", j.cevap);
    const plan = (j.cevap.match(/^TAMAM\|[^|]*\|[^|]*\|(.+)$/) || [])[1];
    if (plan) $("son-plan").textContent = plan;
    return j.cevap;
  }

  // ---------------------------------------------------------------- para dagitici simulasyonu (ariza enjeksiyonu)
  const planCoz = (metin) =>
    Object.fromEntries(String(metin || "").split("+").filter(Boolean).map((p) => p.split("x").map(Number)));
  const planTutari = (plan) => Object.entries(plan).reduce((t, [n, a]) => t + Number(n) * a, 0);
  const planYaz = (plan) =>
    Object.keys(plan).map(Number).sort((a, b) => b - a).filter((n) => plan[n] > 0).map((n) => `${n}x${plan[n]}`).join("+");

  // Secili ariza SADECE bir sonraki cekim icin gecerlidir, kullanilinca "normal"e doner
  function arizayiTuket() {
    const secim = $("ariza").value;
    $("ariza").value = "normal";
    return secim;
  }

  async function dagitici(plan, tutar) {
    const ariza = arizayiTuket();
    if (plan) $("son-plan").textContent = plan;
    if (ariza === "sikisma") return { durum: "SIKISTI" };
    if (ariza === "baglanti") return { durum: "BAGLANTI_YOK" };
    if (ariza === "eksik") {
      const verilen = planCoz(plan);
      const enBuyuk = Math.max(...Object.keys(verilen).map(Number));
      if (!isFinite(enBuyuk)) return { durum: "TAMAM" };
      verilen[enBuyuk] -= 1; // en buyuk banknottan biri verilmedi
      if (planTutari(verilen) === 0) return { durum: "TAMAM" };
      return { durum: "EKSIK", verilen: planYaz(verilen), verilenTutar: planTutari(verilen) };
    }
    return { durum: "TAMAM" };
  }

  let yanitKaybiBekliyor = false;
  const rastgele = () => Array.from(crypto.getRandomValues(new Uint8Array(6)), (b) => b.toString(16).padStart(2, "0")).join("");

  // ---------------------------------------------------------------- denetleyici
  const sim = ATMSimulatoru({
    ekranYaz,
    led: (acik) => $("led").classList.toggle("acik", acik),
    kilit: (acik) => {
      $("kilit").classList.toggle("acik", acik);
      $("kilit-yazi").textContent = acik ? "KİLİT AÇIK" : "KİLİTLİ";
    },
    bip,
    komut: komutYolla,
    rastgele,
    dagitici,
  });

  function durumMesaji(metin) {
    const m = $("mesaj");
    m.textContent = metin;
    m.classList.toggle("gorunur", !!metin);
    if (metin) setTimeout(() => { if (m.textContent === metin) durumMesaji(""); }, 2500);
  }

  // tus takimi
  const TUSLAR = [["1", "2", "3", "A"], ["4", "5", "6", "B"], ["7", "8", "9", "C"], ["*", "0", "#", "D"]];
  TUSLAR.flat().forEach((t) => {
    const b = el("button", "tus" + (/[A-D]/.test(t) ? " harf" : ""), t);
    b.type = "button";
    b.addEventListener("click", () => {
      b.blur();
      tusBas(t);
    });
    b.dataset.tus = t;
    $("tuslar").appendChild(b);
  });
  function tusBas(t) {
    const buton = document.querySelector(`.tus[data-tus="${CSS.escape(t)}"]`);
    if (buton) {
      buton.classList.add("basili");
      setTimeout(() => buton.classList.remove("basili"), 120);
    }
    if (!sim.girdiVer({ tur: "tus", tus: t })) durumMesaji("Terminal meşgul: tuş yok sayıldı");
  }
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
    let t = null;
    if (/^[0-9]$/.test(e.key)) t = e.key;
    else if (/^[a-dA-D]$/.test(e.key)) t = e.key.toUpperCase();
    else if (e.key === "*") t = "*";
    else if (e.key === "#" || e.key === "Enter") t = "#";
    else if (e.key === "Backspace") t = "D";
    else if (e.key === "Escape") t = "*";
    if (t) {
      e.preventDefault();
      tusBas(t);
    }
  });

  // kartlar
  function kartOkut(uid, eleman) {
    eleman.classList.add("okutuluyor");
    setTimeout(() => eleman.classList.remove("okutuluyor"), 700);
    if (!sim.girdiVer({ tur: "kart", uid })) durumMesaji("Terminal meşgul: kart yok sayıldı");
  }
  function kartCiz(kap, liste, tur) {
    kap.textContent = "";
    liste.forEach((k) => {
      const e = el("button", "kart " + tur);
      e.type = "button";
      e.appendChild(el("div", "kart-ad", k.ad));
      e.appendChild(el("div", "kart-alt", tur === "yetkili" ? `${k.rol} · ${k.uid}` : `hesap ${k.hesap_no} · ${k.uid}`));
      if (k.ipucu) e.appendChild(el("div", "kart-pin", `demo PIN ${k.ipucu}`));
      if (k.bloke) e.appendChild(el("div", "kart-bloke", "BLOKE"));
      e.addEventListener("click", () => kartOkut(k.uid, e));
      kap.appendChild(e);
    });
  }
  kartCiz($("musteri-kartlari"), veri.musteriler, "musteri");
  kartCiz($("yetkili-kartlari"), veri.yetkililer, "yetkili");
  $("ipucu-notu").hidden = !veri.ipuclari_acik;

  // terminal secimi
  veri.terminaller.forEach((t) => {
    const o = el("option", "", `${t.kod} · ${t.ad}`);
    o.value = t.kod;
    $("terminal").appendChild(o);
  });

  $("sifirla").addEventListener("click", () => location.reload());

  // ---------------------------------------------------------------- canli serit
  const DURUM_SINIF = { tutarli: "ok", tutarsiz: "kotu", baslangic_yok: "uyari" };
  let sorgu = 0;
  async function seritYenile() {
    try {
      const zincir = sorgu % 8 === 0 ? "?zincir=1" : "";
      sorgu += 1;
      const r = await fetch(veri.serit_url + zincir, { credentials: "same-origin" });
      if (!r.ok) return;
      const v = await r.json();
      seritCiz(v);
    } catch (e) {
      /* sunucu gecici olarak yok */
    }
  }
  let sonZincir = null;
  function seritCiz(v) {
    dagiticiPaneliCiz(v);
    if (v.zincir) sonZincir = v.zincir;
    const kap = $("serit-icerik");
    kap.textContent = "";

    const rozetler = el("div", "rozetler");
    [["Politika", v.durumlar.politika], ["Yetkili", v.durumlar.personel], ["Kart", v.durumlar.kart]].forEach(([ad, d]) =>
      rozetler.appendChild(el("span", "rozet " + DURUM_SINIF[d], `${ad}: ${d === "tutarli" ? "tutarlı" : d === "tutarsiz" ? "TUTARSIZ" : "başlangıç yok"}`)));
    rozetler.appendChild(el("span", "rozet " + (v.anomali_sayisi ? "uyari" : "ok"), `Anomali: ${v.anomali_sayisi}`));
    if (sonZincir) rozetler.appendChild(el("span", "rozet " + (sonZincir.saglam ? "ok" : "kotu"),
      sonZincir.saglam ? `Zincir sağlam (${sonZincir.kayit_sayisi})` : "ZİNCİR BOZUK"));
    kap.appendChild(rozetler);

    kap.appendChild(el("h3", "", "Hesaplar"));
    const hesap = el("table", "tablo");
    v.hesaplar.forEach((h) => {
      const satir = el("tr");
      satir.appendChild(el("td", "", h.hesap_no));
      satir.appendChild(el("td", "", h.tur === "KASA" ? "Nakit kasa" : h.ad));
      satir.appendChild(el("td", "sayi", Number(h.bakiye).toLocaleString("tr-TR", { minimumFractionDigits: 2 })));
      hesap.appendChild(satir);
    });
    kap.appendChild(hesap);

    kap.appendChild(el("h3", "", "Bekleyen onaylar"));
    if (!v.bekleyenler.length) kap.appendChild(el("div", "bos", "Bekleyen talep yok"));
    v.bekleyenler.forEach((t) => {
      const kutu = el("div", "talep");
      const baslik = t.tur === "KASA_ACMA" ? "Kasa dairesi açma" : `${t.tur} ${Number(t.tutar).toLocaleString("tr-TR")} TL`;
      kutu.appendChild(el("div", "", `#${t.talep_id} ${baslik}`));
      kutu.appendChild(el("div", "ince", `onay ${t.verilen}/${t.gereken} · ${t.kalan_sn} sn kaldı${t.ust_yetkili_gerekli ? " · üst rol gerekli" : ""}`));
      const cubuk = el("div", "cubuk");
      const ic = el("div", "cubuk-ic");
      ic.style.width = `${Math.min(100, (t.verilen / Math.max(1, t.gereken)) * 100)}%`;
      cubuk.appendChild(ic);
      kutu.appendChild(cubuk);
      kap.appendChild(kutu);
    });

    if (v.belirsiz.length) {
      kap.appendChild(el("h3", "", "Teslimi belirsiz çekmeler"));
      v.belirsiz.forEach((b) => {
        const kutu = el("div", "talep belirsiz");
        kutu.appendChild(el("div", "", `#${b.islem_id} · ${Number(b.tutar).toLocaleString("tr-TR")} TL · ${b.terminal}`));
        kutu.appendChild(el("div", "ince", `plan ${b.plan || "-"} · kasetler sayıldıktan sonra karar ver`));
        const dugmeler = el("div", "dugmeler");
        [["Verildi", "VERILDI"], ["Verilmedi (iade)", "VERILMEDI"]].forEach(([yazi, sonuc]) => {
          const d = el("button", "kucuk", yazi);
          d.type = "button";
          d.addEventListener("click", () => operatorGonder({ eylem: "coz", islem_id: b.islem_id, sonuc }));
          dugmeler.appendChild(d);
        });
        kutu.appendChild(dugmeler);
        kap.appendChild(kutu);
      });
    }

    kap.appendChild(el("h3", "", "Defter hareketleri"));
    const def = el("table", "tablo");
    v.hareketler.forEach((h) => {
      const satir = el("tr");
      satir.appendChild(el("td", "", `#${h.islem}`));
      satir.appendChild(el("td", "", h.hesap));
      satir.appendChild(el("td", h.yon === "BORC" ? "borc" : "alacak", h.yon === "BORC" ? "BORÇ" : "ALACAK"));
      satir.appendChild(el("td", "sayi", Number(h.tutar).toLocaleString("tr-TR", { minimumFractionDigits: 2 })));
      def.appendChild(satir);
    });
    kap.appendChild(def);

    kap.appendChild(el("h3", "", "Denetim kaydı (son olaylar)"));
    v.loglar.forEach((l) => {
      const s = el("div", "log");
      s.appendChild(el("span", "log-no", `#${l.sira}`));
      s.appendChild(el("span", "log-zaman", l.zaman));
      s.appendChild(el("span", "log-olay", l.olay));
      s.appendChild(el("div", "log-ozet", l.ozet));
      kap.appendChild(s);
    });
  }
  // Kaset paneli (secili terminal) ve operator dugmeleri
  async function operatorGonder(govde) {
    const r = await fetch(veri.operator_url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(govde),
    });
    const j = await r.json();
    if (!j.ok) durumMesaji(j.hata || "Operatör işlemi başarısız");
    seritYenile();
  }

  function dagiticiPaneliCiz(v) {
    const kod = $("terminal").value;
    const kap = $("kasetler");
    kap.textContent = "";
    (v.kasetler[kod] || []).forEach((k) => {
      const satir = el("div", "kaset-satir");
      satir.appendChild(el("span", "kaset-nominal", `${k.nominal} TL`));
      satir.appendChild(el("span", "kaset-adet", `${k.adet.toLocaleString("tr-TR")} adet`));
      const bilgi = [];
      if (k.rezerve) bilgi.push(`rezerve ${k.rezerve}`);
      if (k.iade_kutusu) bilgi.push(`iade kutusu ${k.iade_kutusu}`);
      satir.appendChild(el("span", "ince", bilgi.join(" · ")));
      kap.appendChild(satir);
    });
    const acik = v.dagiticilar[kod] !== false;
    const durum = $("dagitici-durum");
    durum.textContent = acik ? "Dağıtıcı açık" : "DAĞITICI KAPALI (arıza)";
    durum.className = "rozet " + (acik ? "ok" : "kotu");
    $("dagitici-ac").hidden = acik;
  }

  seritYenile();
  setInterval(seritYenile, 1500);
  $("dagitici-ac").addEventListener("click", () => operatorGonder({ eylem: "dagitici_ac", terminal: $("terminal").value }));
  $("terminal").addEventListener("change", seritYenile);

  // ---------------------------------------------------------------- telefonlar (SMS gelen kutusu simulasyonu)
  let telefonAcik = false;
  async function telefonYenile() {
    if (!telefonAcik) return;
    try {
      const r = await fetch(veri.telefon_url, { credentials: "same-origin" });
      const v = await r.json();
      const kap = $("telefon-liste");
      kap.textContent = "";
      if (!v.mesajlar.length) kap.appendChild(el("div", "bos", "Gelen mesaj yok"));
      v.mesajlar.slice().reverse().forEach((m) => {
        const k = el("div", "sms");
        k.appendChild(el("div", "sms-ust", `${m.telefon} · ${m.zaman}`));
        k.appendChild(el("div", "", m.metin));
        kap.appendChild(k);
      });
    } catch (e) {
      /* yoksay */
    }
  }
  $("telefon-ac").addEventListener("click", () => {
    telefonAcik = !telefonAcik;
    $("telefon").hidden = !telefonAcik;
    telefonYenile();
  });
  setInterval(telefonYenile, 2000);

  sim.baslat();
})();

if (typeof module !== "undefined") module.exports = { ATMSimulatoru };
