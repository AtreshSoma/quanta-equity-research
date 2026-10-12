/* Dashboard presentation only. Data fetching, scoring and trading state stay
   in app.js; these controls only disclose content already rendered there. */
(() => {
  'use strict';

  function selectTab(group, selected, focus = false) {
    group.querySelectorAll(':scope > [role="tablist"] > [role="tab"]').forEach(tab => {
      const active = tab === selected;
      tab.classList.toggle('is-active', active);
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
      const panel = document.getElementById(tab.getAttribute('aria-controls'));
      if (panel) panel.hidden = !active;
    });
    if (focus) selected.focus();
  }

  document.querySelectorAll('[data-dashboard-tabs]').forEach(group => {
    const tabs = [...group.querySelectorAll(':scope > [role="tablist"] > [role="tab"]')];
    tabs.forEach((tab, index) => {
      tab.addEventListener('click', () => selectTab(group, tab));
      tab.addEventListener('keydown', event => {
        const next = { ArrowRight: (index + 1) % tabs.length, ArrowLeft: (index + tabs.length - 1) % tabs.length, Home: 0, End: tabs.length - 1 }[event.key];
        if (next === undefined) return;
        event.preventDefault();
        selectTab(group, tabs[next], true);
      });
    });
  });

  // Existing jump links must reveal a tabbed target before scrolling to it.
  document.addEventListener('click', event => {
    const link = event.target.closest('[data-scroll-to]');
    const target = link && document.getElementById(link.dataset.scrollTo);
    const panel = target?.closest('[role="tabpanel"]');
    const group = panel?.closest('[data-dashboard-tabs]');
    if (group) selectTab(group, document.getElementById(panel.getAttribute('aria-labelledby')));
  }, true);

  const opportunities = document.getElementById('portfolio-scan-body');
  if (!opportunities) return;
  function summarizeRows() {
    const table = opportunities.querySelector('table');
    if (!table || table.dataset.summarized) return;
    table.dataset.summarized = 'true';
    const rows = [...table.querySelectorAll('tbody > .q-tr')];
    if (rows.length <= 5) return;
    const more = document.createElement('button');
    more.type = 'button';
    more.className = 'q-more';
    more.setAttribute('aria-controls', 'portfolio-scan-body');
    function apply(expanded) {
      opportunities.dataset.expanded = String(expanded);
      rows.slice(5).forEach(row => {
        row.hidden = !expanded;
        if (!expanded) {
          row.classList.remove('is-open');
          row.setAttribute('aria-expanded', 'false');
          if (row.nextElementSibling?.classList.contains('q-detail')) row.nextElementSibling.hidden = true;
        }
      });
      more.setAttribute('aria-expanded', String(expanded));
      more.textContent = expanded ? 'Show top 5 candidates' : `View all ${rows.length} candidates`;
    }
    more.addEventListener('click', () => apply(opportunities.dataset.expanded !== 'true'));
    apply(opportunities.dataset.expanded === 'true');
    opportunities.append(more);
  }
  new MutationObserver(summarizeRows).observe(opportunities, { childList: true });
  summarizeRows();
})();
