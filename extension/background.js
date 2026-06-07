const SERVER = 'http://localhost:5000';

function setBadge(text, color = '#ef4444') {
  chrome.action.setBadgeText({ text: String(text || '') });
  chrome.action.setBadgeBackgroundColor({ color });
}

function clearBadge() {
  chrome.action.setBadgeText({ text: '' });
}

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

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {

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
    return true; 

  if (msg.type === 'LEARN') {
    fetch(`${SERVER}/learn`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ typed: msg.typed, selected: msg.selected, app: msg.app }),
      signal:  AbortSignal.timeout(1000),
    }).catch(() => {});
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

chrome.runtime.onInstalled.addListener(({ reason }) => {
  if (reason === 'install') {
    console.log('[StickyWords] Installed. Make sure your Flask server is running on localhost:5000.');
  }
});