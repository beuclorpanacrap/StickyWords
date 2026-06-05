/**
 * StickyWords — background.js (service worker)
 *
 * Responsibilities:
 *  - Proxy all localhost fetches on behalf of content scripts.
 *    Content scripts run in the page's origin context, so Chrome's
 *    Private Network Access policy blocks their direct calls to
 *    http://localhost from HTTPS pages. The service worker runs in
 *    the extension's own origin and is permitted by host_permissions.
 *  - Maintain a session correction counter shown as a badge.
 *  - Poll the local server every 30s to update the badge with real stats.
 */

const SERVER = 'http://localhost:5000';

// ── Badge helpers ─────────────────────────────────────────

function setBadge(text, color = '#ef4444') {
  chrome.action.setBadgeText({ text: String(text || '') });
  chrome.action.setBadgeBackgroundColor({ color });
}

function clearBadge() {
  chrome.action.setBadgeText({ text: '' });
}

// ── Fetch stats from server ────────────────────────────────

async function refreshBadge() {
  try {
    const res  = await fetch(`${SERVER}/api/stats`, { signal: AbortSignal.timeout(2000) });
    if (!res.ok) { clearBadge(); return; }
    const data = await res.json();
    const count = data.total_corrections || 0;
    setBadge(count > 999 ? '999+' : count > 0 ? String(count) : '');
  } catch {
    clearBadge();
  }
}

refreshBadge();
setInterval(refreshBadge, 30_000);

// ── Message handling ──────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {

  // ── Proxied prediction request from content script ──────
  // Content scripts can't call localhost directly from HTTPS pages
  // (Chrome Private Network Access policy). They send a message here
  // and we do the fetch from the extension origin instead.
  if (msg.type === 'PREDICT') {
    fetch(`${SERVER}/predict`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ word: msg.word }),
      signal:  AbortSignal.timeout(1500),
    })
      .then(r => r.ok ? r.json() : { suggestions: [] })
      .then(data => sendResponse({ ok: true, suggestions: data.suggestions || [] }))
      .catch(() => sendResponse({ ok: false, suggestions: [] }));
    return true; // keep channel open for async response
  }

  // ── Proxied learn request from content script ────────────
  if (msg.type === 'LEARN') {
    fetch(`${SERVER}/learn`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ typed: msg.typed, selected: msg.selected, app: msg.app }),
      signal:  AbortSignal.timeout(1000),
    }).catch(() => {});
    // fire-and-forget, no response needed
    return false;
  }

  if (msg.type === 'GET_STATS') {
    fetch(`${SERVER}/api/stats`, { signal: AbortSignal.timeout(2000) })
      .then(r => r.json())
      .then(data => sendResponse({ ok: true, data }))
      .catch(() => sendResponse({ ok: false }));
    return true;
  }

  if (msg.type === 'GET_APP_STATS') {
    fetch(`${SERVER}/api/app_stats`, { signal: AbortSignal.timeout(2000) })
      .then(r => r.json())
      .then(data => sendResponse({ ok: true, data }))
      .catch(() => sendResponse({ ok: false }));
    return true;
  }

  if (msg.type === 'CORRECTION_MADE') {
    refreshBadge();
    sendResponse({ ok: true });
  }
});

// ── Installation ──────────────────────────────────────────

chrome.runtime.onInstalled.addListener(({ reason }) => {
  if (reason === 'install') {
    console.log('[StickyWords] Installed. Make sure your Flask server is running on localhost:5000.');
  }
});