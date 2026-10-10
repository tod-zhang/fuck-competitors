// view switching (client-side; the views are rendered server-side into the page)
document.querySelectorAll('.nav a[data-view]').forEach(a => a.addEventListener('click', () => showView(a.dataset.view)));

// /#settings (after adding / switching / deleting a site from the settings page) reopens it
if (location.hash === '#settings') {
  document.body.classList.remove('first-run');
  showView('settings');
}

// add-modal chips (visual selection only)
document.querySelectorAll('.chip').forEach(c => c.addEventListener('click', () => c.classList.toggle('on')));

function showView(view) {
  document.querySelectorAll('.nav a').forEach(x => x.classList.toggle('active', x.dataset.view === view));
  document.querySelector('.side-settings').classList.toggle('active', view === 'settings');
  document.querySelectorAll('.view').forEach(s => s.classList.toggle('show', s.id === 'view-' + view));
}
// custom dropdown (.xselect) — delegated so it also works in the on-demand settings modal
document.addEventListener('click', e => {
  const trigger = e.target.closest('.xselect-trigger');
  const item = e.target.closest('.xselect-menu li');
  // close any open dropdown the click landed outside of
  document.querySelectorAll('.xselect.open').forEach(x => { if (!x.contains(e.target)) x.classList.remove('open'); });
  if (trigger) { trigger.closest('.xselect').classList.toggle('open'); return; }
  if (item) {
    const xs = item.closest('.xselect');
    xs.querySelector('input[type=hidden]').value = item.dataset.value;
    xs.querySelector('.xselect-label').textContent = item.textContent;
    xs.querySelectorAll('.xselect-menu li').forEach(li => li.classList.toggle('sel', li === item));
    xs.classList.remove('open');
  }
});

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

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    document.querySelectorAll('.xselect.open').forEach(x => x.classList.remove('open'));
    closeModal(); closeSettings();
  }
});

// demo instance: every form is for show — explain instead of submitting (the server rejects writes anyway)
if (window.FC_DEMO) {
  const demoNotice = () => alert('这是演示站，不能添加或修改数据。\n想监控自己的竞品，请到 GitHub 自行部署：\nhttps://github.com/tod-zhang/fuck-competitors');
  document.addEventListener('submit', e => { e.preventDefault(); demoNotice(); }, true);
}
