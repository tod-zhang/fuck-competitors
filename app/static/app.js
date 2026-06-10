// view switching (client-side; all four views are rendered server-side into the page)
document.querySelectorAll('.nav a[data-view]').forEach(a => {
  a.addEventListener('click', () => {
    document.querySelectorAll('.nav a').forEach(x => x.classList.remove('active'));
    a.classList.add('active');
    const v = a.dataset.view;
    document.querySelectorAll('.view').forEach(s => s.classList.toggle('show', s.id === 'view-' + v));
    if (v === 'timeline') resetTimelineFilter();
  });
});

// competitor filter is driven by the sidebar (no competitor chips in the bar); held here.
let currentComp = null;

// reset the changelog filters to "all competitors + all types"
function resetTimelineFilter() {
  currentComp = null;
  const scope = document.getElementById('tlScope');
  if (scope) scope.textContent = '全部';
  document.querySelectorAll('#view-timeline .fchip[data-type]').forEach(c => c.classList.add('on'));
  applyTimelineFilter();
}

// add-modal chips (visual selection only)
document.querySelectorAll('.chip').forEach(c => c.addEventListener('click', () => c.classList.toggle('on')));

// timeline filtering: by competitor (sidebar selection) AND by change type (toggle chips), combined.
function applyTimelineFilter() {
  const comp = currentComp;
  const types = new Set([...document.querySelectorAll('#view-timeline .fchip[data-type].on')].map(c => c.dataset.type));

  document.querySelectorAll('#view-timeline .entry').forEach(e => {
    const compOk = !comp || e.dataset.comp === comp;
    let anyRow = false;
    e.querySelectorAll('.change').forEach(row => {
      const show = compOk && types.has(row.dataset.cat);
      row.style.display = show ? '' : 'none';
      if (show) anyRow = true;
    });
    // keep the entry-head summary tags consistent with the visible rows
    e.querySelectorAll('.summary-tags .tag[data-cat]').forEach(t => {
      t.style.display = types.has(t.dataset.cat) ? '' : 'none';
    });
    e.style.display = (compOk && anyRow) ? '' : 'none';
  });
  document.querySelectorAll('#view-timeline .day-group').forEach(g => {
    const anyVisible = [...g.querySelectorAll('.entry')].some(e => e.style.display !== 'none');
    g.style.display = anyVisible ? '' : 'none';
  });
}
function showView(view) {
  document.querySelectorAll('.nav a').forEach(x => x.classList.toggle('active', x.dataset.view === view));
  document.querySelectorAll('.view').forEach(s => s.classList.toggle('show', s.id === 'view-' + view));
}
// clicking a competitor in the sidebar: jump to the changelog filtered to it
function filterCompetitor(name) {
  currentComp = name;
  const scope = document.getElementById('tlScope');
  if (scope) scope.textContent = name || '全部';
  showView('timeline');
  applyTimelineFilter();
  window.scrollTo(0, 0);
}
// change-type chips toggle which types are shown
document.querySelectorAll('.fchip[data-type]').forEach(c => c.addEventListener('click', () => {
  c.classList.toggle('on');
  applyTimelineFilter();
}));

function openModal() { document.getElementById('scrim').classList.add('open'); }
function closeModal() { document.getElementById('scrim').classList.remove('open'); }

// per-competitor settings modal: load the form on demand, then show
async function openSettings(competitorId) {
  const resp = await fetch('/competitors/' + competitorId + '/settings');
  if (!resp.ok) return;
  document.getElementById('settingsContent').innerHTML = await resp.text();
  document.getElementById('settingsScrim').classList.add('open');
}
function closeSettings() { document.getElementById('settingsScrim').classList.remove('open'); }

// change-detail drawer: load a server-rendered partial, then slide in
async function openDetail(changeId) {
  const resp = await fetch('/changes/' + changeId);
  if (!resp.ok) return;
  document.getElementById('drawerContent').innerHTML = await resp.text();
  document.getElementById('drawer').classList.add('open');
  document.getElementById('drawerScrim').classList.add('open');
}
function closeDrawer() {
  document.getElementById('drawer').classList.remove('open');
  document.getElementById('drawerScrim').classList.remove('open');
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { closeDrawer(); closeModal(); closeSettings(); }
});
