/* Presentation-only enhancements. No API calls, no financial actions, no auto-refresh. */
(() => {
  "use strict";
  const search = document.getElementById("cc-terminal-search");
  const count = document.getElementById("cc-terminal-count");
  const table = document.querySelector("#terminaller tbody");
  if (search && count && table) {
    const rows = Array.from(table.querySelectorAll("tr.searchable"));
    const update = () => {
      const query = search.value.trim().toLocaleLowerCase("tr-TR");
      let visible = 0;
      rows.forEach((row) => {
        const matches = row.textContent.toLocaleLowerCase("tr-TR").includes(query);
        row.hidden = !matches;
        if (matches) visible++;
      });
      count.textContent = `${visible} / ${rows.length} terminal`;
    };
    search.addEventListener("input", update);
    update();
    document.addEventListener("keydown", (event) => {
      if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.target?.closest?.("input, textarea, select, [contenteditable]")) return;
      event.preventDefault();
      search.focus();
    });
  }
  const sections = Array.from(document.querySelectorAll("main [id]"));
  const anchors = Array.from(document.querySelectorAll(".subnav a[href^='#']"));
  const activate = (id) => {
    anchors.forEach((anchor) => {
      const current = anchor.getAttribute("href") === `#${id}`;
      anchor.classList.toggle("current", current);
      if (current) anchor.setAttribute("aria-current", "location");
      else anchor.removeAttribute("aria-current");
    });
  };
  if ("IntersectionObserver" in window && anchors.length) {
    const observed = sections.filter((section) => anchors.some((anchor) => anchor.getAttribute("href") === `#${section.id}`));
    const observer = new IntersectionObserver((entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting).sort((a,b) => b.intersectionRatio - a.intersectionRatio);
      if (visible.length) activate(visible[0].target.id);
    }, { rootMargin: "-10% 0px -72% 0px", threshold: [0, .25, .6] });
    observed.forEach((section) => observer.observe(section));
  }
})();
