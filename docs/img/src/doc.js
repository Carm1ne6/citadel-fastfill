// Builds a document figure from a small config. Markers and red boxes are placed on the
// document's own words (pages/sec_words.js, from build_pages.py), so a phrase that is not
// on the page, word for word, stops the render instead of being drawn somewhere.
(function () {
  const errors = (window.__errors = []);
  const fail = (m) => { errors.push(m); console.error(m); };

  // marker and box geometry, in units of the font size (body text is Times 12 pt)
  const MARK_TOP = 0.80, MARK_BOT = 0.25, MARK_PADX = 1.4;   // pt
  const BOX_TOP = 0.92, BOX_BOT = 0.36, BOX_PADX = 2.0, BOX_PADY = 1.6; // pt

  const norm = (s) => s
    .replace(/[‘’]/g, "'")
    .replace(/[“”]/g, '"')
    .replace(/[–—]/g, '-');

  // find a phrase on a page; returns [{x0,x1,base,size,line}] per matched word
  function findPhrase(page, spec) {
    const phrase = typeof spec === 'string' ? spec : spec.p;
    const nth = (spec && spec.nth) || 0;
    const words = (window.SEC_WORDS[page] || []).filter((w) => !w.sup);
    const toks = norm(phrase).trim().split(/\s+/);
    const hits = [];
    for (let i = 0; i + toks.length <= words.length; i++) {
      const got = [];
      let ok = true;
      for (let k = 0; k < toks.length && ok; k++) {
        const w = words[i + k], t = toks[k];
        const wt = norm(w.t), wc = norm(w.core);
        const first = k === 0, last = k === toks.length - 1;
        let x0 = w.x0, x1 = w.x1;
        if (t === wt) {
          // the whole word, punctuation included
        } else if ((first || last) && t === wc) {
          // a boundary word without its punctuation: start after a leading quote, stop before a comma
          if (first) x0 = w.cx0;
          if (last) x1 = w.cx1;
          if (first && !last && !wt.endsWith(wc)) ok = false; // a first word may only drop leading marks
          if (last && !first && !wt.startsWith(wc)) ok = false; // a last word may only drop trailing marks
        } else if (first && !last && wt.endsWith(t) && t.startsWith(wc)) {
          x0 = w.cx0;                       // drops a leading quote, keeps its trailing mark
        } else if (last && !first && wt.startsWith(t) && t.endsWith(wc)) {
          x1 = w.cx1;                       // keeps a leading mark, drops the trailing one
        } else {
          ok = false;
        }
        got.push({ x0, x1, base: w.base, size: w.size, line: w.line, y0: w.y0, y1: w.y1 });
      }
      if (ok) hits.push(got);
    }
    if (hits.length <= nth) {
      fail('phrase not found on page ' + page + ' (' + hits.length + ' hits, wanted #' + nth + '): ' + phrase);
      return [];
    }
    return hits[nth];
  }

  function lineSegments(ws) {
    const segs = [];
    for (const w of ws) {
      const last = segs[segs.length - 1];
      if (last && last.line === w.line) {
        last.x0 = Math.min(last.x0, w.x0);
        last.x1 = Math.max(last.x1, w.x1);
      } else {
        segs.push({ line: w.line, x0: w.x0, x1: w.x1, base: w.base, size: w.size });
      }
    }
    return segs;
  }

  function el(tag, cls, style, parent) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (style) Object.assign(e.style, style);
    if (parent) parent.appendChild(e);
    return e;
  }

  // one paper sheet. s = {page | src, clip:[x0,y0,x1,y1] (pt or px), scale, marks, boxes, rects}
  function sheet(s, parent) {
    const isImg = !!s.src;
    const full = isImg ? [0, 0, s.natural[0], s.natural[1]] : [0, 0, window.SEC_PAGE.w, window.SEC_PAGE.h];
    const c = s.clip || full;
    const k = s.scale;
    const W = Math.round((c[2] - c[0]) * k), H = Math.round((c[3] - c[1]) * k);
    const box = el('div', 'sheet' + (s.cls ? ' ' + s.cls : ''), Object.assign({ width: W + 'px', height: H + 'px' }, s.style || {}), parent);
    const img = el('img', 'page', {
      width: full[2] * k + 'px', height: full[3] * k + 'px',
      left: -c[0] * k + 'px', top: -c[1] * k + 'px',
    }, box);
    img.src = isImg ? s.src : 'pages/sec_p' + String(s.page).padStart(2, '0') + '.svg';
    img.alt = '';
    const X = (x) => (x - c[0]) * k, Y = (y) => (y - c[1]) * k;

    // yellow marker: one stroke per line, under the ink (multiply)
    for (const m of s.marks || []) {
      const segs = isImg ? [m] : lineSegments(findPhrase(s.page, m));
      for (const g of segs) {
        let x0, x1, y0, y1;
        if (isImg) { [x0, y0, x1, y1] = [g.x0, g.y0, g.x1, g.y1]; }
        else {
          x0 = g.x0 - MARK_PADX; x1 = g.x1 + MARK_PADX;
          y0 = g.base - MARK_TOP * g.size; y1 = g.base + MARK_BOT * g.size;
        }
        el('div', 'mark', {
          left: X(x0) + 'px', top: Y(y0) + 'px',
          width: (x1 - x0) * k + 'px', height: (y1 - y0) * k + 'px',
        }, box);
      }
    }
    // thin red box around a phrase (bounding box of all its words)
    for (const b of s.boxes || []) {
      let x0, x1, y0, y1;
      if (isImg) { [x0, y0, x1, y1] = [b.x0, b.y0, b.x1, b.y1]; }
      else {
        const ws = findPhrase(s.page, b);
        if (!ws.length) continue;
        x0 = Math.min(...ws.map((w) => w.x0)) - BOX_PADX;
        x1 = Math.max(...ws.map((w) => w.x1)) + BOX_PADX;
        y0 = Math.min(...ws.map((w) => w.base - BOX_TOP * w.size)) - BOX_PADY;
        y1 = Math.max(...ws.map((w) => w.base + BOX_BOT * w.size)) + BOX_PADY;
        if (b.padx) { x0 -= b.padx; x1 += b.padx; }
        if (b.pady) { y0 -= b.pady; y1 += b.pady; }
      }
      el('div', 'rbox', {
        left: X(x0) + 'px', top: Y(y0) + 'px',
        width: (x1 - x0) * k + 'px', height: (y1 - y0) * k + 'px',
      }, box);
    }
    return box;
  }

  async function figure(cfg) {
    const fig = document.getElementById('fig');
    if (cfg.canvas) fig.style.background = cfg.canvas;
    if (cfg.layout === 'abs') {
      fig.style.width = cfg.size[0] + 'px';
      fig.style.height = cfg.size[1] + 'px';
      fig.style.padding = '0';
      fig.style.overflow = 'hidden';
      for (const s of cfg.sheets) {
        const cw = s.clip ? s.clip[2] - s.clip[0] : (s.src ? s.natural[0] : window.SEC_PAGE.w);
        if (!s.scale) s.scale = s.width / cw;
        const b = sheet(s, fig);
        Object.assign(b.style, {
          position: 'absolute', left: s.at[0] + 'px', top: s.at[1] + 'px',
          transform: s.rot ? 'rotate(' + s.rot + 'deg)' : '', zIndex: s.z || 0,
        });
        b.classList.add('lift');
      }
    } else {
      const flow = el('div', 'flow', null, fig);
      for (const s of cfg.sheets) {
        if (!s.scale) s.scale = cfg.sheetWidth / ((s.clip ? s.clip[2] - s.clip[0] : (s.src ? s.natural[0] : window.SEC_PAGE.w)));
        sheet(s, flow);
      }
      if (cfg.caption) {
        const cap = el('div', 'cap', null, fig);
        el('div', 'l', null, cap).innerHTML = cfg.caption[0];
        el('div', 'r', null, cap).innerHTML = cfg.caption[1] || '';
      }
    }
    await Promise.all([...document.images].map((im) => im.decode().catch(() => fail('image failed: ' + im.src))));
    await document.fonts.ready;
    window.__ready = true;
  }

  window.renderFigure = (cfg) => { figure(cfg).catch((e) => { fail(String(e)); window.__ready = true; }); };
})();
