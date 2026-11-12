(() => {
  "use strict";

  const nav = document.getElementById("main-nav");
  const menu = document.getElementById("menu-toggle");

  menu?.addEventListener("click", () => {
    const open = nav.classList.toggle("open");
    menu.setAttribute("aria-expanded", String(open));
  });

  nav?.querySelectorAll("a").forEach(link => {
    link.addEventListener("click", () => {
      nav.classList.remove("open");
      menu?.setAttribute("aria-expanded", "false");
    });
  });

  document.getElementById("refresh-v42")?.addEventListener(
    "click", () => window.location.reload()
  );

  // Terminal aramasi
  const search = document.getElementById("terminal-search-v42");
  const units = Array.from(document.querySelectorAll(".terminal-unit"));
  const count = document.getElementById("terminal-count-v42");
  const empty = document.getElementById("terminal-empty-v42");

  search?.addEventListener("input", () => {
    const q = search.value.toLocaleLowerCase("tr-TR").trim();
    let visible = 0;

    units.forEach(unit => {
      const value = (unit.dataset.search || "")
        .toLocaleLowerCase("tr-TR");

      const matches = value.includes(q);
      unit.hidden = !matches;
      if (matches) visible++;
    });

    if (count) count.textContent = `${visible} / ${units.length} kayıt`;
    if (empty) empty.hidden = visible !== 0 || units.length === 0;
  });

  // Kaydedilmis islemleri filtrele
  const buttons = document.querySelectorAll("[data-filter]");
  const transactions = Array.from(document.querySelectorAll(".ledger-row"));
  const ledgerEmpty = document.getElementById("ledger-empty-v42");

  buttons.forEach(button => {
    button.addEventListener("click", () => {
      const selected = button.dataset.filter;
      let visible = 0;

      buttons.forEach(b => {
        const active = b === button;
        b.classList.toggle("active", active);
        b.setAttribute("aria-pressed", String(active));
      });

      transactions.forEach(row => {
        const matches =
          selected === "all" || row.dataset.kind === selected;

        row.hidden = !matches;
        if (matches) visible++;
      });

      if (ledgerEmpty) {
        ledgerEmpty.hidden = visible !== 0 || transactions.length === 0;
      }
    });
  });
})();