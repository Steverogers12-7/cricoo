/* ipl-live.js  -  drives ipl.html (live strip + model performance panel)

   HOW IT WORKS
   - One function, render(state), paints the whole page from a single "state" object.
   - Today the state comes from buildDemo(t) (fake match, clearly labelled DEMO).
   - Later set API_BASE and the same page runs on real data: GET {API_BASE}/api/ipl/live
     must return an object in the SAME shape as buildDemo() returns (see "STATE SHAPE").

   STATE SHAPE
   {
     demo: true|false,
     status: "pre" | "inn1" | "break" | "inn2" | "done",
     updatedAt: ISO string,
     venue: "...", toss: "...", result: "...",
     teams: { a: {code, name}, b: {code, name} },     // a bats first, b chases
     model1: { total, low, high, xiA: [11 names], xiB: [11 names] },
     inn1:   { runsByOver: [...], wktsByOver: [...], proj: [...],
               ballsByOver: [["1","W","Wd","4","0","6","2"], ...] },   // one entry per completed over; ballsByOver optional
     //   ball tokens: "0"-"6" runs off the bat, "W" wicket, "Wd"/"Wd1" wide(+runs), "Nb"/"Nb4" no-ball(+runs), "Lb1" leg-bye, "B4" bye
     inn2:   { target, runsByOver: [...], wktsByOver: [...], win: [...] }  // win = chasing team's win %, per over
   }
*/
(function () {
  'use strict';

  /* ---------- config ---------- */
  var API_BASE = 'http://127.0.0.1:5000';            // '' = demo mode. Later: 'https://your-api.onrender.com'
  var POLL_MS = 60000;          // live polling (backend itself refreshes about once per over)
  var DEMO_STEP_MS = 1200;

  /* ---------- tiny helpers ---------- */
  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function sum(a) { return a.reduce(function (x, y) { return x + y; }, 0); }
  function cumTo(a, n) { return sum(a.slice(0, n)); }
  function round1(x) { return Math.round(x * 10) / 10; }

  /* team badge = coloured circle with the team code (no official logos, so no licensing issue) */
  var TEAM_COL = {
    RCB: ['#d4202a', '#fff'], CSK: ['#f5c518', '#1b1b1b'], MI: ['#1d4ed8', '#fff'], KKR: ['#5b2a86', '#fff'],
    RR: ['#e5369a', '#fff'], GT: ['#1c3a5e', '#fff'], PBKS: ['#d71920', '#fff'], DC: ['#17479e', '#fff'],
    SRH: ['#f26522', '#fff'], LSG: ['#18a9c9', '#04202a']
  };
  function badge(code) {
    var c = TEAM_COL[code] || ['#35506f', '#fff'];
    return '<span class="ipl-badge" style="background:' + c[0] + ';color:' + c[1] + '">' + esc(code) + '</span>';
  }

  /* ---------- DEMO DATA (fake, for layout testing only) ---------- */
  var D = {
    teams: { a: { code: 'RCB', name: 'Royal Challengers Bengaluru' }, b: { code: 'MI', name: 'Mumbai Indians' } },
    venue: 'M. Chinnaswamy Stadium, Bengaluru',
    toss: 'RCB won the toss and chose to bat',
    inn1Runs: [6, 9, 7, 12, 8, 10, 5, 11, 9, 8, 7, 9, 8, 9, 10, 9, 10, 11, 10, 13],
    inn1Wk:   [0, 0, 1, 0, 0, 1, 0, 1, 0, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0, 0],
    inn2Runs: [8, 7, 11, 6, 9, 12, 8, 10, 7, 9, 9, 8, 11, 7, 9, 10, 8, 12, 9, 8],
    inn2Wk:   [0, 1, 0, 1, 0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 1],
    model1: { total: 176, low: 163, high: 189 }
  };
  D.model1.xiA = ['Virat Kohli', 'Phil Salt', 'Rajat Patidar (C)', 'Liam Livingstone', 'Jitesh Sharma (WK)', 'Tim David', 'Krunal Pandya', 'Bhuvneshwar Kumar', 'Josh Hazlewood', 'Yash Dayal', 'Suyash Sharma'];
  D.model1.xiB = ['Rohit Sharma', 'Ryan Rickelton (WK)', 'Suryakumar Yadav', 'Tilak Varma', 'Hardik Pandya (C)', 'Naman Dhir', 'Mitchell Santner', 'Deepak Chahar', 'Trent Boult', 'Jasprit Bumrah', 'Will Jacks'];

  /* ball-by-ball for the demo (deterministic): over total + wickets are fixed, balls are generated to match */
  function rng(seed) { return function () { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296; }; }
  var BV = [0, 1, 2, 3, 4, 6], BW = [30, 34, 8, 2, 14, 6];
  function pickBall(r) { var t = 0, i; for (i = 0; i < BW.length; i++) t += BW[i]; var x = r() * t; for (i = 0; i < BW.length; i++) { x -= BW[i]; if (x <= 0) return BV[i]; } return 0; }
  function mkBalls(runs, wk, seed) {
    var r = rng(seed * 7919 + 13), extras = [], vals, sum, tries = 0, i;
    if (runs >= 3 && seed % 5 === 2) extras.push('Wd'); else if (runs >= 6 && seed % 7 === 3) extras.push('Nb');
    var rem = runs - extras.length, legal = 6 - wk;
    do { vals = []; sum = 0; for (i = 0; i < legal; i++) { var v = pickBall(r); vals.push(v); sum += v; } tries++; } while (sum !== rem && tries < 5000);
    var slots = [0, 1, 2, 3, 4, 5], wpos = {}, out = [], vi = 0;
    for (i = 0; i < wk; i++) { var pi = Math.floor(r() * slots.length); wpos[slots[pi]] = true; slots.splice(pi, 1); }
    for (i = 0; i < 6; i++) out.push(wpos[i] ? 'W' : String(vals[vi++]));
    extras.forEach(function (x) { out.splice(Math.floor(r() * (out.length + 1)), 0, x); });
    return out;
  }

  function projInn1(cum, wk, ov) {
    if (ov >= 20) return cum;
    var rr = ov === 0 ? 8.4 : cum / ov;
    var rate = (0.5 * rr + 0.5 * 8.4) * (1 - 0.035 * wk);
    return Math.round(cum + (20 - ov) * rate);
  }
  function winInn2(cum, wk, ov, target) {
    if (cum >= target) return 100;
    if (ov >= 20 || wk >= 10) return 0;
    var rr = ov === 0 ? 8.2 : cum / ov;
    var rate = (0.5 * rr + 0.5 * 8.2) * (1 - 0.04 * wk);
    var p = cum + (20 - ov) * rate;
    var z = (p - target + 0.5) / (3 + (20 - ov) * 1.1);
    return Math.round(1000 / (1 + Math.exp(-z))) / 10;
  }

  // t: 0 = toss+XI, 1..20 = innings 1 overs, 21 = break, 22..41 = innings 2 overs, 42 = finished
  function buildDemo(t) {
    var s = {
      demo: true, updatedAt: new Date().toISOString(), venue: D.venue, toss: D.toss, result: '',
      teams: D.teams, model1: D.model1,
      inn1: { runsByOver: [], wktsByOver: [], proj: [], ballsByOver: [] },
      inn2: { target: 0, runsByOver: [], wktsByOver: [], win: [], ballsByOver: [] }
    };
    var n1 = t <= 0 ? 0 : Math.min(t, 20);
    var i2 = t >= 22 ? Math.min(t - 21, 20) : 0;
    s.status = t === 0 ? 'pre' : t <= 20 ? 'inn1' : t === 21 ? 'break' : t <= 41 ? 'inn2' : 'done';

    var k, cum, wk;
    for (k = 1; k <= n1; k++) {
      s.inn1.runsByOver.push(D.inn1Runs[k - 1]);
      s.inn1.wktsByOver.push(D.inn1Wk[k - 1]);
      s.inn1.ballsByOver.push(mkBalls(D.inn1Runs[k - 1], D.inn1Wk[k - 1], k));
      s.inn1.proj.push(projInn1(cumTo(D.inn1Runs, k), cumTo(D.inn1Wk, k), k));
    }
    var total1 = sum(D.inn1Runs);
    s.inn2.target = total1 + 1;
    for (k = 1; k <= i2; k++) {
      cum = cumTo(D.inn2Runs, k); wk = cumTo(D.inn2Wk, k);
      s.inn2.runsByOver.push(D.inn2Runs[k - 1]);
      s.inn2.wktsByOver.push(D.inn2Wk[k - 1]);
      s.inn2.ballsByOver.push(mkBalls(D.inn2Runs[k - 1], D.inn2Wk[k - 1], k + 20));
      s.inn2.win.push(winInn2(cum, wk, k, s.inn2.target));
    }
    if (s.status === 'done') {
      var margin = s.inn2.target - 1 - sum(D.inn2Runs);
      s.result = D.teams.a.code + ' won by ' + margin + ' runs';
    }
    return s;
  }

  /* ---------- derived numbers ---------- */
  function innings(runsBy, wktsBy) {
    var n = runsBy.length;
    return { runs: sum(runsBy), wkts: sum(wktsBy), overs: n, crr: n ? sum(runsBy) / n : 0 };
  }

  /* ---------- SVG line chart (no library) ---------- */
  function chartSize() {
    var p = document.querySelector('.ipl-panel'), w = p ? p.clientWidth : 640;
    w = Math.max(300, Math.min(1200, w));
    return [w, w < 520 ? 160 : 180];
  }
  function lineChart(opt) {
    var sz = chartSize(), W = sz[0], H = sz[1], L = 36, R = 12, T = 10, B = 24;
    var pw = W - L - R, ph = H - T - B;
    function X(x) { return L + (x / 20) * pw; }
    function Y(y) { return T + ph - ((y - opt.yMin) / (opt.yMax - opt.yMin)) * ph; }
    var out = '<svg class="ipl-chart" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="' + esc(opt.label) + '">';
    var g, v;
    for (g = 0; g <= 4; g++) {
      v = opt.yMin + (g / 4) * (opt.yMax - opt.yMin);
      out += '<line x1="' + L + '" x2="' + (W - R) + '" y1="' + Y(v) + '" y2="' + Y(v) + '" stroke="rgba(255,255,255,.08)"/>';
      out += '<text x="' + (L - 6) + '" y="' + (Y(v) + 4) + '" text-anchor="end">' + Math.round(v) + (opt.unit || '') + '</text>';
    }
    for (g = 0; g <= 20; g += 5) {
      out += '<text x="' + X(g) + '" y="' + (H - 6) + '" text-anchor="middle">' + g + '</text>';
    }
    out += '<text x="' + (W - R) + '" y="' + (T + 2) + '" text-anchor="end">overs</text>';
    opt.series.forEach(function (s) {
      if (!s.pts.length) return;
      var d = s.pts.map(function (p, i) { return (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ' ' + Y(p[1]).toFixed(1); }).join(' ');
      if (s.area) {
        var last = s.pts[s.pts.length - 1], first = s.pts[0];
        out += '<path d="' + d + ' L' + X(last[0]).toFixed(1) + ' ' + Y(opt.yMin) + ' L' + X(first[0]).toFixed(1) + ' ' + Y(opt.yMin) + ' Z" fill="' + s.color + '" opacity=".12"/>';
      }
      out += '<path d="' + d + '" fill="none" stroke="' + s.color + '" stroke-width="' + (s.w || 2.5) + '"' +
        (s.dash ? ' stroke-dasharray="6 5"' : '') + ' stroke-linejoin="round" stroke-linecap="round"/>';
      var e = s.pts[s.pts.length - 1];
      if (s.dot) out += '<circle cx="' + X(e[0]).toFixed(1) + '" cy="' + Y(e[1]).toFixed(1) + '" r="4.5" fill="' + s.color + '"/>';
    });
    return out + '</svg>';
  }

  /* ---------- render pieces ---------- */
  var STATUS_TEXT = { pre: 'TOSS DONE', inn1: 'LIVE · INNINGS 1', 'break': 'INNINGS BREAK', inn2: 'LIVE · INNINGS 2', done: 'RESULT' };
  var CURRENT_TAB_FOR = { pre: 'pre', inn1: 'inn1', 'break': 'inn1', inn2: 'inn2', done: 'inn2' };

  function renderStrip(s) {
    var a = s.teams.a, b = s.teams.b;
    var i1 = innings(s.inn1.runsByOver, s.inn1.wktsByOver);
    var i2 = innings(s.inn2.runsByOver, s.inn2.wktsByOver);

    $('mVenue').textContent = s.venue || '';
    var st = $('mStatus');
    st.textContent = STATUS_TEXT[s.status] || '';
    st.className = 'ipl-status' + (s.status === 'inn1' || s.status === 'inn2' ? ' is-live' : s.status === 'done' ? ' is-done' : '');

    function teamInner(t, score, small, batting) {
      return badge(t.code) +
        '<div class="nm">' + esc(t.name) + (batting ? '<span class="ipl-bat" title="Batting"></span>' : '') + '<small>' + esc(t.code) + '</small></div>' +
        '<div class="sc">' + score + '<small>' + small + '</small></div>';
    }
    function paint(el, extra, batting, overs, inner) {
      el.className = 'ipl-team' + extra + (batting ? ' is-batting' : '');
      el.style.setProperty('--p', Math.min(100, Math.round((overs || 0) / 20 * 100)) + '%');
      el.innerHTML = inner;
    }
    var aHas = s.status !== 'pre', bHas = s.status === 'inn2' || s.status === 'done';
    paint($('teamA'), '', s.status === 'inn1', i1.overs,
      teamInner(a, aHas ? i1.runs + '/' + i1.wkts : '-', aHas ? '(' + i1.overs + ' ov)' : 'Yet to bat', s.status === 'inn1'));
    paint($('teamB'), ' mt-3', s.status === 'inn2', i2.overs,
      teamInner(b, bHas ? i2.runs + '/' + i2.wkts : '-', bHas ? '(' + i2.overs + ' ov)' : 'Yet to bat', s.status === 'inn2'));

    var line = '', chips = [];
    if (s.status === 'pre') {
      line = s.toss || 'Toss done. Playing XIs announced.';
      chips.push(['Projected score', s.model1.total]);
    } else if (s.status === 'inn1') {
      line = esc(a.code) + ' batting: ' + i1.runs + '/' + i1.wkts + ' after ' + i1.overs + ' overs';
      chips.push(['Projected score', s.inn1.proj.length ? s.inn1.proj[s.inn1.proj.length - 1] : s.model1.total]);
    } else if (s.status === 'break') {
      line = 'Innings break. ' + esc(b.code) + ' need ' + s.inn2.target + ' to win';
      chips.push(['Target', s.inn2.target], ['Pre-match projection', s.model1.total]);
    } else if (s.status === 'inn2') {
      var need = s.inn2.target - i2.runs, balls = 120 - i2.overs * 6;
      line = esc(b.code) + ' need ' + need + ' off ' + balls + ' balls';
      chips.push(['Target', s.inn2.target]);
    } else {
      line = esc(s.result || 'Match finished');
      chips.push(['Final', esc(a.code) + ' ' + i1.runs + '/' + i1.wkts + ' · ' + esc(b.code) + ' ' + i2.runs + '/' + i2.wkts]);
    }
    $('mLine').innerHTML = line;
    var sw = $('mWin');
    if (s.status === 'inn2' || s.status === 'done') {
      var wn = s.inn2.win.length ? s.inn2.win[s.inn2.win.length - 1] : 50;
      sw.innerHTML = '<div class="top"><span>' + esc(b.code) + ' win <b>' + wn + '%</b></span><span><b>' + (Math.round((100 - wn) * 10) / 10) + '%</b> ' + esc(a.code) + '</span></div>' +
        '<div class="bar"><i style="width:' + wn + '%"></i></div>';
      sw.classList.remove('d-none');
    } else { sw.classList.add('d-none'); }
    $('mChips').innerHTML = chips.map(function (c) { return '<div class="ipl-chip">' + c[0] + ': <b>' + c[1] + '</b></div>'; }).join('');

    renderOvers(s, (s.status === 'inn2' || s.status === 'done') ? s.inn2 : s.inn1);
  }

  /* ---------- overs carousel: one box per completed over, click = ball-by-ball ---------- */
  var ov = { key: '', sel: -1, user: false, s: null, cur: null, first: true };
  function ballHtml(t) {
    var m = /^(Wd|Nb|Lb|B)(\d*)$/.exec(t), cls = 'n', txt = t, ttl = t + ' run' + (t === '1' ? '' : 's');
    var names = { Wd: 'Wide', Nb: 'No ball', Lb: 'Leg byes', B: 'Byes' };
    if (t === 'W') { cls = 'w'; txt = 'W'; ttl = 'Wicket'; }
    else if (m) { cls = 'x'; txt = m[1] + (m[2] ? '+' + m[2] : ''); ttl = names[m[1]] + (m[2] ? ' + ' + m[2] : ''); }
    else if (t === '4') { cls = 'f'; ttl = 'Four'; }
    else if (t === '6') { cls = 's'; ttl = 'Six'; }
    else if (t === '0') { cls = 'd'; txt = '&bull;'; ttl = 'Dot ball'; }
    return '<span class="ipl-ball ' + cls + '" title="' + esc(ttl) + '">' + (cls === 'd' ? txt : esc(txt)) + '</span>';
  }
  function overDetail() {
    var cur = ov.cur, k = ov.sel, el = $('mOverDetail');
    if (!cur || k < 0 || k >= cur.runsByOver.length) { el.innerHTML = ''; return; }
    var runs = cur.runsByOver[k], w = cur.wktsByOver[k], balls = cur.ballsByOver && cur.ballsByOver[k];
    var extras = balls ? balls.filter(function (t) { return /^(Wd|Nb|Lb|B)/.test(t); }).length : 0;
    el.innerHTML = '<div class="top"><b>Over ' + (k + 1) + '</b><span>' + runs + ' runs' + (w ? ' &middot; ' + w + ' wicket' + (w > 1 ? 's' : '') : '') +
      (extras ? ' &middot; ' + extras + ' extra' + (extras > 1 ? 's' : '') : '') + '</span></div>' +
      (balls ? '<div class="ipl-balls">' + balls.map(ballHtml).join('') + '</div>' : '');
  }
  function navState() {
    var sc = $('mOvers');
    $('ovPrev').disabled = sc.scrollLeft <= 2;
    $('ovNext').disabled = sc.scrollLeft + sc.clientWidth >= sc.scrollWidth - 2;
  }
  function renderOvers(s, cur) {
    var n = cur.runsByOver.length, key = (s.status === 'inn2' || s.status === 'done') ? 'i2' : 'i1';
    var sc = $('mOvers'), left = sc.scrollLeft;
    var atEnd = ov.first || (sc.scrollWidth - sc.clientWidth - sc.scrollLeft < 6);
    if (ov.key !== key) { ov.key = key; ov.user = false; atEnd = true; left = 0; }
    ov.s = s; ov.cur = cur; ov.first = false;
    if (!ov.user || ov.sel > n - 1) { ov.sel = n - 1; ov.user = false; }
    $('ovWrap').classList.toggle('d-none', n === 0);
    if (!n) { sc.innerHTML = ''; $('mOverDetail').innerHTML = ''; return; }
    var html = '', k;
    for (k = 0; k < n; k++) {
      html += '<button type="button" class="ipl-over' + (cur.wktsByOver[k] ? ' has-w' : cur.runsByOver[k] >= 12 ? ' is-big' : '') + (k === ov.sel ? ' is-sel' : '') + '" data-k="' + k + '">' +
        cur.runsByOver[k] + '<small>ov ' + (k + 1) + '</small></button>';
    }
    sc.innerHTML = html;
    sc.scrollLeft = left;
    if (atEnd) sc.scrollTo({ left: sc.scrollWidth, behavior: 'smooth' });
    overDetail();
    setTimeout(navState, 400);
  }
  $('mOvers').addEventListener('click', function (e) {
    var b = e.target.closest('.ipl-over'); if (!b || !ov.cur) return;
    ov.sel = +b.getAttribute('data-k');
    ov.user = ov.sel !== ov.cur.runsByOver.length - 1;
    Array.prototype.forEach.call($('mOvers').children, function (c) { c.classList.toggle('is-sel', +c.getAttribute('data-k') === ov.sel); });
    overDetail();
  });
  $('mOvers').addEventListener('scroll', navState);
  $('ovPrev').addEventListener('click', function () { var sc = $('mOvers'); sc.scrollBy({ left: -sc.clientWidth * 0.8, behavior: 'smooth' }); });
  $('ovNext').addEventListener('click', function () { var sc = $('mOvers'); sc.scrollBy({ left: sc.clientWidth * 0.8, behavior: 'smooth' }); });

  function renderPre(s) {
    var m = s.model1, el = $('pane-pre');
    if (!m || !m.total) { el.innerHTML = '<div class="ipl-empty">Playing XI not announced yet. Model 1 runs after the toss.</div>'; return; }
    var lo = 100, hi = 260;
    function pos(v) { return Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100)); }
    function list(arr) { return '<ol>' + arr.map(function (p) { return '<li>' + esc(p) + '</li>'; }).join('') + '</ol>'; }
    el.innerHTML =
      '<h3>Model 1 &middot; Projected first-innings total</h3>' +
      '<div class="ipl-sub">Built from this playing XI and the venue. Updated once the XIs are known.</div>' +
      '<div class="ipl-kpis">' +
      '<div class="ipl-kpi"><div class="l">Projected total (' + esc(s.teams.a.code) + ')</div><div class="v mint">' + m.total + '</div></div>' +
      '<div class="ipl-kpi"><div class="l">Likely range</div><div class="v">' + m.low + ' &ndash; ' + m.high + '</div></div>' +
      '</div>' +
      '<div class="ipl-range"><i style="left:' + pos(m.low) + '%;width:' + (pos(m.high) - pos(m.low)) + '%"></i><b style="left:calc(' + pos(m.total) + '% - 2px)"></b></div>' +
      '<div class="ipl-sub mb-4">' + lo + ' &nbsp;&middot;&middot;&middot;&nbsp; ' + hi + ' runs</div>' +
      '<div class="ipl-xi"><div><h3>' + badge(s.teams.a.code) + esc(s.teams.a.name) + '</h3>' + list(m.xiA || []) + '</div>' +
      '<div><h3>' + badge(s.teams.b.code) + esc(s.teams.b.name) + '</h3>' + list(m.xiB || []) + '</div></div>';
  }

  function renderInn1(s) {
    var el = $('pane-inn1'), p = s.inn1.proj, n = p.length;
    if (!n) { el.innerHTML = '<div class="ipl-empty">Innings 1 has not started. The projection updates over by over from here.</div>'; return; }
    var i1 = innings(s.inn1.runsByOver, s.inn1.wktsByOver);
    var latest = p[n - 1], diff = latest - s.model1.total;
    var projPts = [[0, s.model1.total]], scorePts = [[0, 0]], k;
    for (k = 1; k <= n; k++) { projPts.push([k, p[k - 1]]); scorePts.push([k, cumTo(s.inn1.runsByOver, k)]); }
    var yMax = Math.ceil(Math.max(latest, s.model1.high, i1.runs) / 20) * 20 + 20;
    var chart = lineChart({
      label: 'Projected first innings total by over', yMin: 0, yMax: yMax,
      series: [
        { pts: [[0, s.model1.total], [20, s.model1.total]], color: '#9aa7bd', dash: true, w: 1.8 },
        { pts: scorePts, color: '#ffc857', w: 2.2 },
        { pts: projPts, color: '#2fe3a5', w: 3, dot: true }
      ]
    });
    var report = '';
    if (s.status === 'break' || s.status === 'inn2' || s.status === 'done') {
      var err = i1.runs - s.model1.total, inRange = i1.runs >= s.model1.low && i1.runs <= s.model1.high;
      report = '<div class="ipl-report"><b>Model 1 report card:</b> predicted ' + s.model1.total + ' (range ' + s.model1.low + '&ndash;' + s.model1.high +
        '), actual ' + i1.runs + '. Off by ' + (err > 0 ? '+' : '') + err + ' runs, ' + (inRange ? 'inside' : 'outside') + ' the range.</div>';
    }
    el.innerHTML =
      '<h3>In-play &middot; Live target projection</h3>' +
      '<div class="ipl-sub">Re-projected after every over from score, wickets and run rate.</div>' +
      '<div class="ipl-kpis ipl-kpis-2">' +
      '<div class="ipl-kpi"><div class="l">Current score</div><div class="v">' + i1.runs + '/' + i1.wkts + '</div><div class="n">' + i1.overs + ' overs</div></div>' +
      '<div class="ipl-kpi"><div class="l">Live projection</div><div class="v mint">' + latest + '</div><div class="n">after over ' + n + '</div></div>' +
      '</div>' + chart +
      '<div class="ipl-legend"><span style="--c:#2fe3a5">Live projection</span><span style="--c:#ffc857">Actual score</span><span class="dash" style="--c:#9aa7bd">Pre-match projection</span></div>' +
      report;
  }

  function renderInn2(s) {
    var el = $('pane-inn2'), w = s.inn2.win, n = w.length;
    if (!n) { el.innerHTML = '<div class="ipl-empty">Win probability starts once the chase begins.</div>'; return; }
    var i2 = innings(s.inn2.runsByOver, s.inn2.wktsByOver);
    var pb = w[n - 1], pa = Math.round((100 - pb) * 10) / 10;
    var need = Math.max(0, s.inn2.target - i2.runs), balls = 120 - i2.overs * 6;
    var pts = [], k;
    for (k = 1; k <= n; k++) pts.push([k, w[k - 1]]);
    var chart = lineChart({
      label: 'Chasing team win probability by over', yMin: 0, yMax: 100, unit: '%',
      series: [
        { pts: [[0, 50], [20, 50]], color: '#9aa7bd', dash: true, w: 1.5 },
        { pts: pts, color: '#2fe3a5', w: 3, dot: true, area: true }
      ]
    });
    el.innerHTML =
      '<h3>Model 2 &middot; Chase win probability</h3>' +
      '<div class="ipl-sub">Updates after every over of the chase.</div>' +
      '<div class="ipl-win"><div class="a" style="width:' + Math.max(pb, 18) + '%">' + esc(s.teams.b.code) + ' ' + pb + '%</div>' +
      '<div class="b" style="width:' + Math.max(pa, 18) + '%">' + pa + '% ' + esc(s.teams.a.code) + '</div></div>' +
      '<div class="ipl-kpis ipl-kpis-2">' +
      '<div class="ipl-kpi"><div class="l">Current score</div><div class="v">' + i2.runs + '/' + i2.wkts + '</div><div class="n">' + i2.overs + ' overs</div></div>' +
      '<div class="ipl-kpi"><div class="l">Needed</div><div class="v">' + need + '</div><div class="n">off ' + balls + ' balls</div></div>' +
      '</div>' + chart +
      '<div class="ipl-legend"><span style="--c:#2fe3a5">' + esc(s.teams.b.code) + ' win % (chasing)</span><span class="dash" style="--c:#9aa7bd">50% line</span></div>';
  }

  var rsT = null;
  window.addEventListener('resize', function () { clearTimeout(rsT); rsT = setTimeout(function () { if (lastState && lastState.teams && lastState.status !== 'none') render(lastState); }, 150); });

  /* ---------- tabs ---------- */
  var userTab = null, lastStatus = null, activeTab = 'pre';
  function showTab(name) {
    activeTab = name;
    ['pre', 'inn1', 'inn2'].forEach(function (t) {
      $('pane-' + t).classList.toggle('is-active', t === name);
      $('tab-' + t).classList.toggle('is-active', t === name);
      $('tab-' + t).setAttribute('aria-selected', t === name ? 'true' : 'false');
    });
  }
  function renderTabs(s) {
    $('tab-inn1').disabled = s.status === 'pre';
    $('tab-inn2').disabled = s.status === 'pre' || s.status === 'inn1' || s.status === 'break';
    if (s.status !== lastStatus) { userTab = null; lastStatus = s.status; }   // new match phase: follow the match again
    var want = userTab || CURRENT_TAB_FOR[s.status];
    if ($('tab-' + want).disabled) want = CURRENT_TAB_FOR[s.status];
    showTab(want);
  }

  var lastState = null;
  function render(s) {
    lastState = s;
    var none = !s || s.status === 'none' || !s.teams;
    $('mMain').classList.toggle('d-none', none);
    $('mEmpty').classList.toggle('d-none', !none);
    if (none) {
      $('mEmpty').innerHTML = '<h3>No match in progress</h3><p>' + esc((s && s.next) || 'Live scores and predictions appear here when the next IPL match starts.') + '</p>';
      $('mStatus').textContent = 'NO LIVE MATCH'; $('mStatus').className = 'ipl-status';
      return;
    }
    renderStrip(s);
    renderPre(s);
    renderInn1(s);
    renderInn2(s);
    renderTabs(s);
  }

  $('tabs').addEventListener('click', function (e) {
    var b = e.target.closest('.ipl-tab');
    if (!b || b.disabled) return;
    userTab = b.getAttribute('data-tab');
    showTab(userTab);
  });

  /* ---------- demo mode ---------- */
  var t = 0, timer = null;
  var STAGE_LABEL = function (x) {
    if (x === 0) return 'Toss + XI';
    if (x <= 20) return 'Innings 1, over ' + x;
    if (x === 21) return 'Innings break';
    if (x <= 41) return 'Innings 2, over ' + (x - 21);
    return 'Finished';
  };
  function demoRender() {
    render(buildDemo(t));
    $('dLabel').textContent = STAGE_LABEL(t);
    var sel = $('dJump'), found = false, i;
    for (i = 0; i < sel.options.length; i++) if (+sel.options[i].value === t) { sel.selectedIndex = i; found = true; }
    if (!found) sel.selectedIndex = -1;
  }
  function demoStop() { if (timer) { clearInterval(timer); timer = null; } $('dPlay').textContent = 'Play'; }
  function demoStart() {
    if (t >= 42) t = 0;
    $('dPlay').textContent = 'Pause';
    timer = setInterval(function () { if (t >= 42) { demoStop(); return; } t++; demoRender(); }, DEMO_STEP_MS);
  }
  function initDemo() {
    $('demoBar').classList.remove('d-none');
    $('dPlay').addEventListener('click', function () { timer ? demoStop() : demoStart(); });
    $('dNext').addEventListener('click', function () { demoStop(); t = Math.min(42, t + 1); demoRender(); });
    $('dPrev').addEventListener('click', function () { demoStop(); t = Math.max(0, t - 1); demoRender(); });
    $('dJump').addEventListener('change', function () { demoStop(); t = +this.value; demoRender(); });
    t = 0; demoRender();
  }

  /* ---------- live mode (used once API_BASE is set) ---------- */
  function fetchLive() {
    fetch(API_BASE + '/api/ipl/live', { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(render)
      .catch(function (err) {
        /* keep showing the last data we had */
      });
  }
  function initLive() { fetchLive(); setInterval(fetchLive, POLL_MS); }

  /* ---------- start ---------- */
  if (API_BASE) initLive(); else initDemo();
})();