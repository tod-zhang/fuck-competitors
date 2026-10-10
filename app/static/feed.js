// Content feed view: competitor-grouped cards, keyboard triage, inline notes, trends.
// The view's HTML comes from /feed/* partials; app.js handles the nav switch to #view-feed.
const Feed = (() => {
  const state = { status: 'new', window: 7, comp: null, current: null, loaded: false };
  const $ = id => document.getElementById(id);
  const post = (url, data) => {
    if (window.FC_DEMO) {
      // demo site: triage and notes still play out on the page (nothing is saved); everything else explains
      if (/\/(status|note)$|\/feed\/dismiss$/.test(url)) return Promise.resolve(new Response('{"ok":true}'));
      alert('这是演示站，不能修改数据，也不调用付费接口。\n想用自己的数据和 API，请到 GitHub 自行部署：\nhttps://github.com/tod-zhang/fuck-competitors');
      return new Promise(() => {});  // never settles: the caller just stops
    }
    return fetch(url, { method: 'POST', body: new URLSearchParams(data || {}) });
  };
  const cards = () => [...document.querySelectorAll('#feedRoot .fd-card')];
  const card = id => document.querySelector(`#feedRoot .fd-card[data-id="${id}"]`);

  async function load(opts) {
    Object.assign(state, opts || {});
    const q = new URLSearchParams({ status: state.status, window: state.window });
    if (state.comp) q.set('comp', state.comp);
    const resp = await fetch('/feed/list?' + q);
    $('feedRoot').innerHTML = await resp.text();
    state.loaded = true;
    state.current = null;
    const first = cards()[0];
    if (first) focus(+first.dataset.id);
  }

  // ---- selection
  function focus(id, e) {
    // clicks on links / buttons / fields inside the card do their own thing; just move the highlight
    state.current = id;
    cards().forEach(c => c.classList.toggle('sel', +c.dataset.id === id));
    const c = card(id);
    if (c && !e) c.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
  function move(delta) {
    const list = cards();
    if (!list.length) return;
    const i = list.findIndex(c => +c.dataset.id === state.current);
    const next = list[Math.max(0, Math.min(list.length - 1, i < 0 ? 0 : i + delta))];
    focus(+next.dataset.id);
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
    c.querySelectorAll('.fd-btns .btn').forEach(b => { if (b.textContent.includes('保存')) b.remove(); });
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
  function toggleHelp() { const h = $('feedHelp'); if (h) h.classList.toggle('open'); }

  // ---- settings (API keys + my site), in the modal
  async function openSettings() {
    openModal('<p class="fd-hint">加载中…</p>');
    $('feedModal').innerHTML = await (await fetch('/feed/config')).text();
  }
  async function saveSettings(form, quiet) {
    const resp = await post('/feed/config', new FormData(form));
    if (!resp.ok) { $('cfgState').textContent = '保存失败（' + resp.status + '）'; return false; }
    form.querySelectorAll('input[type=password]').forEach(i => { i.value = ''; });
    if (!quiet) { closeModal(); if (state.loaded) load(); }
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
    $('syncState').textContent = data.error || '同步中…（后台抓取和嵌入，稍后再打开设置查看）';
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
  async function follow(domain, btn) {
    btn.disabled = true;
    btn.textContent = '找 sitemap…';
    const data = await (await post('/feed/suggestions/follow', { domain })).json();
    if (data.ok) btn.replaceWith(Object.assign(document.createElement('span'), { className: 'fd-hint', textContent: '已关注 · ' + data.sitemap }));
    else { btn.disabled = false; btn.textContent = '关注'; alert(data.error); }
  }

  // ---- hotkeys (only while the feed view is showing)
  document.addEventListener('keydown', e => {
    const view = $('view-feed');
    if (!view || !view.classList.contains('show') || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.target.matches('#feedRoot .fd-note')) {
      const id = +e.target.closest('.fd-card').dataset.id;
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveNote(id); }
      else if (e.key === 'Escape') { e.preventDefault(); skipNote(id); }
      return;
    }
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) {
      if (e.key === 'Escape') e.target.blur();
      return;
    }
    if (modalOpen()) { if (e.key === 'Escape') closeModal(); return; }
    const c = card(state.current);
    const actions = {
      j: () => move(1), ArrowDown: () => move(1), k: () => move(-1), ArrowUp: () => move(-1),
      s: () => save(), d: () => dismiss(), u: () => restore(),
      n: () => { if (c) { c.querySelector('.fd-notebox').classList.add('open'); c.querySelector('.fd-note').focus(); } },
      o: () => c && window.open(c.dataset.url, '_blank', 'noopener'),
      m: () => { if (c) { const d = c.querySelector('details[data-kind="matches"]'); d.open = !d.open; } },
      w: () => c && !c.querySelector('details[data-kind="keywords"] table') && loadKeywords(state.current),
      '?': () => toggleHelp(),
    };
    const fn = actions[e.key] || actions[e.key.toLowerCase()];
    if (fn) { e.preventDefault(); fn(); }
  });

  // load lazily the first time the view is opened
  document.querySelectorAll('.nav a[data-view="feed"]').forEach(a => a.addEventListener('click', () => { if (!state.loaded) load(); }));
  if ($('view-feed') && $('view-feed').classList.contains('show')) load();

  return { load, focus, save, dismiss, restore, dismissGroup, saveNote, skipNote, loadMatches, loadKeywords,
           refresh, toggleHelp, openSettings, saveSettings, testService, syncBlog, refreshTrend, generateTrends, openSuggestions,
           follow, closeModal };
})();
