// Content feed view: competitor-grouped cards, keyboard triage, inline notes, trends.
// The view's HTML comes from /feed/* partials; app.js handles the nav switch to #view-feed.
const Feed = (() => {
  const state = { status: 'new', comp: null, current: null, loaded: false };
  const $ = id => document.getElementById(id);
  // Tab HTML fetched ahead of time (each request is a full round trip through Cloudflare, ~1 s),
  // so switching tabs is instant. Any write drops it, since counts and lists may have changed.
  const cache = new Map();
  const post = (url, data) => {
    cache.clear();
    if (window.FC_DEMO) {
      // demo site: triage and notes still play out on the page (nothing is saved); everything else explains
      if (/\/(status|note)$|\/feed\/dismiss$/.test(url)) return Promise.resolve(new Response('{"ok":true}'));
      alert('这是演示站，不能修改数据，也不调用付费接口。\n想用自己的数据和 API，请到 GitHub 自行部署：\nhttps://github.com/tod-zhang/fuck-competitors');
      return new Promise(() => {});  // never settles: the caller just stops
    }
    return fetch(url, { method: 'POST', body: new URLSearchParams(data || {}) }).then(r => { if (state.loaded) prefetch(); return r; });
  };
  const cards = () => [...document.querySelectorAll('#feedRoot .fd-card')];
  const card = id => document.querySelector(`#feedRoot .fd-card[data-id="${id}"]`);

  function query(status) {
    const q = new URLSearchParams({ status: status || state.status });
    if (state.comp) q.set('comp', state.comp);
    return q.toString();
  }
  function prefetch() {
    for (const status of ['new', 'saved', 'declined', 'trends']) {
      const q = query(status);
      if (status === state.status || cache.has(q)) continue;
      cache.set(q, fetch('/feed/list?' + q).then(r => r.text()));
    }
  }
  async function load(opts) {
    Object.assign(state, opts || {});
    const q = query();
    const pending = cache.get(q) || fetch('/feed/list?' + q).then(r => r.text());
    cache.delete(q);  // shown once; the next visit gets a fresh copy from prefetch()
    const html = await pending;
    if (q !== query()) return;  // the user already switched again
    $('feedRoot').innerHTML = html;
    state.loaded = true;
    document.querySelectorAll('.site-row').forEach(r => r.classList.toggle('on', +r.dataset.id === state.comp));
    state.current = null;
    const first = cards()[0];
    if (first) focus(+first.dataset.id);
    prefetch();
  }

  // ---- selection
  function focus(id, e) {
    // clicks on links / buttons / fields inside the card do their own thing; just move the highlight
    state.current = id;
    cards().forEach(c => c.classList.toggle('sel', +c.dataset.id === id));
    const c = card(id);
    if (c && !e) c.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
  // remove a card from the current tab and select its neighbour (inbox-style)
  function drop(id) {
    const list = cards();
    const i = list.findIndex(c => +c.dataset.id === id);
    const c = list[i];
    if (!c) return;
    const next = list[i + 1] || list[i - 1];
    const group = c.closest('.fd-group');
    c.remove();
    bump(state.status, -1);
    const left = group.querySelectorAll('.fd-card').length;
    if (left) group.querySelector('.fd-gcount').textContent = left; else group.remove();
    if (next) focus(+next.dataset.id);
    else { state.current = null; if (!cards().length) $('feedRoot').insertAdjacentHTML('beforeend', '<p class="empty-soft fd-empty">这一栏刷完了。</p>'); }
  }
  function bump(status, d) {
    const el = document.querySelector(`.fd-tab[data-status="${status}"] .fd-cnt`);
    if (el) el.textContent = Math.max(0, +el.textContent + d);
  }

  // ---- triage
  async function save(id) {
    id = id || state.current;
    const c = card(id);
    if (!c) return;
    focus(id);
    await post(`/feed/item/${id}/status`, { status: 'saved' });
    if (state.status === 'saved') return;
    c.classList.add('just-saved');
    c.querySelectorAll('.fd-btns [data-act=save]').forEach(b => b.remove());
    const box = c.querySelector('.fd-notebox');
    box.classList.add('open');
    box.querySelector('textarea').focus();
  }
  async function dismiss(id) {
    id = id || state.current;
    if (!id) return;
    await post(`/feed/item/${id}/status`, { status: 'declined' });
    if (state.status !== 'declined') drop(id);
  }
  async function restore(id) {
    id = id || state.current;
    if (!id || state.status === 'new') return;
    await post(`/feed/item/${id}/status`, { status: 'new' });
    drop(id);
  }
  async function dismissGroup(btn) {
    const group = btn.closest('.fd-group');
    const ids = [...group.querySelectorAll('.fd-card')].map(c => c.dataset.id);
    await post('/feed/dismiss', { ids: ids.join(',') });
    const after = group.nextElementSibling && group.nextElementSibling.querySelector('.fd-card');
    bump(state.status, -ids.length);
    group.remove();
    if (after) focus(+after.dataset.id);
  }

  // ---- notes
  async function saveNote(id) {
    const c = card(id);
    if (!c) return;
    const box = c.querySelector('.fd-notebox');
    await post(`/feed/item/${id}/note`, { note: box.querySelector('textarea').value });
    box.querySelector('.fd-note-state').textContent = '已保存';
    if (c.classList.contains('just-saved')) finishSaved(id);
  }
  function skipNote(id) {
    const c = card(id);
    if (c && c.classList.contains('just-saved')) finishSaved(id);
    else if (c) c.querySelector('textarea').blur();
  }
  // a card saved from the triage tab leaves it once its note is done (or skipped)
  function finishSaved(id) {
    document.activeElement && document.activeElement.blur();
    bump('saved', 1);
    drop(id);
  }
  let noteTimer = null;
  document.addEventListener('input', e => {  // autosave notes on cards already saved (saved tab)
    if (!e.target.matches('#feedRoot .fd-note')) return;
    const c = e.target.closest('.fd-card');
    if (c.classList.contains('just-saved')) return;
    clearTimeout(noteTimer);
    noteTimer = setTimeout(async () => {
      await post(`/feed/item/${c.dataset.id}/note`, { note: e.target.value });
      c.querySelector('.fd-note-state').textContent = '已保存';
    }, 700);
  });

  // ---- AI bits inside a card
  async function loadMatches(details, id) {
    if (!details.open || details.dataset.loaded) return;
    details.dataset.loaded = '1';
    const body = details.querySelector('.fd-more-body');
    if (body.children.length) return;  // rendered with the card already
    body.innerHTML = '<p class="fd-hint">计算中…</p>';
    body.innerHTML = await (await fetch(`/feed/item/${id}/matches`)).text();
  }
  async function loadKeywords(id, el) {
    const c = id ? card(id) : (el && el.closest('.fd-card')) || card(state.current);
    if (!c) return;
    const details = c.querySelector('details[data-kind="keywords"]');
    details.open = true;
    const body = details.querySelector('.fd-more-body');
    body.innerHTML = '<p class="fd-hint">正在向 DataForSEO 查询…</p>';
    body.innerHTML = await (await post(`/feed/item/${c.dataset.id}/keywords`)).text();
  }

  // ---- header actions
  async function refresh(btn) {
    btn.disabled = true;
    const data = await (await post('/feed/refresh')).json();
    btn.textContent = `已开始巡检 ${data.count} 个竞品`;
  }

  // a competitor picked in the sidebar or the top search: its articles in the feed (null = all)
  function filterCompetitor(id) {
    showView('feed');
    load(id ? { comp: id, status: 'new' } : { comp: null });
    document.querySelector('.main').scrollTo(0, 0);  // .main is the scroll container inside the framed window
    const box = $('compSearch');
    if (box) { box.value = ''; box.blur(); searchCompetitors(''); }
  }
  function searchCompetitors(q) {
    const menu = $('compSearchMenu');
    if (!menu) return;
    q = q.trim().toLowerCase();
    let shown = 0;
    menu.querySelectorAll('li[data-id]').forEach(li => {
      const hit = q && li.dataset.text.includes(q);
      li.hidden = !hit;
      if (hit) shown++;
    });
    menu.querySelector('.search-none').hidden = !q || shown > 0;
    menu.classList.toggle('open', !!q);
  }
  function searchKey(e) {
    if (e.key === 'Escape') { e.target.value = ''; searchCompetitors(''); e.target.blur(); }
    if (e.key === 'Enter') {
      const first = [...document.querySelectorAll('#compSearchMenu li[data-id]')].find(li => !li.hidden);
      if (first) filterCompetitor(+first.dataset.id);
    }
  }

  // ---- my sites: the current one is ?site= in the URL (app.js adds it to every fetch)
  async function addSite(btn) {
    const [nameInput, domainInput] = btn.parentElement.querySelectorAll('input');
    const name = nameInput.value.trim(), domain = domainInput.value.trim();
    if (!name && !domain) return nameInput.focus();
    btn.disabled = true;
    const resp = await post('/sites', { name, domain });
    if (!resp.ok) { btn.disabled = false; return alert('添加失败（' + resp.status + '）'); }
    location.href = '/?site=' + (await resp.json()).id;  // straight into the new, empty site
  }
  async function deleteSite(id, name) {
    if (!confirm(`删除站点「${name}」？它的竞品、巡检记录、Feed 文章和同步的博客文章都会一起删除，无法恢复。`)) return;
    const data = await (await post(`/sites/${id}/delete`)).json();
    if (data.error) return alert(data.error);
    if (String(id) === document.body.dataset.site) location.href = '/';
    else location.reload();
  }

  // ---- settings (API keys + my sites): its own view, rendered server-side into index.html
  function openSettings() {
    document.body.classList.remove('first-run');  // a new site has no competitors yet
    showView('settings');
    document.querySelector('.main').scrollTo(0, 0);
  }
  async function saveSettings(form, quiet) {
    const resp = await post('/feed/config', new FormData(form));
    if (!resp.ok) { $('cfgState').textContent = '保存失败（' + resp.status + '）'; return false; }
    form.querySelectorAll('input[type=password]').forEach(i => { i.value = ''; });
    if (!quiet) {
      // re-render so saved secrets show as "已设置 ··· last4"
      $('view-settings').innerHTML = await (await fetch('/feed/config')).text();
      $('cfgState').textContent = '已保存';
      if (state.loaded) load();
    }
    return true;
  }
  async function testService(service, btn) {
    const msg = document.querySelector(`.fd-test-msg[data-service="${service}"]`);
    btn.disabled = true;
    msg.className = 'fd-hint fd-test-msg';
    msg.textContent = '保存并测试中…';
    if (!(await saveSettings($('feedConfig'), true))) { btn.disabled = false; return; }
    const data = await (await post('/feed/config/test/' + service)).json();
    msg.textContent = data.message;
    msg.classList.add(data.ok ? 'ok' : 'bad');
    btn.disabled = false;
  }
  async function syncBlog() {
    if (!(await saveSettings($('feedConfig'), true))) return;
    const data = await (await post('/feed/sync-my-blog')).json();
    $('syncState').textContent = data.error || '同步中…（后台抓取和嵌入，稍后刷新页面查看）';
  }

  // ---- trends
  async function refreshTrend(cid, btn) {
    btn.disabled = true;
    btn.textContent = '生成中…';
    const html = await (await post('/feed/strategy/' + cid)).text();
    btn.closest('.fd-trend').outerHTML = html;
  }
  async function generateTrends(all, btn) {
    btn.disabled = true;
    const data = await (await post('/feed/trends/generate', { all })).json();
    if (data.error) { btn.disabled = false; return alert(data.error); }
    pollTrends();
  }
  function pollTrends() {
    setTimeout(async () => {
      if (state.status !== 'trends') return;
      await load();
      const s = $('trendsState');
      if (s && /^(生成中|排队中)/.test(s.textContent)) pollTrends();
    }, 4000);
  }

  // ---- modal (competitor suggestions)
  function openModal(html) { $('feedModal').innerHTML = html; $('feedScrim').classList.add('open'); }
  function closeModal() { $('feedScrim').classList.remove('open'); }
  const modalOpen = () => $('feedScrim') && $('feedScrim').classList.contains('open');
  async function openSuggestions(refresh) {
    openModal('<p class="fd-hint">正在查询 DataForSEO 自然搜索竞品…</p>');
    $('feedModal').innerHTML = await (await fetch('/feed/suggestions' + (refresh ? '?refresh=1' : ''))).text();
  }
  async function ignoreSuggestion(domain, btn) {
    btn.disabled = true;
    await post('/feed/suggestions/ignore', { domain });
    btn.closest('tr').remove();
  }
  async function follow(domain, btn) {
    btn.disabled = true;
    btn.textContent = '找 sitemap…';
    const data = await (await post('/feed/suggestions/follow', { domain })).json();
    if (data.ok) btn.parentElement.replaceChildren(Object.assign(document.createElement('span'), { className: 'fd-hint', textContent: '已关注 · ' + data.sitemap }));
    else { btn.disabled = false; btn.textContent = '关注'; alert(data.error); }
  }

  // ---- keys: Enter / Esc in a note box, Esc closes the modal
  document.addEventListener('keydown', e => {
    const view = $('view-feed');
    if (!view || !view.classList.contains('show') || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.target.matches('#feedRoot .fd-note')) {
      const id = +e.target.closest('.fd-card').dataset.id;
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveNote(id); }
      else if (e.key === 'Escape') { e.preventDefault(); skipNote(id); }
      return;
    }
    if (modalOpen() && e.key === 'Escape') closeModal();
  });

  // load lazily the first time the view is opened
  document.querySelectorAll('.nav a[data-view="feed"]').forEach(a => a.addEventListener('click', () => { if (!state.loaded) load(); }));
  if ($('view-feed') && $('view-feed').classList.contains('show')) load();

  return { load, focus, save, dismiss, restore, dismissGroup, saveNote, skipNote, loadMatches, loadKeywords,
           refresh, filterCompetitor, searchCompetitors, searchKey, addSite, deleteSite, openSettings, saveSettings, testService, syncBlog, refreshTrend, generateTrends, openSuggestions,
           follow, ignoreSuggestion, closeModal };
})();
