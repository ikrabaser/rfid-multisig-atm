(() => {
  'use strict';
  const nav = document.getElementById('primary-nav');
  const toggle = document.querySelector('.nav-menu-toggle');
  toggle?.addEventListener('click', () => {
    const open = nav?.classList.toggle('open') ?? false;
    toggle.setAttribute('aria-expanded', String(open));
  });
  nav?.querySelectorAll('a').forEach((link) => link.addEventListener('click', () => {
    nav.classList.remove('open');
    toggle?.setAttribute('aria-expanded', 'false');
  }));
  document.getElementById('refresh-v3')?.addEventListener('click', () => window.location.reload());
  const search = document.getElementById('terminal-search-v3');
  const cards = [...document.querySelectorAll('#terminal-grid-v3 .terminal-tile')];
  const count = document.getElementById('terminal-count-v3');
  const empty = document.getElementById('terminal-empty-v3');
  search?.addEventListener('input', () => {
    const term = search.value.toLocaleLowerCase('tr-TR').trim();
    let visible = 0;
    cards.forEach((card) => {
      const value = (card.dataset.search || '').toLocaleLowerCase('tr-TR');
      const show = value.includes(term);
      card.hidden = !show;
      if (show) visible += 1;
    });
    if (count) count.textContent = `${visible} / ${cards.length} terminal`;
    if (empty) empty.hidden = visible !== 0 || cards.length === 0;
  });
  // Nav highlights are purely local; no polling or synthetic device health checks.
  const sectionLinks = [...(nav?.querySelectorAll('a[href^="#"]') ?? [])];
  sectionLinks.forEach(link => link.addEventListener('click', () => {
    sectionLinks.forEach(other => other.classList.remove('active'));
    link.classList.add('active');
  }));
})();
