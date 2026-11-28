/* ==========================================================================
   Universal Downloader - front end
   ========================================================================== */

const API = {
  token: localStorage.getItem('vd_token') || '',
};

/* ----------------------------------------------------------------- utils -- */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function formatBytes(bytes) {
  if (!bytes || bytes < 0) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let n = bytes, i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${units[i]}`;
}

function formatDuration(seconds) {
  if (!seconds || seconds < 0) return '';
  const s = Math.floor(seconds % 60);
  const m = Math.floor((seconds / 60) % 60);
  const h = Math.floor(seconds / 3600);
  const pad = (n) => String(n).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function formatEta(eta) {
  if (eta === null || eta === undefined) return '—';
  if (eta < 60) return `${Math.round(eta)}s`;
  const m = Math.floor(eta / 60), s = Math.round(eta % 60);
  return `${m}m ${String(s).padStart(2, '0')}s`;
}

function escapeHtml(str) {
  return String(str ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function toast(message, kind = 'info') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.innerHTML = `<span class="toast-dot"></span><span>${escapeHtml(message)}</span>`;
  $('#toasts').appendChild(el);
  setTimeout(() => {
    el.style.transition = 'opacity .3s, transform .3s';
    el.style.opacity = '0';
    el.style.transform = 'translateY(6px)';
    setTimeout(() => el.remove(), 320);
  }, 4200);
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  if (API.token) headers['X-API-Token'] = API.token;

  const response = await fetch(path, { ...options, headers });
  let data = null;
  try { data = await response.json(); } catch (_) { /* non-JSON body */ }

  if (!response.ok) {
    const detail = (data && (data.detail || data.message)) || `Request failed (${response.status})`;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return data;
}

/* -------------------------------------------------------------- app state -- */

const state = {
  analysis: null,
  jobs: new Map(),
  sources: new Map(),   // jobId -> EventSource
};

/* ---------------------------------------------------------------- health -- */

async function loadHealth() {
  try {
    const health = await api('/api/health');
    const yt = $('#pillYt');
    yt.classList.add('ok');
    yt.querySelector('span').textContent = `yt-dlp ${health.yt_dlp_version}`;
    const ff = $('#pillFfmpeg');
    if (health.ffmpeg) {
      ff.classList.add('ok');
      ff.querySelector('span').textContent = 'ffmpeg ready';
    } else {
      ff.classList.add('bad');
      ff.querySelector('span').textContent = 'ffmpeg missing';
    }
    if (health.auth_required && !API.token) {
      toast('This server requires an API token.', 'error');
    }
  } catch (err) {
    $('#pillYt').classList.add('bad');
    $('#pillYt').querySelector('span').textContent = 'backend offline';
  }
}

/* --------------------------------------------------------------- analyze -- */

async function analyzeUrl() {
  const input = $('#urlInput');
  const url = input.value.trim();
  const hint = $('#inputHint');

  if (!url) {
    hint.textContent = 'Please paste a link first.';
    hint.classList.add('error');
    input.focus();
    return;
  }

  const button = $('#analyzeBtn');
  button.classList.add('loading');
  button.disabled = true;
  hint.textContent = 'Inspecting the link...';
  hint.classList.remove('error');

  try {
    const analysis = await api('/api/analyze', {
      method: 'POST',
      body: JSON.stringify({ url }),
    });
    state.analysis = analysis;
    renderAnalysis(analysis);
    hint.textContent = analysis.kind === 'playlist'
      ? `Playlist detected — ${analysis.playlist_count} items found.`
      : 'Ready. Pick a quality and download.';
  } catch (err) {
    hint.textContent = err.message;
    hint.classList.add('error');
    $('#results').innerHTML = '';
    toast(err.message, 'error');
  } finally {
    button.classList.remove('loading');
    button.disabled = false;
  }
}

/* ---------------------------------------------------------- render media -- */

function qualityOptions(qualities) {
  const video = qualities.filter((q) => q.kind !== 'audio');
  const audio = qualities.filter((q) => q.kind === 'audio');
  let html = '';
  if (video.length) {
    html += '<optgroup label="Video">';
    for (const q of video) {
      const size = q.size ? ` · ~${formatBytes(q.size)}` : '';
      html += `<option value="${escapeHtml(q.id)}">${escapeHtml(q.label)}${escapeHtml(size)}</option>`;
    }
    html += '</optgroup>';
  }
  if (audio.length) {
    html += '<optgroup label="Audio only">';
    for (const q of audio) {
      const size = q.size ? ` · ~${formatBytes(q.size)}` : '';
      html += `<option value="${escapeHtml(q.id)}">${escapeHtml(q.label)}${escapeHtml(size)}</option>`;
    }
    html += '</optgroup>';
  }
  return html;
}

function renderAnalysis(analysis) {
  const results = $('#results');
  if (analysis.kind === 'playlist') {
    renderPlaylist(analysis);
    return;
  }

  const badges = [];
  if (analysis.extractor) badges.push(`<span class="badge accent">${escapeHtml(analysis.extractor)}</span>`);
  if (analysis.uploader) badges.push(`<span class="badge">${escapeHtml(analysis.uploader)}</span>`);
  if (analysis.duration) badges.push(`<span class="badge">${formatDuration(analysis.duration)}</span>`);
  if (analysis.is_live) badges.push('<span class="badge live">LIVE</span>');
  badges.push(`<span class="badge">${analysis.formats.length} formats</span>`);

  const thumb = analysis.thumbnail
    ? `<img src="${escapeHtml(analysis.thumbnail)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">`
    : '';

  results.innerHTML = `
    <div class="panel media-card">
      <div class="media-top">
        <div class="media-thumb">${thumb}</div>
        <div class="media-meta">
          <h3>${escapeHtml(analysis.title)}</h3>
          <div class="badges">${badges.join('')}</div>
        </div>
      </div>
      <div class="download-controls">
        <div class="control">
          <label for="qualitySelect">Quality</label>
          <select id="qualitySelect">${qualityOptions(analysis.qualities)}</select>
          <span class="size-note" id="qualityNote">${escapeHtml(analysis.qualities[0]?.hint || '')}</span>
        </div>
        <label class="check"><input type="checkbox" id="subsToggle" /> Embed subtitles</label>
        <button class="primary-btn" id="downloadBtn" type="button">Download</button>
      </div>
    </div>`;

  const select = $('#qualitySelect');
  const note = $('#qualityNote');
  select.addEventListener('change', () => {
    const chosen = analysis.qualities.find((q) => q.id === select.value);
    note.textContent = chosen ? chosen.hint : '';
  });

  $('#downloadBtn').addEventListener('click', async () => {
    const qualityId = select.value;
    const chosen = analysis.qualities.find((q) => q.id === qualityId) || {};
    const mode = chosen.kind === 'audio' ? 'audio' : 'video';
    await startDownload({
      url: analysis.webpage_url || analysis.url,
      plan_id: analysis.plan_id,
      quality_id: qualityId,
      mode,
      subtitles: $('#subsToggle').checked,
    });
  });
}

/* --------------------------------------------------------- render playlist -- */

// A long series can have thousands of episodes. Rendering every row at once
// floods the DOM and keeps every thumbnail in memory, so the list is built in
// pages and grows only when the user asks for more.
const PLAYLIST_PAGE = 200;

let playlistEntries = [];
let playlistSelected = new Set();
let playlistRendered = 0;

function entryRow(entry, index) {
  const thumb = entry.thumbnail
    ? `<img class="entry-thumb" src="${escapeHtml(entry.thumbnail)}" alt="" loading="lazy" decoding="async" onerror="this.style.visibility='hidden'">`
    : '<div class="entry-thumb"></div>';
  return `
    <div class="entry" data-index="${index}">
      <input type="checkbox" class="entry-check" value="${index}" ${playlistSelected.has(index) ? 'checked' : ''} />
      ${thumb}
      <div class="entry-info">
        <div class="entry-title">${escapeHtml(entry.title)}</div>
        <div class="entry-sub">${entry.duration ? formatDuration(entry.duration) : ''}${entry.uploader ? ` · ${escapeHtml(entry.uploader)}` : ''}</div>
      </div>
      <button class="ghost-btn tiny entry-download" type="button" data-index="${index}">Get</button>
    </div>`;
}

function appendPlaylistPage(container, entries) {
  const next = entries.slice(playlistRendered, playlistRendered + PLAYLIST_PAGE);
  if (!next.length) return false;
  container.insertAdjacentHTML('beforeend', next.map((entry, offset) => entryRow(entry, playlistRendered + offset)).join(''));
  playlistRendered += next.length;
  return true;
}

function renderPlaylist(analysis) {
  const results = $('#results');
  playlistEntries = analysis.entries || [];
  playlistSelected = new Set(playlistEntries.map((_, index) => index));
  playlistRendered = 0;

  results.innerHTML = `
    <div class="panel media-card">
      <div class="playlist-head">
        <h3>${escapeHtml(analysis.title)}</h3>
        <p>${analysis.playlist_count} items${analysis.uploader ? ` · ${escapeHtml(analysis.uploader)}` : ''}</p>
      </div>
      <div class="playlist-actions">
        <button class="ghost-btn tiny" id="selectAllBtn" type="button">Select all</button>
        <button class="ghost-btn tiny" id="selectNoneBtn" type="button">Clear</button>
        <button class="primary-btn" id="downloadSelectedBtn" type="button" style="height:34px;padding:0 16px;font-size:13px">
          Download selected
        </button>
      </div>
      <div class="entry-list" id="entryList"></div>
      <div class="playlist-more" id="playlistMore" hidden>
        <button class="ghost-btn tiny" id="showMoreBtn" type="button"></button>
      </div>
    </div>`;

  const list = $('#entryList');
  appendPlaylistPage(list, playlistEntries);

  const more = $('#playlistMore');
  const showMore = $('#showMoreBtn');
  const refreshMore = () => {
    const left = playlistEntries.length - playlistRendered;
    more.hidden = left <= 0;
    showMore.textContent = `Show ${Math.min(left, PLAYLIST_PAGE)} more (${left} left)`;
  };
  refreshMore();

  showMore.addEventListener('click', () => {
    appendPlaylistPage(list, playlistEntries);
    refreshMore();
  });

  // Selection lives outside the DOM, so it survives paging and rendering.
  list.addEventListener('change', (event) => {
    const box = event.target.closest('.entry-check');
    if (!box) return;
    const index = Number(box.value);
    if (box.checked) playlistSelected.add(index);
    else playlistSelected.delete(index);
  });

  $('#selectAllBtn').addEventListener('click', () => {
    playlistSelected = new Set(playlistEntries.map((_, index) => index));
    $$('.entry-check').forEach((box) => { box.checked = true; });
  });
  $('#selectNoneBtn').addEventListener('click', () => {
    playlistSelected = new Set();
    $$('.entry-check').forEach((box) => { box.checked = false; });
  });

  list.addEventListener('click', (event) => {
    const button = event.target.closest('.entry-download');
    if (!button) return;
    const entry = playlistEntries[Number(button.dataset.index)];
    if (entry) analyzeAndQueue(entry.url, entry.title);
  });

  $('#downloadSelectedBtn').addEventListener('click', async () => {
    const selected = [...playlistSelected]
      .sort((a, b) => a - b)
      .map((index) => playlistEntries[index])
      .filter(Boolean);
    if (!selected.length) {
      toast('Select at least one video.', 'info');
      return;
    }
    toast(`Analyzing ${selected.length} item(s)...`, 'info');
    for (const entry of selected) {
      // Sequential keeps the backend from being hammered by many probes.
      // eslint-disable-next-line no-await-in-loop
      await analyzeAndQueue(entry.url, entry.title);
    }
  });
}

/* -------------------------------------------------- analyze + queue item -- */

async function analyzeAndQueue(url, fallbackTitle) {
  try {
    const analysis = await api('/api/analyze', { method: 'POST', body: JSON.stringify({ url }) });
    if (analysis.kind === 'playlist') {
      toast('That link is itself a playlist - opening it instead.', 'info');
      state.analysis = analysis;
      renderAnalysis(analysis);
      return;
    }
    const quality = (analysis.qualities || []).find((q) => q.kind === 'merged' && q.height)
      || (analysis.qualities || []).find((q) => q.id === 'best');
    await startDownload({
      url: analysis.webpage_url || url,
      plan_id: analysis.plan_id,
      quality_id: quality ? quality.id : 'best',
      mode: 'video',
      subtitles: false,
    });
  } catch (err) {
    toast(`${fallbackTitle || url}: ${err.message}`, 'error');
  }
}

/* ----------------------------------------------------------- start a job -- */

async function startDownload(payload) {
  try {
    const job = await api('/api/jobs', { method: 'POST', body: JSON.stringify(payload) });
    state.jobs.set(job.id, job);
    renderJob(job);
    subscribe(job.id);
    toast('Download started.', 'ok');
  } catch (err) {
    toast(err.message, 'error');
  }
}

/* ------------------------------------------------------------- job events -- */

// Progress frames arrive several times a second, so they are coalesced into
// one paint per frame instead of one DOM write per event.
const pendingRenders = new Map();
let renderScheduled = false;

function scheduleJobRender(job) {
  pendingRenders.set(job.id, job);
  if (renderScheduled) return;
  renderScheduled = true;
  const flush = () => {
    renderScheduled = false;
    const jobs = [...pendingRenders.values()];
    pendingRenders.clear();
    for (const pending of jobs) renderJob(pending);
  };
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(flush);
  else setTimeout(flush, 60);
}

function subscribe(jobId) {
  if (state.sources.has(jobId)) return;
  const url = `/api/jobs/${jobId}/events${API.token ? `?token=${encodeURIComponent(API.token)}` : ''}`;
  const source = new EventSource(url);

  source.onmessage = (event) => {
    try {
      const job = JSON.parse(event.data);
      state.jobs.set(job.id, job);
      scheduleJobRender(job);
      if (['completed', 'error', 'cancelled'].includes(job.status)) {
        source.close();
        state.sources.delete(job.id);
      }
    } catch (_) { /* ignore malformed frames */ }
  };

  source.onerror = () => {
    // The stream ended (or the server dropped it) - fall back to polling once.
    source.close();
    state.sources.delete(jobId);
    pollJob(jobId);
  };

  state.sources.set(jobId, source);
}

async function pollJob(jobId) {
  const job = state.jobs.get(jobId);
  if (!job || ['completed', 'error', 'cancelled'].includes(job.status)) return;
  try {
    const fresh = await api(`/api/jobs/${jobId}`);
    state.jobs.set(jobId, fresh);
    scheduleJobRender(fresh);
    if (['completed', 'error', 'cancelled'].includes(fresh.status)) return;
  } catch (_) { /* ignore */ }
  setTimeout(() => pollJob(jobId), 1200);
}

/* ------------------------------------------------------------ render jobs -- */

function jobStats(job) {
  if (job.status === 'downloading') {
    return [
      `${formatBytes(job.downloaded_bytes)} / ${formatBytes(job.total_bytes)}`,
      `${job.speed ? `${formatBytes(job.speed)}/s` : '—'} · ETA ${formatEta(job.eta)}`,
    ];
  }
  if (job.status === 'completed') {
    return [job.filename || 'finished', 'completed'];
  }
  return [job.quality_label || '', job.status];
}

function jobCard(job) {
  const stats = jobStats(job);

  const thumb = job.thumbnail
    ? `<img class="job-thumb" src="${escapeHtml(job.thumbnail)}" alt="" loading="lazy" decoding="async" onerror="this.style.visibility='hidden'">`
    : '<div class="job-thumb"></div>';

  const isLive = ['queued', 'downloading', 'processing'].includes(job.status);
  const actions = [];
  if (isLive) {
    actions.push(`<button class="ghost-btn" data-action="cancel" data-id="${job.id}" title="Cancel">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
    </button>`);
  }
  if (!isLive) {
    actions.push(`<button class="ghost-btn" data-action="remove" data-id="${job.id}" title="Remove from list">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>
    </button>`);
  }

  const saveBtn = job.status === 'completed' && job.has_file ? `
    <button class="save-btn" data-action="save" data-id="${job.id}">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 3v12"/><path d="m7 11 5 5 5-5"/><path d="M4 20h16"/>
      </svg>
      Save to device
    </button>` : '';

  const error = job.error ? `<div class="job-error">${escapeHtml(job.error)}</div>` : '';

  return `
    <div class="job ${job.status}" data-job="${job.id}" data-status="${job.status}">
      <div class="job-head">
        ${thumb}
        <div class="job-info">
          <div class="job-title">${escapeHtml(job.title)}</div>
          <div class="job-stage">${escapeHtml(job.stage || job.status)}</div>
        </div>
        <div class="job-actions">${actions.join('')}</div>
      </div>
      <div class="progress-track"><div class="progress-bar" style="width:${Math.max(2, job.progress)}%"></div></div>
      <div class="job-stats"><span>${escapeHtml(stats[0])}</span><span>${escapeHtml(stats[1])}</span></div>
      ${error}
      ${saveBtn}
    </div>`;
}

// Progress frames arrive several times a second. Replacing the whole card
// each time throws the subtree away and builds it again, so while the status
// stays the same only the few text nodes and the bar width are touched.
function updateJobCard(node, job) {
  const stage = node.querySelector('.job-stage');
  if (stage) stage.textContent = job.stage || job.status;

  const bar = node.querySelector('.progress-bar');
  if (bar) bar.style.width = `${Math.max(2, job.progress)}%`;

  const stats = node.querySelectorAll('.job-stats span');
  const values = jobStats(job);
  if (stats[0]) stats[0].textContent = values[0];
  if (stats[1]) stats[1].textContent = values[1];

  const errorNode = node.querySelector('.job-error');
  if (job.error) {
    if (errorNode) errorNode.textContent = job.error;
    else node.insertAdjacentHTML('beforeend', `<div class="job-error">${escapeHtml(job.error)}</div>`);
  }
}

// Keep the download list bounded: a long-running session should not grow the
// DOM (and the browser's memory) without limit.
const MAX_JOB_CARDS = 100;

function trimJobCards() {
  const list = $('#jobList');
  const cards = $$('.job', list);
  if (cards.length <= MAX_JOB_CARDS) return;
  const live = ['queued', 'downloading', 'processing'];
  let removed = 0;
  for (const card of cards.slice().reverse()) {
    if (removed >= cards.length - MAX_JOB_CARDS) break;
    if (live.includes(card.dataset.status)) continue;
    const id = card.dataset.job;
    card.remove();
    state.jobs.delete(id);
    const source = state.sources.get(id);
    if (source) { source.close(); state.sources.delete(id); }
    removed += 1;
  }
}

function renderJob(job) {
  const list = $('#jobList');
  const empty = $('#jobsEmpty');
  if (empty) empty.remove();

  const existing = list.querySelector(`[data-job="${job.id}"]`);
  if (existing) {
    if (existing.dataset.status === job.status) {
      updateJobCard(existing, job);
    } else {
      existing.outerHTML = jobCard(job);
    }
  } else {
    list.insertAdjacentHTML('afterbegin', jobCard(job));
    trimJobCards();
  }

  $('#jobCounter').textContent = state.jobs.size;
}

/* ------------------------------------------------------------ job actions -- */

document.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-action]');
  if (!button) return;
  const { action, id } = button.dataset;

  if (action === 'cancel') {
    try { await api(`/api/jobs/${id}/cancel`, { method: 'POST' }); toast('Cancelling...', 'info'); }
    catch (err) { toast(err.message, 'error'); }
  } else if (action === 'remove') {
    const source = state.sources.get(id);
    if (source) { source.close(); state.sources.delete(id); }
    state.jobs.delete(id);
    try { await api(`/api/jobs/${id}`, { method: 'DELETE' }); } catch (_) { /* ignore */ }
    const node = $(`[data-job="${id}"]`);
    if (node) node.remove();
    $('#jobCounter').textContent = state.jobs.size;
    if (state.jobs.size === 0) showEmptyJobs();
  } else if (action === 'save') {
    const token = API.token ? `?token=${encodeURIComponent(API.token)}` : '';
    const link = document.createElement('a');
    link.href = `/api/jobs/${id}/file${token}`;
    link.rel = 'noopener';
    document.body.appendChild(link);
    link.click();
    link.remove();
  }
});

function showEmptyJobs() {
  const list = $('#jobList');
  if ($('#jobsEmpty')) return;
  list.innerHTML = `
    <div class="empty-state" id="jobsEmpty">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 3v12"/><path d="m7 11 5 5 5-5"/><path d="M4 20h16"/>
      </svg>
      <p>No downloads yet</p><span>Analyze a link to get started.</span>
    </div>`;
}

/* ------------------------------------------------------------- supported -- */

let siteNames = [];

async function loadSites() {
  try {
    const data = await api('/api/sites');
    siteNames = data.sites || [];
    buildSiteChips();
    $('#sitesBtn').textContent = `${data.count} supported sites`;
  } catch (_) {
    siteNames = [];
  }
}

// There are well over a thousand supported sites. They are built once and
// then filtered by toggling visibility, so typing in the search box does not
// rebuild a thousand nodes on every keystroke.
let siteChips = [];

function buildSiteChips() {
  const list = $('#siteList');
  list.innerHTML = '';
  siteChips = siteNames.map((name) => {
    const chip = document.createElement('span');
    chip.className = 'site-chip';
    chip.textContent = name;
    chip.dataset.lower = name.toLowerCase();
    list.appendChild(chip);
    return chip;
  });
  $('#sitesEmpty').hidden = siteChips.length > 0;
}

function renderSites(filter = '') {
  const needle = filter.trim().toLowerCase();
  let visible = 0;
  for (const chip of siteChips) {
    const match = !needle || chip.dataset.lower.includes(needle);
    chip.hidden = !match;
    if (match) visible += 1;
  }
  $('#sitesEmpty').hidden = visible > 0;
}

/* ------------------------------------------------------------------ init -- */

function init() {
  // Token can arrive as ?token=... (handy when linking a remote instance).
  const params = new URLSearchParams(window.location.search);
  if (params.get('token')) {
    API.token = params.get('token');
    localStorage.setItem('vd_token', API.token);
    params.delete('token');
    const clean = `${window.location.pathname}${params.toString() ? `?${params}` : ''}`;
    window.history.replaceState({}, '', clean);
  }

  $('#analyzeBtn').addEventListener('click', analyzeUrl);
  $('#urlInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') analyzeUrl();
  });

  $('#pasteBtn').addEventListener('click', async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text) { $('#urlInput').value = text.trim(); analyzeUrl(); }
      else toast('Clipboard is empty.', 'info');
    } catch (_) {
      toast('Clipboard access was blocked by the browser.', 'error');
    }
  });

  $('#clearFinishedBtn').addEventListener('click', async () => {
    try {
      const result = await api('/api/clear-finished', { method: 'POST' });
      for (const [id] of state.jobs) {
        const job = state.jobs.get(id);
        if (['completed', 'error', 'cancelled'].includes(job.status)) state.jobs.delete(id);
      }
      $('#jobList').innerHTML = '';
      showEmptyJobs();
      $('#jobCounter').textContent = state.jobs.size;
      toast(`${result.removed} finished item(s) cleared.`, 'ok');
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  $('#sitesBtn').addEventListener('click', () => {
    renderSites('');
    $('#sitesModal').hidden = false;
    $('#sitesSearch').focus();
  });
  $('#closeSitesBtn').addEventListener('click', () => { $('#sitesModal').hidden = true; });
  $('#sitesModal').addEventListener('click', (e) => {
    if (e.target === $('#sitesModal')) $('#sitesModal').hidden = true;
  });
  let siteFilterTimer = null;
  $('#sitesSearch').addEventListener('input', (e) => {
    const value = e.target.value;
    clearTimeout(siteFilterTimer);
    siteFilterTimer = setTimeout(() => renderSites(value), 120);
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') $('#sitesModal').hidden = true;
  });

  loadHealth();
  loadSites();

  // Restore jobs from a previous page load.
  api('/api/jobs')
    .then((data) => {
      (data.jobs || []).forEach((job) => {
        state.jobs.set(job.id, job);
        renderJob(job);
        if (['queued', 'downloading', 'processing'].includes(job.status)) subscribe(job.id);
      });
    })
    .catch(() => { /* backend not ready yet */ });
}

document.addEventListener('DOMContentLoaded', init);
