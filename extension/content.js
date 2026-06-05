/**
 * StickyWords — content.js
 *
 * Intercepts typing everywhere: <input>, <textarea>, contentEditable
 * Proxies fetch via background.js to bypass CORS/Private Network errors.
 */

(() => {
  'use strict';

  console.log("StickyWords: Content Script Loaded!");

  const DEBOUNCE_MS = 180;
  const MIN_LEN     = 2;
  const MAX_SUGGEST = 4;
  const RETRY_AFTER = 10_000;

  let overlay     = null;
  let ghostEl     = null;
  let activeEl    = null;
  let activeWord  = '';
  let suggestions = [];
  let selIdx      = 0;
  let debTimer    = null;
  let ghostSuffix = '';
  let serverOk    = true;
  let fetchGen    = 0;

  // ── Build overlay ─────────────────────────────────────────

  function buildOverlay() {
    if (overlay) return;
    overlay = document.createElement('div');
    overlay.id = 'sw-overlay';
    overlay.innerHTML = `
      <div class="sw-hdr">
        <span class="sw-logo">SW</span>
        <span class="sw-brand">StickyWords</span>
      </div>
      <div class="sw-rows" id="sw-rows"></div>
      <div class="sw-foot">
        <kbd>Tab</kbd> accept &nbsp;·&nbsp;
        <kbd>↑↓</kbd> cycle &nbsp;·&nbsp;
        <kbd>Esc</kbd> dismiss
      </div>
    `;
    overlay.addEventListener('mousedown', e => e.preventDefault());
    document.body.appendChild(overlay);
    console.log("StickyWords: Overlay built and injected.");
  }

  function buildGhost() {
    if (ghostEl) return;
    ghostEl = document.createElement('span');
    ghostEl.id = 'sw-ghost';
    document.body.appendChild(ghostEl);
  }

  // FORCE BUILD IMMEDIATELY (Fixes ReferenceError)
  buildOverlay();
  buildGhost();

  // ── Caret position ────────────────────────────────────────

  function getCaretCoords(el) {
    if (isContentEditable(el)) {
      const sel = window.getSelection();
      if (sel && sel.rangeCount > 0) {
        const range = sel.getRangeAt(0).cloneRange();
        range.collapse(true);
        const rects = range.getClientRects();
        if (rects.length) {
          return { top: rects[0].top, left: rects[0].left, height: rects[0].height };
        }
      }
      const r = el.getBoundingClientRect();
      return { top: r.bottom, left: r.left, height: 0 };
    }

    const elRect = el.getBoundingClientRect();
    const cs     = window.getComputedStyle(el);

    const mirror = document.createElement('div');
    mirror.style.cssText = [
      `position:fixed`, `top:${elRect.top}px`, `left:${elRect.left}px`,
      `width:${elRect.width}px`, `height:${elRect.height}px`,
      `font-family:${cs.fontFamily}`, `font-size:${cs.fontSize}`,
      `font-weight:${cs.fontWeight}`, `line-height:${cs.lineHeight}`,
      `letter-spacing:${cs.letterSpacing}`, `padding-top:${cs.paddingTop}`,
      `padding-right:${cs.paddingRight}`, `padding-bottom:${cs.paddingBottom}`,
      `padding-left:${cs.paddingLeft}`, `overflow:hidden`,
      `white-space:pre-wrap`, `word-wrap:break-word`, `word-break:break-word`,
      `visibility:hidden`, `pointer-events:none`, `z-index:-1`,
      `color:transparent`, `background:transparent`,
      `border:${cs.border}`, `box-sizing:${cs.boxSizing}`,
    ].join(';');

    const cur    = typeof el.selectionEnd === 'number' ? el.selectionEnd : (el.value || '').length;
    const text   = (el.value || '').substring(0, cur);
    const anchor = document.createElement('span');
    anchor.textContent = '\u200b';

    mirror.appendChild(document.createTextNode(text));
    mirror.appendChild(anchor);
    document.body.appendChild(mirror);

    mirror.scrollTop  = el.scrollTop;
    mirror.scrollLeft = el.scrollLeft;

    const anchorRect = anchor.getBoundingClientRect();
    document.body.removeChild(mirror);

    return {
      top:    anchorRect.top,
      left:   anchorRect.left,
      height: anchorRect.height || parseFloat(cs.lineHeight) || 18,
    };
  }

  // ── Overlay positioning ───────────────────────────────────

  function positionOverlay(el) {
    if (!overlay) return;

    const coords = getCaretCoords(el);
    const GAP    = 6;
    const vw     = window.innerWidth;
    const vh     = window.innerHeight;

    overlay.style.visibility = 'hidden';
    overlay.style.display    = 'block';

    const ow = overlay.offsetWidth  || 300;
    const oh = overlay.offsetHeight || 160;

    let top  = coords.top + coords.height + GAP;
    let left = coords.left;

    if (top + oh > vh - 8) top = coords.top - oh - GAP;
    if (left + ow > vw - 8) left = vw - ow - 8;
    if (left < 8)            left = 8;

    overlay.style.top        = `${top}px`;
    overlay.style.left       = `${left}px`;
    overlay.style.visibility = 'visible';
  }

  // ── Ghost text ────────────────────────────────────────────

  function showGhost(el, suffix) {
    ghostSuffix = suffix;
    if (!suffix || isContentEditable(el)) { hideGhost(); return; }

    buildGhost();
    const coords = getCaretCoords(el);
    const cs     = window.getComputedStyle(el);

    // ADD THIS: Match the line-height adjustment here too
    const isInput = el.tagName === 'INPUT';
    const adjustedLineHeight = isInput 
      ? `${el.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom)}px` 
      : cs.lineHeight;

    ghostEl.style.cssText = [
      'position:fixed',
      `top:${coords.top}px`,
      `left:${coords.left}px`,
      `font-family:${cs.fontFamily}`,
      `font-size:${cs.fontSize}`,
      `font-weight:${cs.fontWeight}`,       // <-- ADDED
      `letter-spacing:${cs.letterSpacing}`, // <-- ADDED
      `line-height:${adjustedLineHeight}`,  // <-- UPDATED
      'white-space:pre',
      'pointer-events:none',
      'z-index:2147483646',
    ].join(';');
    
    ghostEl.textContent = suffix;
    ghostEl.style.display = 'inline';
  }

  function hideGhost() {
    ghostSuffix = '';
    if (ghostEl) ghostEl.style.display = 'none';
  }

  // ── Render rows ───────────────────────────────────────────

  function renderRows(words) {
    const container = document.getElementById('sw-rows');
    if (!container) return;
    container.innerHTML = '';
    words.forEach((word, i) => {
      const row = document.createElement('div');
      row.className = 'sw-row' + (i === 0 ? ' sw-row--top' : '');
      row.dataset.idx = i;
      row.innerHTML = `<span class="sw-num">${i + 1}</span><span class="sw-word">${esc(word)}</span>`;
      row.addEventListener('mouseenter', () => { selIdx = i; highlight(); });
      row.addEventListener('click',      () => accept(i));
      container.appendChild(row);
    });
    highlight();
  }

  function highlight() {
    if (!overlay) return;
    overlay.querySelectorAll('.sw-row').forEach((r, i) => {
      r.classList.toggle('sw-row--sel', i === selIdx);
    });
  }

  function showOverlay(el) {
    if (!overlay) return;
    overlay.style.display = 'block';
    positionOverlay(el);
  }

  function hideOverlay() {
    if (overlay) overlay.style.display = 'none';
    hideGhost();
    suggestions = [];
    activeWord  = '';
    selIdx      = 0;
  }

  function onScrollOrResize() {
    if (overlay && overlay.style.display === 'block' && activeEl) positionOverlay(activeEl);
    if (ghostEl && ghostEl.style.display !== 'none' && activeEl) showGhost(activeEl, ghostSuffix);
  }
  window.addEventListener('scroll', onScrollOrResize, true);
  window.addEventListener('resize', onScrollOrResize);

  // ── Server calls ──────────────────────────────────────────

  async function fetchPredictions(word) {
    if (!serverOk) return [];
    return new Promise(resolve => {
      try {
        chrome.runtime.sendMessage({ type: 'PREDICT', word }, response => {
          if (chrome.runtime.lastError || !response || !response.ok) {
            serverOk = false;
            setTimeout(() => { serverOk = true; }, RETRY_AFTER);
            resolve([]);
          } else {
            serverOk = true;
            resolve(response.suggestions || []);
          }
        });
      } catch {
        serverOk = false;
        setTimeout(() => { serverOk = true; }, RETRY_AFTER);
        resolve([]);
      }
    });
  }

  function postLearn(typed, selected) {
    if (!serverOk) return;
    const app = document.title ? document.title.split(' - ').at(-1).trim() : window.location.hostname;
    try {
      chrome.runtime.sendMessage({ type: 'LEARN', typed, selected, app });
    } catch {}
  }

  // ── Word extraction / replacement ─────────────────────────

  function getWord(el) {
    if (isContentEditable(el)) {
      const sel = window.getSelection();
      if (!sel || !sel.rangeCount) return '';
      const node = sel.getRangeAt(0).startContainer;
      if (node.nodeType !== Node.TEXT_NODE) return '';
      const text = node.textContent.substring(0, sel.getRangeAt(0).startOffset);
      return (text.match(/[a-zA-Z'-]+$/) || [''])[0];
    }
    const val = el.value || '';
    const cur = el.selectionEnd || 0;
    return (val.substring(0, cur).match(/[a-zA-Z'-]+$/) || [''])[0];
  }

  function replaceWord(el, replacement) {
    if (isContentEditable(el)) {
      const sel = window.getSelection();
      if (!sel || !sel.rangeCount) return;
      const range = sel.getRangeAt(0);
      const node  = range.startContainer;
      if (node.nodeType !== Node.TEXT_NODE) return;
      const end   = range.startOffset;
      const start = node.textContent.substring(0, end).search(/[a-zA-Z'-]+$/);
      if (start === -1) return;
      node.textContent =
        node.textContent.substring(0, start) + replacement + ' ' +
        node.textContent.substring(end);
      const nr = document.createRange();
      nr.setStart(node, start + replacement.length + 1);
      nr.collapse(true);
      sel.removeAllRanges();
      sel.addRange(nr);
      el.dispatchEvent(new InputEvent('input', { bubbles: true }));
      return;
    }
    const val   = el.value || '';
    const cur   = el.selectionEnd || 0;
    const start = val.substring(0, cur).search(/[a-zA-Z'-]+$/);
    if (start === -1) return;
    el.value = val.substring(0, start) + replacement + ' ' + val.substring(cur);
    const pos = start + replacement.length + 1;
    el.setSelectionRange(pos, pos);
    el.dispatchEvent(new InputEvent('input', { bubbles: true }));
  }

  // ── Core pipeline ─────────────────────────────────────────

  async function handleInput(el) {
    const word  = getWord(el);
    activeWord  = word;
    const myGen = ++fetchGen;

    if (!word || word.length < MIN_LEN) { hideOverlay(); return; }

    const words = await fetchPredictions(word);

    if (fetchGen !== myGen) return;
    if (getWord(el) !== word) return;
    if (!words.length) { hideOverlay(); return; }

    suggestions = words;
    selIdx      = 0;

    const top = words[0].toLowerCase();
    const lw  = word.toLowerCase();
    if (top.startsWith(lw) && top !== lw) {
      showGhost(el, words[0].substring(word.length));
    } else {
      hideGhost();
    }

    renderRows(words.slice(0, MAX_SUGGEST));
    showOverlay(el);
  }

  function accept(idx) {
    if (!suggestions[idx] || !activeEl) return;
    clearTimeout(debTimer);
    debTimer = null;
    fetchGen++;

    const chosen = suggestions[idx];
    const typed  = activeWord;
    replaceWord(activeEl, chosen);
    postLearn(typed, chosen);
    try { chrome.runtime.sendMessage({ type: 'CORRECTION_MADE' }); } catch {}
    hideOverlay();
    activeEl.focus();
  }

  function onKeyDown(e) {
    const visible = overlay && overlay.style.display === 'block';

    if (e.key === 'Tab' && (ghostSuffix || (visible && suggestions.length))) {
      e.preventDefault();
      e.stopPropagation();
      accept(selIdx);
      return;
    }

    if (!visible) return;

    if (e.key === 'Escape')    { e.stopPropagation(); hideOverlay(); return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); selIdx = (selIdx + 1) % suggestions.length; highlight(); return; }
    if (e.key === 'ArrowUp')   { e.preventDefault(); selIdx = (selIdx - 1 + suggestions.length) % suggestions.length; highlight(); return; }

    if (['1','2','3','4'].includes(e.key)) {
      const idx = parseInt(e.key, 10) - 1;
      if (idx < suggestions.length) { e.preventDefault(); accept(idx); return; }
    }

    if (e.key === ' ' || e.key === 'Enter') hideOverlay();
  }

  // ── Element attachment ────────────────────────────────────

  function attach(el) {
    if (el._sw) return;
    el._sw = true;
    
    console.log("StickyWords: Attaching to element:", el);

    el.addEventListener('input', () => {
      clearTimeout(debTimer);
      debTimer = setTimeout(() => handleInput(el), DEBOUNCE_MS);
    });

    el.addEventListener('keydown', e => {
      activeEl = el;
      onKeyDown(e);
    }, true);

    el.addEventListener('focus', () => { activeEl = el; });
    el.addEventListener('blur',  () => { setTimeout(hideOverlay, 150); });
  }

  function isEditable(el) {
    if (!el || el.id === 'sw-overlay' || el.id === 'sw-ghost') return false;
    
    // Skip visually hidden elements (Crucial for Google's complex DOM)
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;

    if (el.tagName === 'INPUT') {
      return ['text','search','email','url','tel',''].includes((el.type || '').toLowerCase());
    }
    return el.tagName === 'TEXTAREA' || isContentEditable(el);
  }

  function isContentEditable(el) {
    return !!(el && (el.isContentEditable || el.getAttribute?.('contenteditable') === 'true'));
  }

  function scan() {
    document.querySelectorAll(
      'input[type=text],input[type=search],input[type=email],input[type=url],' +
      'input:not([type]),textarea,[contenteditable=true]'
    ).forEach(el => { if (isEditable(el)) attach(el); });
  }

  new MutationObserver(muts => {
    for (const m of muts) {
      for (const node of m.addedNodes) {
        if (node.nodeType !== 1) continue;
        if (isEditable(node)) attach(node);
        node.querySelectorAll?.('input,textarea,[contenteditable=true]')
            .forEach(el => { if (isEditable(el)) attach(el); });
      }
    }
  }).observe(document.body, { childList: true, subtree: true });

  document.addEventListener('focusin', e => {
    if (isEditable(e.target)) { attach(e.target); activeEl = e.target; }
  });

  document.addEventListener('click', e => {
    if (overlay && !overlay.contains(e.target) && e.target !== activeEl) hideOverlay();
  });

  // Initial Scan
  scan();

  // Re-scan when history changes (fixes Single Page Apps like Google)
  window.addEventListener('popstate', () => {
      console.log("StickyWords: URL changed, re-scanning...");
      scan();
  });
  window.addEventListener('load', () => setTimeout(scan, 1500));

  function esc(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

})();