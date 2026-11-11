(() => {
  "use strict";
  const sidebar = document.getElementById("sidebar");
  const toggle = document.querySelector(".menu-toggle");
  toggle?.addEventListener("click", () => {
    const open = sidebar.classList.toggle("open");
    toggle.setAttribute("aria-expanded", String(open));
  });
  sidebar?.querySelectorAll("a").forEach(link => link.addEventListener("click", () => {
    sidebar.classList.remove("open");
    toggle?.setAttribute("aria-expanded", "false");
  }));
  document.getElementById("refresh")?.addEventListener("click", () => {
    window.location.reload();
  });
})();
