/**
 * StickyWords — popup.js
 * Fetches live stats from the background service worker and renders them.
 */

const SERVER = 'http://localhost:5000';

const statusPill = document.getElementById('status-pill');
const statusText = document.getElementById('status-text');
const mainContent = document.getElementById('main-content');
const syncBtn = document.getElementById('sync-btn');
const openDash = document.getElementById('open-dash');

openDash.href = SERVER;

function setOnline() {
  statusPill.classList.remove('offline');
  statusText.textContent = 'ONLINE';
}

function setOffline() {
  statusPill.classList.add('offline');
  statusText.textContent = 'OFFLINE';
}

function renderStats(stats, appData) {
  const top     = stats.top_mistakes || [];
  const apps    = appData?.apps || [];

  let html = '';

  // ── Stats grid ──
  html += `
    <div class="stats">
      <div class="stat-cell">
        <div class="stat-label">Corrections</div>
        <div class="stat-val">${stats.total_corrections || 0}</div>
      </div>
      <div class="stat-cell">
        <div class="stat-label">Unique Typos</div>
        <div class="stat-val red">${stats.unique_mistakes || 0}</div>
      </div>
    </div>
  `;

  // ── App breakdown ──
  if (apps.length > 0) {
    html += `<div class="section">
      <div class="section-label">Worst Offender Apps</div>`;
    apps.slice(0, 4).forEach(a => {
      html += `
        <div class="app-row">
          <div class="app-name" title="${esc(a.app)}">${esc(a.app)}</div>
          <div class="bar-wrap"><div class="bar-fill" style="width:${a.pct}%"></div></div>
          <div class="app-pct">${a.pct}%</div>
        </div>`;
    });
    html += `</div>`;
  }

  // ── Top mistakes ──
  if (top.length > 0) {
    html += `<div class="section">
      <div class="section-label">Top Repeat Errors</div>`;
    top.slice(0, 4).forEach(m => {
      html += `
        <div class="mistake-row">
          <div>
            <span class="typo">${esc(m.typo)}</span>
            <span class="arrow">→</span>
            <span class="fix">${esc(m.fix)}</span>
          </div>
          <span class="cnt">${m.count}×</span>
        </div>`;
    });
    html += `</div>`;
  } else {
    html += `<div class="section"><p class="empty">No corrections logged yet. Start typing!</p></div>`;
  }

  mainContent.innerHTML = html;
}

function renderOffline() {
  mainContent.innerHTML = `
    <div class="section">
      <p class="empty">⚠ Cannot reach StickyWords server.<br><br>
      Make sure <code>app.py</code> is running on <strong>localhost:5000</strong>.</p>
    </div>`;
}

async function loadData() {
  syncBtn.textContent = '…';
  try {
    const [statsRes, appRes] = await Promise.all([
      fetch(`${SERVER}/api/stats`,    { signal: AbortSignal.timeout(2000) }),
      fetch(`${SERVER}/api/app_stats`, { signal: AbortSignal.timeout(2000) }),
    ]);

    if (!statsRes.ok) throw new Error('stats failed');

    const stats   = await statsRes.json();
    const appData = appRes.ok ? await appRes.json() : { apps: [] };

    setOnline();
    renderStats(stats, appData);
  } catch {
    setOffline();
    renderOffline();
  } finally {
    syncBtn.textContent = '↺ Sync';
  }
}

syncBtn.addEventListener('click', loadData);
loadData();

function esc(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
