/* Firmware Laboratuvarı: GERÇEK firmware çekirdeği (WebAssembly) + gerçek köprü kodu/sunucu (POST /firmware-lab/hat/).
 *
 * Tarayıcı yalnızca "kablo ve ekran"dır: zamanı ilerletir, tuş/kart verir, firmware'in seri satırlarını sunucuya taşır,
 * köprü cevabını geri verir. CRC, NAK, yeniden iletim, tekrar önbelleği, onay kuralları: hepsi gerçek koddur. */
(function () {
  "use strict";
  const VERI = JSON.parse(document.getElementById("fl-veri").textContent);
  const $ = (id) => document.getElementById(id);
  const LAB_ID = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2) + Date.now()).replace(/[^0-9a-z-]/gi, "").slice(0, 36);
  const EEPROM_ANAHTAR = "vx_lab_eeprom";
  const MAKS_SATIR = 400;

  // ---------------------------------------------------------------- CRC-16/CCITT-FALSE (bağımsız doğrulama)
  function crc16(metin) {
    let c = 0xFFFF;
    for (let i = 0; i < metin.length; i++) {
      c ^= (metin.charCodeAt(i) & 0xFF) << 8;
      for (let k = 0; k < 8; k++) c = (c & 0x8000) ? ((c << 1) ^ 0x1021) & 0xFFFF : (c << 1) & 0xFFFF;
    }
    return c;
  }
  const CERCEVE = /^([0-9A-Fa-f]{2}|--):(.*)\*([0-9A-Fa-f]{4})$/;
  function coz(satir) {
    const m = CERCEVE.exec(satir);
    if (!m) return null;
    const govde = m[1] + ":" + m[2];
    return { sira: m[1].toUpperCase(), yuk: m[2], crc: m[3].toUpperCase(), gecerli: crc16(govde).toString(16).toUpperCase().padStart(4, "0") === m[3].toUpperCase() };
  }
  // PIN ve jeton arayüzde gösterilmez (köprü günlüğüyle aynı ilke)
  function gorunen(yuk) {
    const p = yuk.split("|"), k = p[0].toUpperCase();
    if (["PIN", "PINDEGIS", "KASAAC", "ONAY"].includes(k)) return k + "|•••";
    if (["BAKIYE", "ISLEM"].includes(k) && p.length > 1) { p[1] = "•••"; return p.join("|"); }
    if (k === "GIRIS" && p.length > 1) { p[1] = "•••"; return p.join("|"); }
    return yuk;
  }

  // ---------------------------------------------------------------- arayüz parçaları
  const lcd = [$("lcd0"), $("lcd1")];
  function lcdYaz(s0, s1) { lcd[0].textContent = (s0 || "").padEnd(16).slice(0, 16); lcd[1].textContent = (s1 || "").padEnd(16).slice(0, 16); }
  lcdYaz("", "");

  const tuslar = [["1", "2", "3", "A"], ["4", "5", "6", "B"], ["7", "8", "9", "C"], ["*", "0", "#", "D"]];
  const tusAlani = $("tuslar");
  tuslar.flat().forEach((c) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "tus" + (/[A-D]/.test(c) ? " harf" : ""); b.textContent = c;
    b.addEventListener("click", () => tusBas(c));
    tusAlani.appendChild(b);
  });
  function tusBas(c) { if (modul) { modul.vx_tus(c.charCodeAt(0)); } }
  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT" || e.ctrlKey || e.metaKey || e.altKey) return;
    let c = e.key;
    if (c === "Enter") c = "#"; else if (c === "Backspace") c = "D"; else if (c === "Escape") c = "*"; else c = c.length === 1 ? c.toUpperCase() : "";
    if (c && "0123456789ABCD*#".includes(c)) { e.preventDefault(); tusBas(c); }
  });

  const terminalSecim = $("terminal");
  (VERI.terminaller || []).forEach((t) => { const o = document.createElement("option"); o.value = t.kod; o.textContent = `${t.kod} · ${t.ad}`; terminalSecim.appendChild(o); });
  if (!terminalSecim.options.length) { const o = document.createElement("option"); o.value = "ATM-01"; o.textContent = "ATM-01"; terminalSecim.appendChild(o); }

  function kartDugmeleri() {
    const kutu = $("fl-kartlar");
    const ekle = (uid, ad, alt, ipucu, bloke) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "fl-kart" + (bloke ? " bloke" : "");
      b.innerHTML = '<span class="ad"></span><span class="rol"></span><code></code><span class="ipucu"></span>';
      b.querySelector(".ad").textContent = ad; b.querySelector(".rol").textContent = alt;
      b.querySelector("code").textContent = uid; b.querySelector(".ipucu").textContent = ipucu ? "PIN " + ipucu : "";
      b.addEventListener("click", () => kartOkut(uid));
      kutu.appendChild(b);
    };
    (VERI.musteriler || []).forEach((m) => ekle(m.uid, m.ad, "müşteri", m.ipucu, m.bloke));
    (VERI.yetkililer || []).forEach((y) => ekle(y.uid, y.ad, y.rol, y.ipucu, y.bloke));
  }
  kartDugmeleri();
  function kartOkut(uid) { if (modul) modul.vx_kart(uid); }
  $("fl-uid-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const v = $("fl-uid").value.trim().toLowerCase().replace(/[^0-9a-f]/g, "");
    if (v) kartOkut(v);
  });

  // ---------------------------------------------------------------- hat günlüğü
  const hatKutusu = $("fl-hat");
  let hatTutucu = null;   // ekleme yapılan son TX satırı (önbellek rozeti için)
  function hatSatiri(yon, t, satir, ek) {
    const d = document.createElement("div");
    const c = coz(satir);
    d.className = "fl-satir " + (yon === "tx" ? "tx" : "rx") + (c && !c.gecerli ? " bozuk" : "") + (!c ? " bozuk" : "");
    const tEl = document.createElement("span"); tEl.className = "t"; tEl.textContent = (t / 1000).toFixed(2);
    const yEl = document.createElement("span"); yEl.className = "yon"; yEl.textContent = yon === "tx" ? "→" : "←";
    const yuk = document.createElement("span"); yuk.className = "yuk";
    const roz = document.createElement("span"); roz.className = "rozetler";
    if (c) {
      yuk.innerHTML = "<b></b> <span class=\"ham\"></span>";
      yuk.querySelector("b").textContent = c.sira + " " + gorunen(c.yuk);
      yuk.querySelector(".ham").textContent = "*" + c.crc;
      roz.appendChild(rozet(c.gecerli ? "CRC ✓" : "CRC ✗", c.gecerli ? "ok" : "kotu"));
    } else {
      yuk.innerHTML = "<span class=\"ham\"></span>"; yuk.firstChild.textContent = satir.length > 60 ? satir.slice(0, 60) + "…" : satir;
      roz.appendChild(rozet("çerçeve değil", "kotu"));
    }
    (ek || []).forEach((e) => roz.appendChild(rozet(e[0], e[1])));
    d.append(tEl, yEl, yuk, roz);
    hatEkle(d);
    return { satir: d, roz };
  }
  function rozet(metin, tur) { const s = document.createElement("span"); s.className = "rozet " + tur; s.textContent = metin; return s; }
  function hatNotu(t, metin) {
    const d = document.createElement("div"); d.className = "fl-satir not";
    d.innerHTML = '<span class="t"></span><span class="yon">•</span><span></span>';
    d.children[0].textContent = (t / 1000).toFixed(2); d.children[2].textContent = metin;
    hatEkle(d);
  }
  function hatEkle(d) {
    const alta = hatKutusu.scrollTop + hatKutusu.clientHeight >= hatKutusu.scrollHeight - 30;
    hatKutusu.appendChild(d);
    while (hatKutusu.childElementCount > MAKS_SATIR) hatKutusu.removeChild(hatKutusu.firstChild);
    if (alta) hatKutusu.scrollTop = hatKutusu.scrollHeight;
  }

  // ---------------------------------------------------------------- köprüye gönderme (sıralı)
  let kuyruk = Promise.resolve();
  let kayipBayragi = false, koprukesik = false, sunucuIst = null;
  function cerez(ad) { const m = document.cookie.match(new RegExp("(?:^|; )" + ad + "=([^;]*)")); return m ? decodeURIComponent(m[1]) : ""; }
  function koprunun(t, satir, tx) {
    kuyruk = kuyruk.then(async () => {
      if (koprukesik) { tx.roz.appendChild(rozet("köprü kesik", "uyari")); return; }
      let sonuc;
      try {
        const r = await fetch(VERI.hat_url, {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json", "X-CSRFToken": cerez("csrftoken") },
          body: JSON.stringify({ cerceve: satir, lab_id: LAB_ID, terminal: terminalSecim.value }),
        });
        sonuc = await r.json();
        if (!r.ok) throw new Error(sonuc.hata || r.status);
      } catch (e) {
        tx.roz.appendChild(rozet("sunucu hatası", "kotu")); hatNotu(simdi(), "Sunucuya ulaşılamadı: " + e.message); return;
      }
      sunucuIst = sonuc.istatistik;
      if (sonuc.onbellekten) tx.roz.appendChild(rozet("önbellekten", "uyari"));
      if (sonuc.cevap === null) { tx.roz.appendChild(rozet("köprü sustu", "uyari")); return; }
      if (kayipBayragi) {
        kayipBayragi = false; $("b-kayip").classList.remove("bekliyor");
        tx.roz.appendChild(rozet("cevap kayboldu", "kotu"));
        hatNotu(simdi(), "Köprü isteği İŞLEDİ ama cevap hatta kayboldu; terminal aynı sıra no ile yeniden deneyecek.");
        return;
      }
      modul.vx_seri_ver(sonuc.cevap);
    });
  }

  // ---------------------------------------------------------------- firmware (WebAssembly)
  let modul = null, sonZaman = 0, hiz = 1;
  const bip = (function () {
    let ctx = null;
    return (tamam) => {
      if (!$("ses").checked) return;
      try {
        ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
        const o = ctx.createOscillator(), g = ctx.createGain();
        o.frequency.value = tamam ? 1800 : 400; g.gain.value = 0.05; o.connect(g); g.connect(ctx.destination);
        o.start(); o.stop(ctx.currentTime + (tamam ? 0.12 : 0.4));
      } catch (e) { /* ses yoksa sorun degil */ }
    };
  })();
  function simdi() { return modul ? modul.vx_zaman() : 0; }

  function olaylariIsle(metin) {
    if (!metin) return;
    for (const ham of metin.split("\n")) {
      if (!ham) continue;
      const i = ham.indexOf(" "), j = ham.indexOf(" ", i + 1);
      const t = Number(ham.slice(0, i)), tur = ham.slice(i + 1, j < 0 ? undefined : j), deger = j < 0 ? "" : ham.slice(j + 1);
      if (tur === "LCD") { const [a, b] = deger.split("|"); lcdYaz(a, b); }
      else if (tur === "ROLE") { const ac = deger === "1"; $("kilit").classList.toggle("acik", ac); $("kilit-yazi").textContent = ac ? "AÇIK" : "KİLİTLİ"; hatNotu(t, ac ? "RÖLE AÇILDI (kasa kilidi)" : "Röle kapandı (kilit kilitli)"); }
      else if (tur === "LED") $("led").classList.toggle("acik", deger === "1");
      else if (tur === "BIP") bip(deger === "1");
      else if (tur === "TX") { hatTutucu = hatSatiri("tx", t, deger); koprunun(t, deger, hatTutucu); }
      else if (tur === "RX") hatSatiri("rx", t, deger);
    }
  }

  function ist() {
    if (!modul) return;
    const f = {};
    modul.vx_istatistik().split(" ").forEach((p) => { const [k, v] = p.split("="); f[k] = Number(v); });
    const s = sunucuIst || {};
    const satirlar = [
      ["FİRMWARE", null],
      ["Açılış sayacı (EEPROM)", f.acilis], ["Gönderilen istek", f.gonderilen], ["Yeniden iletim", f.yeniden], ["NAK alındı", f.nak],
      ["Bozuk cevap", f.bozuk], ["Zaman aşımı", f.zaman_asimi], ["EEPROM yazma", f.eeprom_yazma],
      ["Röle değişmezi ihlali", f.ihlal, f.ihlal > 0],
      ["KÖPRÜ (SUNUCU)", null],
      ["İşlenen çerçeve", s.cerceve ?? 0], ["Bozuk çerçeve (NAK)", s.bozuk ?? 0], ["Önbellekten (tekrar)", s.tekrar ?? 0], ["Yoksayılan satır", s.yoksayilan ?? 0],
    ];
    const kutu = $("fl-ist"); kutu.textContent = "";
    for (const [ad, v, kotu] of satirlar) {
      if (v === null) { const a = document.createElement("span"); a.className = "ayrac"; a.textContent = ad; kutu.appendChild(a); continue; }
      const a = document.createElement("span"); a.className = "k"; a.textContent = ad;
      const b = document.createElement("span"); b.className = "v" + (kotu ? " kotu" : ""); b.textContent = String(v ?? 0);
      kutu.append(a, b);
    }
  }

  function eepromKaydet() {
    try { const d = []; for (let i = 0; i < 16; i++) d.push(modul.vx_eeprom_al(i)); localStorage.setItem(EEPROM_ANAHTAR, JSON.stringify(d)); } catch (e) { /* gizli pencere */ }
  }
  function eepromYukle() {
    try { const d = JSON.parse(localStorage.getItem(EEPROM_ANAHTAR) || "null"); if (Array.isArray(d) && d.length === 16) d.forEach((v, i) => modul.vx_eeprom_ver(i, v)); } catch (e) { /* bos */ }
  }
  let tohum = 1;
  function baslat() {
    tohum = (tohum * 7919 + 13) % 100003;
    modul.vx_baslat(tohum, 1000);
    eepromKaydet();
    kuyruk = Promise.resolve();
    hatNotu(0, "Güç verildi (açılış sayacı kalıcı bellekten okundu ve artırıldı).");
    olaylariIsle(modul.vx_cikti());
  }

  function gurultuUygula() {
    const al = (id) => Number($(id).value) / 1000;
    ["bit", "dusur", "ekle"].forEach((k) => { $("g-" + k + "-o").textContent = $("g-" + k).value + "‰"; });
    if (modul) modul.vx_gurultu(al("g-bit"), al("g-dusur"), al("g-ekle"));
  }
  ["g-bit", "g-dusur", "g-ekle"].forEach((id) => $(id).addEventListener("input", gurultuUygula));
  $("b-kayip").addEventListener("click", () => { kayipBayragi = true; $("b-kayip").classList.add("bekliyor"); });
  $("b-kes").addEventListener("click", (e) => { koprukesik = !koprukesik; e.target.setAttribute("aria-pressed", String(koprukesik)); e.target.textContent = koprukesik ? "Köprüyü bağla" : "Köprüyü kes"; hatNotu(simdi(), koprukesik ? "Köprü kesildi: istekler sunucuya gitmiyor." : "Köprü yeniden bağlandı."); });
  $("hiz").addEventListener("change", (e) => { hiz = Number(e.target.value); });
  $("b-baslat").addEventListener("click", () => { if (modul) baslat(); });

  function dongu(zaman) {
    if (modul) {
      const gecen = Math.min(100, (zaman - sonZaman) * hiz);   // ms; sekme arka plandayken sıçrama olmasın
      sonZaman = zaman;
      const n = Math.floor(gecen + (dongu.artik || 0)); dongu.artik = gecen + (dongu.artik || 0) - n;
      if (n > 0) { modul.vx_adim(n); olaylariIsle(modul.vx_cikti()); }
      $("fl-zaman").textContent = (modul.vx_zaman() / 1000).toFixed(1) + " sn";
    }
    requestAnimationFrame(dongu);
  }

  // ---------------------------------------------------------------- sunucu şeridi (defter, hareketler, onaylar)
  async function seritYenile() {
    try {
      const r = await fetch("/simulator/serit/", { credentials: "same-origin" });
      if (!r.ok) return;
      const v = await r.json(), k = $("fl-serit"); k.textContent = "";
      const baslik = (m) => { const d = document.createElement("div"); d.className = "b"; d.textContent = m; k.appendChild(d); };
      const satir = (a, b) => { const d = document.createElement("div"); d.className = "s"; const x = document.createElement("span"), y = document.createElement("span"); x.textContent = a; y.textContent = b; d.append(x, y); k.appendChild(d); };
      baslik("HESAPLAR");
      v.hesaplar.filter((h) => h.tur !== "KASA").slice(0, 4).forEach((h) => satir(`${h.ad} (${h.hesap_no})`, h.bakiye + " TL"));
      baslik("SON HAREKETLER");
      (v.hareketler || []).slice(0, 5).forEach((h) => satir(`#${h.islem} ${h.hesap} ${h.yon}`, h.tutar));
      if (!(v.hareketler || []).length) satir("—", "");
      if ((v.bekleyenler || []).length) { baslik("BEKLEYEN ONAYLAR"); v.bekleyenler.slice(0, 3).forEach((b) => satir(`#${b.talep_id || b.id || ""} ${b.tur || ""}`, `${b.verilen ?? 0}/${b.gereken ?? "?"}`)); }
      baslik("SON LOGLAR");
      (v.loglar || []).slice(0, 5).forEach((l) => satir(l.olay, l.zaman));
    } catch (e) { /* sunucu yok */ }
  }

  // ---------------------------------------------------------------- başlat
  VxModul().then((m) => {
    modul = {
      vx_baslat: m.cwrap("vx_baslat", null, ["number", "number"]), vx_adim: m.cwrap("vx_adim", null, ["number"]),
      vx_tus: m.cwrap("vx_tus", null, ["number"]), vx_kart: m.cwrap("vx_kart", null, ["string"]),
      vx_seri_ver: m.cwrap("vx_seri_ver", null, ["string"]), vx_cikti: m.cwrap("vx_cikti", "string", []),
      vx_istatistik: m.cwrap("vx_istatistik", "string", []), vx_gurultu: m.cwrap("vx_gurultu", null, ["number", "number", "number"]),
      vx_zaman: m.cwrap("vx_zaman", "number", []), vx_eeprom_al: m.cwrap("vx_eeprom_al", "number", ["number"]),
      vx_eeprom_ver: m.cwrap("vx_eeprom_ver", null, ["number", "number"]),
    };
    window.__vx = modul;   // konsoldan deneme ve testler icin
    eepromYukle();
    gurultuUygula();
    baslat();
    sonZaman = performance.now();
    requestAnimationFrame(dongu);
    setInterval(ist, 500);
    setInterval(eepromKaydet, 5000);
    seritYenile(); setInterval(seritYenile, 2500);
  }).catch((e) => { lcdYaz("Firmware yuklenemedi", String(e).slice(0, 16)); });
})();
