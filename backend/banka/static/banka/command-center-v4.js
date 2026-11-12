(() => {
  'use strict';
  const menu = document.getElementById('menu-button-v4');
  const nav = document.getElementById('primary-nav');
  menu?.addEventListener('click', () => {
    const open = nav?.classList.toggle('open') || false;
    menu.setAttribute('aria-expanded', String(open));
  });
  nav?.querySelectorAll('a').forEach((link) => link.addEventListener('click', () => {
    nav.classList.remove('open');
    menu?.setAttribute('aria-expanded', 'false');
  }));
  document.getElementById('refresh-v4')?.addEventListener('click', () => location.reload());
  const search = document.getElementById('terminal-search-v4');
  const rows = [...document.querySelectorAll('#terminal-list-v4 .terminal-row')];
  const count = document.getElementById('terminal-count-v4');
  const empty = document.getElementById('terminal-empty-v4');
  const run = () => {
    const query = search?.value.trim().toLocaleLowerCase('tr-TR') || '';
    let visible = 0;
    rows.forEach((row) => {
      const matches = (row.dataset.search || '').toLocaleLowerCase('tr-TR').includes(query);
      row.hidden = !matches;
      if (matches) visible++;
    });
    if (count) count.textContent = `${visible} / ${rows.length} kayıt`;
    if (empty) empty.hidden = visible !== 0 || rows.length === 0;
  };
  search?.addEventListener('input', run);
  run();
})();
