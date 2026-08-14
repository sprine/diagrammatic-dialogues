// The loop: diagram -> plant a flag -> claude works -> new diagram, previous
// ones still on screen as minimaps, every flag still clickable.

const $ = (sel, root = document) => root.querySelector(sel);
const CHOICES = JSON.parse(document.getElementById('choices').textContent);
const home = $('#home');
const strip = $('#strip');
const trailList = $('#trails');
const crumb = $('#crumb');

let view = null;          // last /api/view payload
let anchor = null;        // { node, label } the composer is pointed at
let stream = null;        // EventSource for the running card
let clockTimer = null;    // elapsed counter on the running card
const readingAscii = new Set();  // cards showing their source instead of the picture

// A turn can think for minutes without touching a file, so show the time passing.
function startClock(node, startedAt) {
  const began = Date.parse(String(startedAt).replace(' ', 'T') + 'Z') || Date.now();
  const paint = () => {
    const secs = Math.max(0, Math.round((Date.now() - began) / 1000));
    node.textContent = secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m ${secs % 60}s`;
  };
  paint();
  clearInterval(clockTimer);
  clockTimer = setInterval(paint, 1000);
}

const api = async (url, opts) => {
  const res = await fetch(url, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(body.error || res.statusText);
    err.status = res.status;
    throw err;
  }
  return body;
};

const post = (url, payload) =>
  api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });

const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

// ---------------------------------------------------------------- home

// Same directory opened on different days is still one project: group trails
// by target_dir so the home page reads as "here is everywhere I've dug into
// this codebase", not a flat log of sessions.
async function loadHome() {
  const [trails, reports] = await Promise.all([
    api('/api/trails'),
    api('/api/reports').catch(() => []),
  ]);
  trailList.replaceChildren();
  if (!trails.length) {
    trailList.appendChild(el('p', 'muted', 'Nothing opened yet.'));
    return;
  }

  const byPath = new Map();
  for (const t of trails) {
    if (!byPath.has(t.target_dir)) byPath.set(t.target_dir, []);
    byPath.get(t.target_dir).push(t);
  }
  const reportsByPath = new Map();
  for (const r of reports) {
    const key = byPath.has(r.target_dir) ? r.target_dir : null;
    if (!reportsByPath.has(key)) reportsByPath.set(key, []);
    reportsByPath.get(key).push(r);
  }

  for (const [path, pathTrails] of byPath) {
    trailList.appendChild(pathGroup(path, pathTrails, reportsByPath.get(path) || []));
  }
  const orphaned = reportsByPath.get(null) || [];
  if (orphaned.length) {
    const group = el('div', 'path-group');
    group.appendChild(el('h2', 'path-heading', 'other'));
    group.appendChild(fixesDetails(orphaned));
    trailList.appendChild(group);
  }
}

function pathGroup(path, trails, reports) {
  const group = el('div', 'path-group');
  const head = el('div', 'path-head');
  head.appendChild(el('h2', 'path-heading', path.replace(/\/+$/, '').split('/').pop() || path));
  head.appendChild(el('span', 'path-full', path));
  group.appendChild(head);

  const tree = el('div', 'tree');
  treeRows(trails).forEach(({ card, prefix, isRoot, trailId }) => tree.appendChild(treeRow(card, prefix, isRoot, trailId)));
  group.appendChild(tree);

  if (reports.length) group.appendChild(fixesDetails(reports));
  return group;
}

// One or more trails against the same path, rendered as a single `tree`-style
// listing: each trail's root diagram is a top-level branch, its own flags
// nested underneath in the order they were asked.
function treeRows(trails) {
  const rows = [];
  trails.forEach((t, ti) => {
    const cards = t.cards || [];
    const root = cards.find((c) => !c.parent_id);
    if (!root) return;
    const byParent = new Map();
    for (const c of cards) {
      if (!c.parent_id) continue;
      if (!byParent.has(c.parent_id)) byParent.set(c.parent_id, []);
      byParent.get(c.parent_id).push(c);
    }
    const walk = (card, prefix, isLast, isRoot) => {
      rows.push({ card, prefix: prefix + (isLast ? '└── ' : '├── '), isRoot, trailId: t.id });
      const kids = byParent.get(card.id) || [];
      const nextPrefix = prefix + (isLast ? '    ' : '│   ');
      kids.forEach((kid, i) => walk(kid, nextPrefix, i === kids.length - 1, false));
    };
    walk(root, '', ti === trails.length - 1, true);
  });
  return rows;
}

function treeRow(card, prefix, isRoot, trailId) {
  const row = el('div', `tree-row${card.status !== 'done' ? ' ' + card.status : ''}`);
  row.appendChild(el('span', 'tree-prefix', prefix));
  const link = el('button', 'tree-link', card.title || card.remark || 'untitled');
  link.type = 'button';
  link.title = card.remark || '';
  link.onclick = () => go(card.id);
  row.appendChild(link);
  row.appendChild(el('span', 'tree-when', (card.created_at || '').slice(0, 10)));
  const del = el('button', 'tree-del', '×');
  del.type = 'button';
  if (isRoot) {
    del.title = 'Forget this trail';
    del.onclick = async (ev) => {
      ev.stopPropagation();
      if (!confirm('Forget this whole trail? This cannot be undone.')) return;
      del.disabled = true;
      try {
        await api(`/api/trails/${trailId}`, { method: 'DELETE' });
        loadHome();
      } catch (err) {
        del.disabled = false;
        alert(err.message);
      }
    };
  } else {
    del.title = 'Discard this branch and its analysis';
    del.onclick = async (ev) => {
      ev.stopPropagation();
      if (!confirm('Discard this branch and its analysis? This cannot be undone.')) return;
      del.disabled = true;
      try {
        await api(`/api/cards/${card.id}`, { method: 'DELETE' });
        loadHome();
      } catch (err) {
        del.disabled = false;
        alert(err.message);
      }
    };
  }
  row.appendChild(del);
  return row;
}

// Renders that came out wrong, waiting to be trained on. Collapsed by default
// and nested under the project they came from — this is upkeep, not headline
// content, and only worth opening when you're specifically curious.
function fixesDetails(reports) {
  const details = el('details', 'fixes');
  details.appendChild(el('summary', null, `training data · ${reports.length} filed`));
  const list = el('ul', 'fixes-list');
  for (const r of reports) {
    const item = el('li', 'fix');
    item.appendChild(el('span', 'fix-title', r.card_title || 'untitled'));
    item.appendChild(el('span', 'fix-note', r.description));
    item.appendChild(el('span', 'fix-when', (r.created_at || '').slice(0, 10)));
    list.appendChild(item);
  }
  details.appendChild(list);
  return details;
}

async function openTrail(dir, create) {
  const { card_id } = await post('/api/trails', { target_dir: dir, create });
  go(card_id);
}

// A 404 means the path doesn't exist yet — offer to create it and start blank,
// rather than just failing. Any other error is shown as-is.
function askToCreate(dir) {
  const box = $('#open-error');
  box.replaceChildren(el('span', null, `"${dir}" doesn't exist. `));
  const create = el('button', 'ghost', 'Create it and start blank');
  create.type = 'button';
  create.onclick = async () => {
    create.disabled = true;
    try {
      await openTrail(dir, true);
    } catch (err) {
      box.textContent = err.message;
    }
  };
  box.appendChild(create);
}

$('#open').onsubmit = async (ev) => {
  ev.preventDefault();
  const button = $('#open button');
  const dir = $('#dir').value.trim();
  button.disabled = true;
  $('#open-error').textContent = '';
  try {
    await openTrail(dir, false);
  } catch (err) {
    if (err.status === 404) askToCreate(dir);
    else $('#open-error').textContent = err.message;
  } finally {
    button.disabled = false;
  }
};

// ---------------------------------------------------------------- routing

const go = (cardId) => { location.hash = cardId; };

window.addEventListener('hashchange', route);

async function route() {
  const id = location.hash.slice(1);
  if (stream) { stream.close(); stream = null; }
  if (!id) {
    home.hidden = false;
    strip.hidden = true;
    crumb.replaceChildren();
    loadHome();
    return;
  }
  home.hidden = true;
  strip.hidden = false;
  await refresh(id);
}

async function refresh(id) {
  try {
    view = await api(`/api/view/${id}`);
  } catch (err) {
    strip.replaceChildren(el('p', 'error', err.message));
    return;
  }
  render();
  const active = view.cards[view.cards.length - 1];
  if (active.status === 'running') listen(active.id);
}

// ---------------------------------------------------------------- render

function render() {
  const cards = view.cards;
  clearInterval(clockTimer);
  crumb.replaceChildren();
  const back = el('button', 'crumb-home', '← trails');
  back.onclick = () => { location.hash = ''; };
  crumb.append(back, el('span', 'crumb-path', view.trail.target_dir));

  strip.replaceChildren();
  cards.forEach((card, i) => {
    const isActive = i === cards.length - 1;
    strip.appendChild(cardNode(card, isActive, i));
  });
  const active = cards[cards.length - 1];
  if (active.status === 'done') strip.appendChild(composer(active));
  addMoreToggles();
  strip.scrollLeft = strip.scrollWidth;
}

function cardNode(card, isActive, index) {
  const node = el('article', `card ${isActive ? 'active' : 'mini'} ${card.status}`);
  node.dataset.card = card.id;

  const head = el('header', 'card-head');
  head.appendChild(el('span', 'step', String(index + 1)));
  head.appendChild(el('h2', null, card.title || (card.status === 'running' ? 'thinking…' : card.remark)));
  if (card.write_mode) head.appendChild(el('span', 'badge write', 'wrote code'));
  if (card.web_mode) head.appendChild(el('span', 'badge web', 'used the web'));
  if (isActive && card.status === 'done') head.appendChild(asciiToggle(card));
  head.appendChild(el('span', 'badge', `${card.model} · ${card.effort}`));
  if (isActive && (card.status === 'done' || card.status === 'error')) {
    head.appendChild(rerunButton(card));
    if (card.parent_id) head.appendChild(discardButton(card));
  }
  node.appendChild(head);

  if (card.remark && card.anchor_label) {
    node.appendChild(el('p', 'anchor-note', `on “${card.anchor_label}” — ${card.remark}`));
  } else if (card.remark && card.parent_id) {
    node.appendChild(el('p', 'anchor-note', card.remark));
  }

  const canvas = el('div', 'canvas');
  node.appendChild(canvas);

  if (card.status === 'running') {
    const pending = el('div', 'pending');
    pending.appendChild(el('span', null, 'working'));
    const clock = el('span', 'clock', '0s');
    pending.appendChild(clock);
    canvas.appendChild(pending);
    startClock(clock, card.created_at);

    const murmur = el('div', 'murmur');
    murmur.dataset.murmur = card.id;
    const log = el('div', 'activity');
    log.dataset.log = card.id;
    const stop = el('button', 'ghost', 'stop');
    stop.onclick = () => api(`/api/cards/${card.id}/cancel`, { method: 'POST' });
    node.append(murmur, log, stop);
  } else if (card.status === 'error') {
    canvas.appendChild(el('pre', 'error', card.error));
  } else {
    paint(canvas, card, isActive);
  }

  if (!isActive) {
    node.classList.add('clickable');
    node.onclick = (ev) => { if (!ev.target.closest('.flag,[data-toggle]')) go(card.id); };
  }

  if (card.answer || card.points.length) node.appendChild(answerNode(card, isActive));

  // The synthetic root of a blank trail never actually ran a turn (no
  // session_id) — a receipt on it would claim work that never happened.
  if (card.status === 'done' && card.session_id) node.appendChild(receipt(card));
  return node;
}

// The direct answer, then the specifics that back it. One claim per line reads
// at a glance in a way a paragraph of the same words does not.
function answerNode(card, isActive) {
  const foot = el('footer', 'answer');
  const body = el('div', 'answer-body clamped');
  if (card.answer) body.appendChild(el('p', 'lead', card.answer));
  if (card.points.length) {
    const list = el('ul', 'points');
    card.points.forEach((p) => list.appendChild(el('li', null, p)));
    body.appendChild(list);
  }
  foot.appendChild(body);
  return foot;
}

// Only offer "more" when something is actually hidden, which is only knowable
// once the text has been laid out.
function addMoreToggles() {
  strip.querySelectorAll('.card.active .answer-body').forEach((body) => {
    if (body.scrollHeight <= body.clientHeight + 2) return;
    const more = el('button', 'ghost', 'more');
    more.onclick = (ev) => {
      ev.stopPropagation();
      body.classList.toggle('clamped');
      more.textContent = body.classList.contains('clamped') ? 'more' : 'less';
    };
    body.after(more);
  });
}

// What the agent actually touched. The reason this app exists is to not have to
// take the answer on faith, so the receipt is one click away, never hidden.
function receipt(card) {
  const box = el('details', 'receipt');
  const blocked = card.evidence.filter((e) => e.denied).length;
  const sum = el('summary', null,
    `${card.evidence.length} step${card.evidence.length === 1 ? '' : 's'}` +
    (blocked ? ` · ${blocked} blocked` : '') +
    (card.changes.length ? ` · ${card.changes.length} file${card.changes.length === 1 ? '' : 's'} changed` : '') +
    ` · $${(card.cost_usd || 0).toFixed(3)} · ${(card.duration_ms / 1000).toFixed(1)}s`);
  box.appendChild(sum);
  if (card.changes.length) {
    const changed = el('div', 'changed');
    changed.appendChild(el('span', 'changed-title', 'changed'));
    card.changes.forEach((f) => changed.appendChild(el('code', null, f)));
    box.appendChild(changed);
  }
  const list = el('ol', 'evidence');
  card.evidence.forEach((e) => {
    const li = el('li', e.denied ? 'denied' : null);
    li.appendChild(el('span', 'tool', e.tool));
    li.appendChild(el('span', 'detail', e.detail));
    if (e.denied) li.appendChild(el('span', 'blocked', 'blocked'));
    list.appendChild(li);
  });
  box.appendChild(list);
  return box;
}

// The picture is generated from the ascii, so being able to read the ascii is
// how you check the picture is not lying to you.
function paint(canvas, card, isActive) {
  if (readingAscii.has(card.id)) {
    canvas.replaceChildren(el('pre', 'ascii', card.ascii), reportRow(card));
    return;
  }
  canvas.innerHTML = card.svg;   // rendered server-side; one renderer, not two
  wire(canvas, isActive);
}

function asciiToggle(card) {
  const pill = el('button', 'badge pill', 'ASCII');
  pill.type = 'button';
  pill.title = 'Read the source this diagram was drawn from';
  if (readingAscii.has(card.id)) pill.classList.add('on');
  pill.onclick = (ev) => {
    ev.stopPropagation();
    readingAscii.has(card.id) ? readingAscii.delete(card.id) : readingAscii.add(card.id);
    pill.classList.toggle('on', readingAscii.has(card.id));
    paint(pill.closest('.card').querySelector('.canvas'), card, true);
  };
  return pill;
}

// Same remark, run again against the code as it is now. Whatever was asked off
// this diagram was asked of a picture that is about to stop existing.
function rerunButton(card) {
  const btn = el('button', 'badge pill', '↻');
  btn.type = 'button';
  btn.title = 'Re-run this against the code as it is now';
  btn.onclick = async (ev) => {
    ev.stopPropagation();
    if (card.flags.length && !confirm('This discards the branches asked from this diagram. Re-run anyway?')) return;
    btn.disabled = true;
    try {
      await post(`/api/cards/${card.id}/rerun`, {});
      refresh(card.id);
    } catch (err) {
      btn.disabled = false;
      alert(err.message);
    }
  };
  return btn;
}

// Deletes this card and every branch asked off it. There is no undo, so this
// asks first the same way the trail list's own delete does.
function discardButton(card) {
  const btn = el('button', 'badge pill discard', '×');
  btn.type = 'button';
  btn.title = 'Discard this branch and its analysis';
  btn.onclick = async (ev) => {
    ev.stopPropagation();
    if (!confirm('Discard this branch and its analysis? This cannot be undone.')) return;
    btn.disabled = true;
    try {
      const { parent_id } = await api(`/api/cards/${card.id}`, { method: 'DELETE' });
      go(parent_id);
    } catch (err) {
      btn.disabled = false;
      alert(err.message);
    }
  };
  return btn;
}

// Reporting belongs next to the ascii: you have to read the source to know
// whether the picture misread it or the model drew it wrong in the first place.
// This files training data. It never edits the renderer.
function reportRow(card) {
  const row = el('form', 'report');
  const input = el('input', 'report-what');
  input.placeholder = 'What did this render get wrong?';
  input.setAttribute('aria-label', 'what the render got wrong');
  const send = el('button', 'badge pill', 'log it');
  send.type = 'submit';
  const said = el('span', 'report-said');
  row.append(input, send, said);

  row.onsubmit = async (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    if (!input.value.trim()) return input.focus();
    send.disabled = true;
    try {
      const { path } = await post(`/api/cards/${card.id}/report`, { description: input.value });
      row.replaceChildren(el('span', 'report-said ok', `filed to training-data/${path}`));
    } catch (err) {
      said.textContent = err.message;
      send.disabled = false;
    }
  };
  return row;
}

function wire(root, isActive) {
  root.querySelectorAll('.flag').forEach((flag) => {
    flag.onclick = (ev) => { ev.stopPropagation(); go(flag.dataset.card); };
  });
  if (!isActive) return;
  root.querySelectorAll('.hit').forEach((hit) => {
    hit.onclick = () => {
      anchor = { node: hit.dataset.node, label: hit.dataset.label };
      render();
      $('#remark')?.focus();
    };
  });
}

// ---------------------------------------------------------------- composer

function composer(card) {
  const blank = !!view.trail.blank;
  const box = el('form', 'card composer');
  const head = el('header', 'card-head');
  head.appendChild(el('h2', null, anchor ? `Flag on “${anchor.label}”` : 'Plant a flag'));
  if (anchor) {
    const clear = el('button', 'ghost', 'anywhere instead');
    clear.type = 'button';
    clear.onclick = () => { anchor = null; render(); };
    head.appendChild(clear);
  }
  box.appendChild(head);
  box.appendChild(el('p', 'hint', blank
    ? 'A blank project — say what to build. Edits and the web are on for this whole trail.'
    : anchor
      ? 'Ask about this part, or tell Claude what to change.'
      : 'Click any box in the diagram to aim at it, or just ask from here.'));

  const text = el('textarea', 'remark');
  text.id = 'remark';
  text.rows = 4;
  text.placeholder = 'Why is it done this way?  /  Is that the decision I would have taken?  /  Split this out.';
  text.onkeydown = (ev) => {
    if ((ev.metaKey || ev.ctrlKey) && ev.key === 'Enter') {
      ev.preventDefault();
      box.requestSubmit();
    }
  };
  box.appendChild(text);

  const controls = el('div', 'controls');
  const depth = card.depth + 1;
  const model = select('model', CHOICES.models, suggested(depth)[0]);
  const effort = select('effort', CHOICES.efforts, suggested(depth)[1]);
  controls.append(model, effort);
  box.appendChild(controls);

  // A blank trail auto-grants both server-side, so the toggle would be a lie.
  let writeBox = null, webBox = null;
  if (!blank) {
    const advanced = el('details', 'advanced');
    advanced.appendChild(el('summary', null, 'Advanced'));
    const perms = el('div', 'controls');
    const write = el('label', 'toggle');
    writeBox = el('input');
    writeBox.type = 'checkbox';
    writeBox.id = 'write';
    write.append(writeBox, el('span', null, 'let it edit files'));
    const web = el('label', 'toggle');
    webBox = el('input');
    webBox.type = 'checkbox';
    webBox.id = 'web';
    web.append(webBox, el('span', null, 'let it search the web'));
    perms.append(write, web);
    advanced.appendChild(perms);
    box.appendChild(advanced);
  }

  const send = el('button', 'send', 'Ask');
  box.appendChild(send);
  const err = el('p', 'error');
  box.appendChild(err);

  box.onsubmit = async (ev) => {
    ev.preventDefault();
    send.disabled = true;
    try {
      const { card_id } = await post(`/api/cards/${card.id}/remark`, {
        remark: text.value,
        anchor_node: anchor?.node ?? null,
        model: model.querySelector('select').value,
        effort: effort.querySelector('select').value,
        write: blank || writeBox.checked,
        web: blank || webBox.checked,
      });
      anchor = null;
      go(card_id);
    } catch (e) {
      err.textContent = e.message;
      send.disabled = false;
    }
  };
  return box;
}

// Sketch: start at low effort, climb as the questions get deeper. The rungs come
// from the server, which is what actually applies them.
const suggested = (depth) => CHOICES.ladder[Math.min(depth, CHOICES.ladder.length - 1)];

function select(name, options, value) {
  const wrap = el('label', 'chip');
  wrap.appendChild(el('span', null, name));
  const sel = document.createElement('select');
  options.forEach((o) => {
    const opt = document.createElement('option');
    opt.value = opt.textContent = o;
    if (o === value) opt.selected = true;
    sel.appendChild(opt);
  });
  wrap.appendChild(sel);
  return wrap;
}

// ---------------------------------------------------------------- live turn

function listen(cardId) {
  stream = new EventSource(`/api/stream/${cardId}`);
  const log = () => document.querySelector(`[data-log="${cardId}"]`);
  const murmur = () => document.querySelector(`[data-murmur="${cardId}"]`);
  stream.onmessage = (ev) => {
    const event = JSON.parse(ev.data);
    if (event.kind === 'note') {
      const line = murmur();
      if (line) line.textContent = event.text;
    } else if (event.kind === 'activity') {
      const target = log();
      if (!target) return;
      const row = el('div', 'act');
      row.appendChild(el('span', 'tool', event.tool));
      row.appendChild(el('span', 'detail', event.detail));
      target.appendChild(row);
      target.scrollTop = target.scrollHeight;
    } else if (event.kind === 'idle' || event.kind === 'done' || event.kind === 'error') {
      // 'idle' means the turn finished before we subscribed; the view has the result.
      clearInterval(clockTimer);
      stream.close();
      stream = null;
      refresh(cardId);
    }
  };
  // A dropped connection does not mean the turn stopped — it runs server-side,
  // detached from any browser tab. Re-fetch the real state instead of just
  // going dark: still running, this reconnects; finished while we were gone,
  // this shows the real result instead of a clock ticking on a dead turn.
  stream.onerror = () => {
    stream?.close();
    stream = null;
    refresh(cardId);
  };
}

route();
