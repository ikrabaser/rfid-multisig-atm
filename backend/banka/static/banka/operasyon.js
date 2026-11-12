(function () {
  "use strict";
  var kok = document.getElementById("kosu");
  if (!kok) return;
  var DURUM_URL = kok.dataset.durumUrl, JETON_URL = kok.dataset.jetonUrl;
  var AKTIF = { BEKLIYOR: 1, CALISIYOR: 1 };
  var seri = [], bitti = false, sonGecen = -1, CENTRIFUGE_JS = "https://cdn.jsdelivr.net/npm/centrifuge@5.5.3/dist/centrifuge.min.js";

  function el(ad, ozellik, metin) {
    var e = document.createElement(ad);
    if (ozellik) for (var k in ozellik) e.setAttribute(k, ozellik[k]);
    if (metin !== undefined && metin !== null) e.textContent = metin;
    return e;
  }
  function sayi(v, b) { return v === null || v === undefined ? "–" : Number(v).toLocaleString("tr-TR", { maximumFractionDigits: b === undefined ? 1 : b }); }

  // ---------------------------------------------------------------- kartlar
  var KPI = [
    ["islem", "Toplam işlem"], ["islem_sn", "İşlem / sn"], ["basarili", "Başarılı"], ["basarisiz", "Başarısız", "kotu"],
    ["p95_ms", "p95 gecikme (ms)"], ["kuyruk", "ATM kuyruğu"], ["bekleyen_onay", "Bekleyen onay"], ["onay_ort_bekleme_sn", "Ort. onay bekleme (sn)"]
  ];
  function kpiCiz(d) {
    var kap = document.getElementById("kpi");
    kap.textContent = "";
    KPI.forEach(function (k) {
      var kutu = el("div", k[2] ? { "class": k[2] } : null);
      kutu.appendChild(el("b", null, sayi(d[k[0]], k[0] === "islem" || k[0] === "basarili" || k[0] === "basarisiz" ? 0 : 1)));
      kutu.appendChild(el("span", null, k[1]));
      kap.appendChild(kutu);
    });
  }

  // ---------------------------------------------------------------- grafikler (satır içi SVG)
  var GRAFIKLER = [["islem_sn", "İşlem / sn (verim)"], ["kuyruk", "ATM kuyruğu (bekleyen işlem)"], ["p95_ms", "p95 gecikme (ms)"], ["bekleyen_onay", "Onay bekleyen talep"]];
  function svgEl(ad, ozellik) {
    var e = document.createElementNS("http://www.w3.org/2000/svg", ad);
    for (var k in ozellik) e.setAttribute(k, ozellik[k]);
    return e;
  }
  function grafikCiz() {
    var kap = document.getElementById("grafikler");
    kap.textContent = "";
    GRAFIKLER.forEach(function (g) {
      var noktalar = seri.filter(function (s) { return s[g[0]] !== null && s[g[0]] !== undefined; });
      var kutu = el("div", { "class": "grafik" });
      kutu.appendChild(el("h3", null, g[1]));
      var svg = svgEl("svg", { viewBox: "0 0 300 120", preserveAspectRatio: "none", role: "img", "aria-label": g[1] });
      svg.appendChild(svgEl("line", { x1: 28, y1: 100, x2: 296, y2: 100, "class": "eksen" }));
      svg.appendChild(svgEl("line", { x1: 28, y1: 8, x2: 28, y2: 100, "class": "eksen" }));
      if (noktalar.length >= 2) {
        var en = Math.max.apply(null, noktalar.map(function (s) { return s[g[0]]; })) || 1;
        var t0 = noktalar[0].gecen_sn, t1 = noktalar[noktalar.length - 1].gecen_sn || 1, dt = (t1 - t0) || 1;
        var pts = noktalar.map(function (s) {
          return (28 + (s.gecen_sn - t0) / dt * 266).toFixed(1) + "," + (100 - s[g[0]] / en * 88).toFixed(1);
        }).join(" ");
        svg.appendChild(svgEl("polyline", { points: pts, "class": "cizgi" }));
        var t = svgEl("text", { x: 2, y: 14, "class": "yazi" }); t.textContent = sayi(en); svg.appendChild(t);
        var t2 = svgEl("text", { x: 2, y: 100, "class": "yazi" }); t2.textContent = "0"; svg.appendChild(t2);
      }
      kutu.appendChild(svg);
      kap.appendChild(kutu);
    });
  }

  // ---------------------------------------------------------------- akış
  function akisEkle(liste, sira) {
    if (!liste || !liste.length) return;
    var ul = document.getElementById("akis");
    if (ul.firstChild && ul.firstChild.className === "soluk") ul.textContent = "";
    liste.forEach(function (i) {
      var li = el("li", { "class": i.hata ? "k" : "o" });
      li.appendChild(el("span", null, (i.terminal || "—")));
      li.appendChild(el("span", null, i.islem));
      li.appendChild(el("span", null, sayi(i.ms) + " ms"));
      li.appendChild(el("span", null, i.hata ? "✗ " + i.hata : "✓"));
      ul.insertBefore(li, ul.firstChild);
    });
    while (ul.children.length > 60) ul.removeChild(ul.lastChild);
  }

  // ---------------------------------------------------------------- durum
  function ilerlemeCiz(d) {
    var oran = d.planli ? Math.min(100, (d.planli - d.kalan_olay) / d.planli * 100) : 0;
    document.getElementById("ilerleme").style.width = oran.toFixed(0) + "%";
    document.getElementById("ilerleme-yazi").textContent = d.planli ? "Planlanan " + d.planli + " olayın %" + oran.toFixed(0) + "'i işlendi · geçen " + sayi(d.gecen_sn) + " sn" : "";
  }
  function tickUygula(d) {
    if (d.tur !== "tick" || d.gecen_sn === undefined) return;
    if (d.gecen_sn > sonGecen) {
      sonGecen = d.gecen_sn;
      var kopya = {}; for (var k in d) if (k !== "akis") kopya[k] = d[k];
      seri.push(kopya);
      akisEkle(d.akis);
      kpiCiz(d); ilerlemeCiz(d); grafikCiz();
    }
  }
  var ASAMA = { sandbox: "sandbox veritabanı hazırlanıyor…", migrate: "şema kuruluyor…", veri: "sanal müşteriler oluşturuluyor…", yuk: "yük çalışıyor" };
  function durumUygula(d) {
    var r = document.getElementById("durum-rozet");
    r.textContent = d.durum_adi; r.className = "rozet " + d.durum;
    document.getElementById("asama").textContent = AKTIF[d.durum] ? (ASAMA[d.asama] || (d.durum === "BEKLIYOR" ? "kuyrukta, işçi bekleniyor…" : "")) : "";
    var h = document.getElementById("hata"); h.hidden = !d.hata; h.textContent = d.hata || "";
    var f = document.getElementById("iptal-formu"); if (f) f.hidden = !AKTIF[d.durum] || d.iptal_istendi;
    if (d.son_durum && d.son_durum.tur === "tick") tickUygula(d.son_durum);
    if (!AKTIF[d.durum]) bitir(d);
  }
  function bitir(d) {
    if (bitti) return;
    bitti = true;
    if (d.ozet && d.ozet.zaman_serisi) { seri = d.ozet.zaman_serisi; grafikCiz(); }
    if (d.ozet && d.ozet.toplam) {
      var son = seri.length ? seri[seri.length - 1] : {};
      kpiCiz(Object.assign({}, son, { islem: d.ozet.toplam.islem, basarili: d.ozet.toplam.basarili, basarisiz: d.ozet.toplam.basarisiz,
        islem_sn: d.ozet.toplam.islem_sn, p95_ms: son.p95_ms }));
      raporCiz(d.ozet);
    }
    if (d.durum === "TAMAMLANDI") document.getElementById("ilerleme").style.width = "100%";
    if (baglanti) { try { baglanti.disconnect(); } catch (e) {} }
    var c = document.getElementById("canli"); c.textContent = "bitti"; c.className = "canli kucuk soluk";
  }
  function sorgula() {
    if (bitti) return;
    fetch(DURUM_URL, { credentials: "same-origin" }).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) durumUygula(d); })
      .catch(function () {})
      .then(function () { if (!bitti) setTimeout(sorgula, 2000); });
  }

  // ---------------------------------------------------------------- nihai rapor
  function tablo(basliklar, satirlar) {
    var kap = el("div", { "class": "tablo-kaydir" }), t = el("table"), th = el("thead"), tr = el("tr");
    basliklar.forEach(function (b, i) { tr.appendChild(el("th", i ? { "class": "sayi" } : null, b)); });
    th.appendChild(tr); t.appendChild(th);
    var tb = el("tbody");
    satirlar.forEach(function (s) {
      var r = el("tr");
      s.forEach(function (h, i) { r.appendChild(el("td", i ? { "class": "sayi" } : null, h)); });
      tb.appendChild(r);
    });
    t.appendChild(tb); kap.appendChild(t); return kap;
  }
  function bolum(baslik, ...icerik) {
    var s = el("section", { "class": "kart" });
    s.appendChild(el("h2", null, baslik));
    icerik.forEach(function (i) { if (i) s.appendChild(i); });
    return s;
  }
  function par(metin, sinif) { return el("p", sinif ? { "class": sinif } : null, metin); }
  function raporCiz(o) {
    var kap = document.getElementById("rapor");
    kap.textContent = ""; kap.hidden = false;
    var t = o.toplam, bulgular = [];
    var islemSatir = Object.keys(o.islemler).map(function (ad) {
      var i = o.islemler[ad], g = i.gecikme_ms;
      return [ad, sayi(i.basarili, 0), sayi(i.basarisiz, 0), sayi(g.p50), sayi(g.p95), sayi(g.p99), sayi(g.en_yuksek),
        Object.keys(i.hata_kodlari).map(function (k) { return k + " ×" + i.hata_kodlari[k]; }).join(", ")];
    });
    var basliklar = ["İşlem", "Başarılı", "Başarısız", "p50 ms", "p95 ms", "p99 ms", "En yüksek ms", "Hata kodları"];
    kap.appendChild(bolum("Rapor özeti",
      par(sayi(t.islem, 0) + " işlem · " + sayi(t.basarili, 0) + " başarılı · " + sayi(t.basarisiz, 0) + " başarısız · " +
        sayi(t.islem_sn) + " işlem/sn (gerçek " + sayi(o.sure.gercek_sn) + " sn, sanal " + o.sure.sanal_sn + " sn)")));
    kap.appendChild(bolum("İşlem türleri ve gecikme", tablo(basliklar, islemSatir)));
    var k = o.kuyruk.gecikme_ms, ob = o.onay.bekleme_sn;
    kap.appendChild(bolum("Kuyruk ve onay gecikmesi",
      par("ATM kuyruğu: planlanan başlangıç ile gerçek başlangıç farkı — p50 " + sayi(k.p50) + " ms, p95 " + sayi(k.p95) + " ms, p99 " + sayi(k.p99) +
        " ms; en uzun kuyruk " + o.kuyruk.en_uzun_kuyruk + " işlem."),
      par("Onay: " + (Object.keys(o.onay.sonuclar).map(function (a) { return a + " ×" + o.onay.sonuclar[a]; }).join(", ") || "onay gerektiren işlem olmadı") +
        " — bekleme p50 " + sayi(ob.p50, 2) + " sn, p95 " + sayi(ob.p95, 2) + " sn, en uzun " + sayi(ob.en_yuksek, 2) + " sn; sonuçsuz kalan " + o.onay_acik_kalan + ".")));
    kap.appendChild(bolum("Terminaller (nakit kullanımı)", tablo(["Terminal", "İşlem", "Başlangıç nakit", "Kalan nakit"],
      o.terminaller.map(function (x) { return [x.kod, sayi(x.islem, 0), sayi(x.nakit_ilk, 0), sayi(x.nakit_kalan, 0)]; }))));
    var risk = Object.keys(o.risk).map(function (a) { return a + " ×" + o.risk[a]; }).join(", ") || "yok";
    var anom = Object.keys(o.anomali).map(function (a) { return a + " ×" + o.anomali[a]; }).join(", ") || "yok";
    kap.appendChild(bolum("Risk ve bütünlük",
      par("Risk kararları: " + risk), par("Anomali bulguları: " + anom),
      par("Defter mutabakatı: " + (o.butunluk.defter_saglam ? "sağlam" : "BOZUK") + " · hash zinciri: " + (o.butunluk.zincir_saglam ? "sağlam" : "BOZUK"),
        o.butunluk.defter_saglam && o.butunluk.zincir_saglam ? "" : "hata")));
    if (o.istisnalar && o.istisnalar.length) {
      var d = el("details"); d.appendChild(el("summary", null, o.istisnalar.length + " beklenmeyen istisna"));
      o.istisnalar.forEach(function (x) { d.appendChild(par(typeof x === "string" ? x : JSON.stringify(x), "kucuk soluk")); });
      kap.appendChild(bolum("Beklenmeyen hatalar", d));
    }
  }

  // ---------------------------------------------------------------- Centrifugo (isteğe bağlı; sorgulama her zaman yedek)
  var baglanti = null;
  function jetonAl() {
    return fetch(JETON_URL, { credentials: "same-origin" }).then(function (r) { if (!r.ok) throw new Error("jeton"); return r.json(); });
  }
  function canliBaslat() {
    if (kok.dataset.centrifugo !== "1") return;
    var s = document.createElement("script");
    s.src = CENTRIFUGE_JS; s.async = true;
    s.onload = function () {
      jetonAl().then(function (j) {
        var c = document.getElementById("canli");
        baglanti = new window.Centrifuge(j.adres, { token: j.jeton, getToken: function () { return jetonAl().then(function (x) { return x.jeton; }); } });
        baglanti.on("connected", function () { c.textContent = "canlı (Centrifugo)"; c.className = "canli kucuk acik"; });
        baglanti.on("disconnected", function () { c.textContent = "sorgulama (2 sn)"; c.className = "canli kucuk soluk"; });
        baglanti.on("publication", function (ctx) {
          var d = ctx.data || {};
          if (d.tur === "tick") tickUygula(d);
          else if (d.tur === "asama") document.getElementById("asama").textContent = ASAMA[d.asama] || "";
          else if (d.tur === "bitti") setTimeout(sorgula, 0);
        });
        baglanti.connect();
      }).catch(function () { /* sorgulama zaten çalışıyor */ });
    };
    document.head.appendChild(s);
  }

  kpiCiz({}); grafikCiz();
  sorgula();
  canliBaslat();
})();
