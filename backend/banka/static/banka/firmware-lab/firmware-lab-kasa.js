/* Firmware Laboratuvarı: onay panosu (oylar, üst rol, röle süresi) ve kasa açma senaryo otopilotu.
   Terminali SÜRMEZ taklit etmez: otopilot yalnızca gerçek firmware'e kart okutur ve tuşa basar (FL kancaları), geri kalanı
   firmware, köprü ve sunucu yapar. Pano, sunucunun onay kayıtlarını salt okunur gösterir. */
(function () {
  "use strict";
  const FL = window.FL;
  if (!FL) return;
  const $ = (id) => document.getElementById(id);
  const VERI = FL.VERI;
  const yetkili = (rol) => (VERI.yetkililer || []).find((y) => y.rol === rol && y.ipucu && !y.bloke);
  const el = (ad, sinif, metin) => { const e = document.createElement(ad); if (sinif) e.className = sinif; if (metin != null) e.textContent = metin; return e; };

  // ------------------------------------------------------------------ röle süresi
  const role = { acik: false, t0: 0, sure: null };
  const rolSatiri = $("fl-role"), rolCubuk = $("fl-role-cubuk"), rolYazi = $("fl-role-yazi");
  FL.rolePaylas = (ac, t) => {
    if (ac) { role.acik = true; role.t0 = t; role.sure = null; }
    else { role.acik = false; role.sure = t - role.t0; }
    rolGuncelle();
  };
  function rolGuncelle() {
    if (!rolSatiri) return;
    let gecen = role.sure != null ? role.sure : (role.acik ? FL.simdi() - role.t0 : 0);
    rolSatiri.classList.toggle("acik", role.acik);
    rolCubuk.style.width = Math.min(100, (gecen / 5000) * 100) + "%";
    rolYazi.textContent = role.acik ? "açık · " + (gecen / 1000).toFixed(1) + " / 5.0 sn"
      : role.sure != null ? "son açılış: " + role.sure + " ms (hedef 5000)" : "kapalı";
  }
  setInterval(rolGuncelle, 100);

  // ------------------------------------------------------------------ onay panosu
  const pano = $("fl-talepler");
  let sonYanit = 0, sonListe = [];
  const DURUM = { BEKLIYOR: "bekliyor", ONAYLANDI: "onaylandı", UYGULANDI: "uygulandı", REDDEDILDI: "reddedildi", SURESI_DOLDU: "süresi doldu", BASARISIZ: "başarısız" };
  const TUR = { KASA_ACMA: "Kasa açma", CEKME: "Para çekme", YATIRMA: "Para yatırma", HAVALE: "Havale" };
  function panoCiz(liste) {
    pano.textContent = ""; sonListe = liste;
    if (!liste.length) { pano.appendChild(el("div", "ince", "Henüz onay talebi yok. Bir yetkili kartıyla “Kasa aç” (A) başlat ya da soldan senaryo çalıştır.")); return; }
    liste.forEach((t) => {
      const k = el("div", "fl-talep " + t.durum.toLowerCase());
      const baslik = el("div", "fl-talep-bas");
      baslik.appendChild(el("b", null, "Talep #" + t.talep_id + " · " + (TUR[t.tur] || t.tur)));
      baslik.appendChild(el("span", "rozet " + (t.durum === "UYGULANDI" ? "ok" : t.durum === "BEKLIYOR" ? "uyari" : "kotu"), DURUM[t.durum] || t.durum));
      k.appendChild(baslik);
      const noktalar = el("div", "fl-noktalar");
      for (let i = 0; i < t.gereken; i++) noktalar.appendChild(el("span", "nokta" + (i < t.verilen ? " dolu" : "")));
      noktalar.appendChild(el("span", "ince", t.verilen + "/" + t.gereken + " onay · en az rol: " + t.en_az_rol));
      k.appendChild(noktalar);
      if (t.ust_yetkili_gerekli) k.appendChild(el("div", "fl-uyari", "Oy sayısı tamam ama onaylayanlar arasında “" + t.en_az_rol + "” yok: kilit AÇILMAZ."));
      if (t.durum === "BEKLIYOR") { const s = el("div", "ince", "Kalan süre: " + t.kalan_sn + " sn"); s.dataset.talep = t.talep_id; s.dataset.kalan = t.kalan_sn; k.appendChild(s); }
      const oylar = el("ol", "fl-oylar");
      t.oylar.forEach((o) => {
        const li = el("li");
        li.appendChild(el("span", null, o.ad));
        li.appendChild(el("span", "ince", o.rol));
        li.appendChild(el("span", "rozet " + (o.karar === "ONAY" ? "ok" : "kotu"), o.karar === "ONAY" ? "onay" : "red"));
        li.appendChild(el("span", "ince", o.saat));
        oylar.appendChild(li);
      });
      if (t.oylar.length) k.appendChild(oylar);
      pano.appendChild(k);
    });
    sonYanit = Date.now();
  }
  async function panoYenile() {
    if (document.hidden) return;
    try {
      const r = await fetch("/firmware-lab/talepler/", { credentials: "same-origin" });
      if (r.ok) panoCiz((await r.json()).talepler);
    } catch (_) { /* ağ yok: eski görüntü kalır */ }
  }
  setInterval(panoYenile, 2000);
  panoYenile();
  setInterval(() => {        // iki yenileme arasında geri sayım akıcı aksın
    pano.querySelectorAll("[data-kalan]").forEach((e) => {
      const kalan = Math.max(0, Number(e.dataset.kalan) - Math.floor((Date.now() - sonYanit) / 1000));
      e.textContent = "Kalan süre: " + kalan + " sn";
    });
  }, 1000);

  // ------------------------------------------------------------------ otopilot
  const gunluk = $("fl-oto-gunluk"), dugmeler = Array.from(document.querySelectorAll("[data-senaryo]"));
  let calisiyor = false;
  const not = (m, tur) => { const s = el("div", tur || ""); s.textContent = m; gunluk.appendChild(s); gunluk.scrollTop = gunluk.scrollHeight; };
  const kisaAd = (p) => p.ad.split(" (")[0];
  const lcdIcerir = (m) => () => FL.lcd().includes(m);
  async function bekleLcd(m, ms, adim) {
    if (!(await FL.bekle(lcdIcerir(m), ms))) throw new Error(adim + ": ekranda “" + m + "” görünmedi (ekran: " + FL.lcd().replace("|", " / ") + ")");
  }
  async function yaz(metin) { for (const c of metin) { FL.tus(c); await FL.uyu(250); } }

  async function kasaAcmayiBaslat(p) {
    not("1) " + kisaAd(p) + " kartını okutuyor…");
    FL.kartOkut(p.uid);
    await bekleLcd("Talep yok", 12000, "Talep başlatma");
    FL.tus("A"); await FL.uyu(400);
    not("   A: Kasa aç → PIN " + p.ipucu);
    await yaz(p.ipucu);
    await bekleLcd("Onay 0/", 12000, "Talep açılışı");
  }
  async function onayVer(p, sira) {
    not(sira + ") " + kisaAd(p) + " (" + p.rol + ") kartını okutuyor → A: Onayla → PIN");
    FL.kartOkut(p.uid);
    await bekleLcd("A:Onayla", 15000, p.ad + " onayı");
    FL.tus("A"); await FL.uyu(400);
    await yaz(p.ipucu);
    await bekleLcd("Oyunuz alindi", 15000, p.ad + " oyu");
    await FL.uyu(1300);
  }
  const SENARYO = {
    // Üç onay var ama müdür yok: kilit açılmamalı; müdür onaylayınca açılmalı
    tam: async () => {
      const mudur = yetkili("Müdür");
      const gor = (VERI.yetkililer || []).filter((y) => y.rol === "Görevli" && y.ipucu && !y.bloke);
      const ydkler = (VERI.yetkililer || []).filter((y) => y.rol === "Müdür yardımcısı" && y.ipucu && !y.bloke);
      if (gor.length < 2 || ydkler.length < 2 || !mudur) throw new Error("Demo yetkilileri (2 görevli, 2 müdür yrd., 1 müdür) ve PIN ipuçları gerekli: seed_demo çalıştır.");
      await kasaAcmayiBaslat(ydkler[0]);
      await onayVer(gor[0], 2); await onayVer(gor[1], 3); await onayVer(ydkler[1], 4);
      await bekleLcd("Ust rol gerekli", 10000, "Üst rol kontrolü");
      not("→ 3 onay tamam ama müdür yok: röle kapalı, ekran “Ust rol gerekli”.", "vurgu");
      if (role.acik) throw new Error("Röle müdür onayı olmadan açıldı!");
      await onayVer(mudur, 5);
      await bekleLcd("Kasa acildi", 15000, "Kasa kilidi");
      not("→ Müdür onayladı: kilit açıldı, röle 5 sn tutulacak.", "vurgu");
      if (!(await FL.bekle(() => !role.acik && role.sure != null, 9000))) throw new Error("Röle kapanmadı");
      not("→ Röle " + role.sure + " ms açık kaldı ve kapandı. Defter ve olay şeridine bak.", "vurgu");
    },
    // Yalnızca görevli ve müdür yrd. oyları: yeterli sayıda ama yeterli rütbe yok -> açılmaz
    musdursuz: async () => {
      const gor = (VERI.yetkililer || []).filter((y) => y.rol === "Görevli" && y.ipucu && !y.bloke);
      const ydkler = (VERI.yetkililer || []).filter((y) => y.rol === "Müdür yardımcısı" && y.ipucu && !y.bloke);
      if (gor.length < 2 || ydkler.length < 2) throw new Error("Demo yetkilileri ve PIN ipuçları gerekli: seed_demo çalıştır.");
      await kasaAcmayiBaslat(ydkler[0]);
      await onayVer(gor[0], 2); await onayVer(gor[1], 3); await onayVer(ydkler[1], 4);
      await bekleLcd("Ust rol gerekli", 10000, "Üst rol kontrolü");
      await FL.uyu(8000);
      if (role.acik || role.sure != null) throw new Error("Röle açıldı: üst rol kuralı çalışmadı!");
      not("→ 8 sn sonra bile röle hiç açılmadı: yeterli rütbe olmadan kasa açılmaz.", "vurgu");
    },
  };
  dugmeler.forEach((b) => b.addEventListener("click", async () => {
    if (calisiyor) return;
    if (!FL.modul()) { not("Firmware henüz yüklenmedi.", "kotu"); return; }
    calisiyor = true; dugmeler.forEach((d) => { d.disabled = true; });
    gunluk.textContent = "";
    await panoYenile();
    const acik = sonListe.find((t) => t.durum === "BEKLIYOR");
    if (acik) {          // terminal bekleyen talebi görür ("A:Onayla") ve yeni talep açtırmaz: senaryo kendi talebini kuramaz
      not("Zaten bekleyen bir talep var (#" + acik.talep_id + ", " + acik.kalan_sn + " sn kaldı). Süresi dolunca ya da sonuçlanınca tekrar dene.", "kotu");
      calisiyor = false; dugmeler.forEach((d) => { d.disabled = false; });
      return;
    }
    const hizSecim = $("hiz"), eskiHiz = hizSecim.value;
    hizSecim.value = "10"; hizSecim.dispatchEvent(new Event("change"));
    role.sure = null;
    try {
      await SENARYO[b.dataset.senaryo]();
      not("Senaryo tamamlandı.", "ok");
    } catch (e) {
      not("Durdu: " + e.message, "kotu");
    } finally {
      hizSecim.value = eskiHiz; hizSecim.dispatchEvent(new Event("change"));
      calisiyor = false; dugmeler.forEach((d) => { d.disabled = false; });
      panoYenile();
    }
  }));
  if (!(VERI.yetkililer || []).some((y) => y.ipucu)) {
    dugmeler.forEach((d) => { d.disabled = true; });
    not("PIN ipuçları kapalı (üretim benzeri kurulum): otopilot kullanılamaz. Kartları elle okutabilirsin.");
  }
})();
