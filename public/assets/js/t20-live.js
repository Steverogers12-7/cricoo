/* t20-live.js - Drives t_20.html (live strip + inplay model monitoring panel) */

(function () {
  'use strict';

  /* ---------- Configuration ---------- */
  // >>> APNA RENDER URL YAHAN DAALO (bina trailing slash ke). Khaali = sirf demo mode.
  var API_BASE = 'https://t20-live-score-api.onrender.com';
  var DEMO = /[?&]demo=1/.test(location.search);   // page?demo=1 -> demo match
  var POLL_MS = 60000;
  var DEMO_STEP_MS = 1500;

  /* ---------- Helpers ---------- */
  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function sum(a) { return a.reduce(function (x, y) { return x + y; }, 0); }
  function cumTo(a, n) { return sum(a.slice(0, n)); }

  /* Missing fields se crash roko */
  function normalize(s) {
    s.teams = s.teams || { a: { code: 'A', name: 'Team A' }, b: { code: 'B', name: 'Team B' } };
    s.model1 = s.model1 || { total: 0 };
    s.inn1 = Object.assign({ runsByOver: [], wktsByOver: [], proj: [], ballsByOver: [] }, s.inn1);
    s.inn2 = Object.assign({ target: 0, runsByOver: [], wktsByOver: [], win: [], ballsByOver: [] }, s.inn2);
    return s;
  }

  /* Team Badge Colors */
  var TEAM_COL = {
    IND: ['#0055a5', '#fff'], AUS: ['#ffcd00', '#000'], ENG: ['#d4202a', '#fff'],
    SA: ['#007a4d', '#fff'], NZ: ['#000000', '#fff'], PAK: ['#006633', '#fff'],
    WI: ['#7b002c', '#fff'], SL: ['#002366', '#ffcd00'], AFG: ['#000000', '#d4202a'],
    RCB: ['#d4202a', '#fff'], CSK: ['#f5c518', '#1b1b1b'], MI: ['#1d4ed8', '#fff']
  };

  function badge(code) {
    var c = TEAM_COL[code] || ['#35506f', '#fff'];
    return '<span class="ipl-badge" style="background:' + c[0] + ';color:' + c[1] + '">' + esc(code) + '</span>';
  }

  /* ---------- DEMO DATA GENERATOR ---------- */
  var D = {
    teams: { a: { code: 'IND', name: 'India' }, b: { code: 'AUS', name: 'Australia' } },
    venue: 'Melbourne Cricket Ground, Melbourne',
    toss: 'India won the toss and elected to bat first',
    inn1Runs: [8, 10, 6, 14, 9, 11, 7, 12, 8, 10, 6, 9, 13, 8, 11, 14, 9, 12, 10, 15],
    inn1Wk:   [0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 1, 0, 1, 1],
    inn2Runs: [9, 8, 12, 7, 10, 11, 6, 9, 8, 10, 7, 11, 8, 10, 9, 12, 8, 11, 9, 7],
    inn2Wk:   [0, 1, 0, 0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 1, 1],
    model1: { total: 188, low: 175, high: 202 }
  };

  function rng(seed) { return function () { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296; }; }
  var BV = [0, 1, 2, 3, 4, 6], BW = [28, 36, 8, 2, 14, 8];
  function pickBall(r) { var t = 0, i; for (i = 0; i < BW.length; i++) t += BW[i]; var x = r() * t; for (i = 0; i < BW.length; i++) { x -= BW[i]; if (x <= 0) return BV[i]; } return 0; }
  function mkBalls(runs, wk, seed) {
    var r = rng(seed * 7919 + 13), extras = [], vals, s, tries = 0, i;
    if (runs >= 3 && seed % 5 === 2) extras.push('Wd'); else if (runs >= 6 && seed % 7 === 3) extras.push('Nb');
    var rem = runs - extras.length, legal = 6 - wk;
    do { vals = []; s = 0; for (i = 0; i < legal; i++) { var v = pickBall(r); vals.push(v); s += v; } tries++; } while (s !== rem && tries < 3000);
    var slots = [0, 1, 2, 3, 4, 5], wpos = {}, out = [], vi = 0;
    for (i = 0; i < wk; i++) { var pi = Math.floor(r() * slots.length); wpos[slots[pi]] = true; slots.splice(pi, 1); }
    for (i = 0; i < 6; i++) out.push(wpos[i] ? 'W' : String(vals[vi++]));
    extras.forEach(function (x) { out.splice(Math.floor(r() * (out.length + 1)), 0, x); });
    return out;
  }

  function projInn1(cum, wk, ov) {
    if (ov >= 20) return cum;
    var rr = ov === 0 ? 8.8 : cum / ov;
    var rate = (0.55 * rr + 0.45 * 8.8) * (1 - 0.038 * wk);
    return Math.round(cum + (20 - ov) * rate);
  }

  function winInn2(cum, wk, ov, target) {
    if (cum >= target) return 100;
    if (ov >= 20 || wk >= 10) return 0;
    var rr = ov === 0 ? 8.5 : cum / ov;
    var rate = (0.5 * rr + 0.5 * 8.5) * (1 - 0.04 * wk);
    var p = cum + (20 - ov) * rate;
    var z = (p - target + 0.5) / (3 + (20 - ov) * 1.1);
    return Math.round(1000 / (1 + Math.exp(-z))) / 10;
  }

  function buildDemo(t) {
    var s = {
      demo: true, updatedAt: new Date().toISOString(), date: new Date().toISOString(), venue: D.venue, toss: D.toss, result: '',
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
      s.result = margin > 0 ? D.teams.a.code + ' won by ' + margin + ' runs' : D.teams.b.code + ' won match';
    }
    // Demo batters / bowler (live me ye API se aayenge)
    if (s.status === 'inn1' || s.status === 'inn2') {
      var cur1 = s.status === 'inn1' ? s.inn1 : s.inn2;
      var rr = sum(cur1.runsByOver), oo = cur1.runsByOver.length, ww = sum(cur1.wktsByOver);
      var nm = s.status === 'inn1' ? ['KL Rahul', 'Vijay', 'M. Starc'] : ['T. Head', 'D. Warner', 'J. Bumrah'];
      s.live = {
        batters: [
          { name: nm[0], runs: Math.round(rr * 0.5), balls: Math.round(oo * 6 * 0.35) },
          { name: nm[1], runs: Math.round(rr * 0.2), balls: Math.round(oo * 6 * 0.16) }
        ],
        bowler: { name: nm[2], runs: Math.round(oo * 1.4), wkts: Math.min(3, Math.floor(ww / 2)) }
      };
    }
    return s;
  }

  function innings(runsBy, wktsBy) {
    var n = runsBy.length;
    return { runs: sum(runsBy), wkts: sum(wktsBy), overs: n };
  }

  /* ---------- Render Live Strip ---------- */
  var STATUS_TEXT = { pre: 'TOSS DONE', inn1: 'LIVE · INNINGS 1', 'break': 'INNINGS BREAK', inn2: 'LIVE · INNINGS 2', done: 'RESULT' };

  function renderStrip(s) {
    var a = s.teams.a, b = s.teams.b;
    var i1 = innings(s.inn1.runsByOver, s.inn1.wktsByOver);
    var i2 = innings(s.inn2.runsByOver, s.inn2.wktsByOver);

    if ($('mVenue')) $('mVenue').textContent = s.venue || '';
    var st = $('mStatus');
    if (st) {
      st.textContent = STATUS_TEXT[s.status] || '';
      st.className = 'ipl-status' + (s.status === 'inn1' || s.status === 'inn2' ? ' is-live' : s.status === 'done' ? ' is-done' : '');
    }

    function teamInner(t, has, runs, wkts, overs, batting, detail) {
      return '<div class="ipl-shead">' + badge(t.code) +
        '<div class="ipl-sname"><div class="nm">' + esc(t.name) + (batting ? '<span class="ipl-bat" title="Batting"></span>' : '') + '</div>' +
          (has
            ? '<div class="ipl-sline"><b>' + runs + '-' + wkts + '</b><span>' + overs + '.0</span></div>'
            : '<div class="ipl-sline"><span>Yet to bat</span></div>') +
        '</div></div>' +
        (detail ? '<div class="ipl-sdetail">' + detail + '</div>' : '');
    }

    function paint(el, extra, batting, overs, inner) {
      if (!el) return;
      el.className = 'ipl-side' + extra + (batting ? ' is-batting' : '');
      el.innerHTML = inner;
    }

    // Batters / bowler: API se s.live = { batters:[{name,runs,balls}], bowler:{name,runs,wkts} } aana chahiye
    var lv = s.live || {};
    function batHtml(arr) {
      return (arr || []).map(function (x) {
        return '<span class="ipl-bt">' + esc(x.name) + ' <b>' + x.runs + '</b>(' + x.balls + ')</span>';
      }).join('');
    }
    function bowlHtml(w) {
      return w ? '<span class="ipl-bt">' + esc(w.name) + ' <b>' + w.runs + '/' + w.wkts + '</b></span>' : '';
    }
    var aDetail = s.status === 'inn1' ? batHtml(lv.batters) : s.status === 'inn2' ? bowlHtml(lv.bowler) : '';
    var bDetail = s.status === 'inn2' ? batHtml(lv.batters) : s.status === 'inn1' ? bowlHtml(lv.bowler) : '';

    var aHas = s.status !== 'pre', bHas = s.status === 'inn2' || s.status === 'done';
    paint($('teamA'), '', s.status === 'inn1', i1.overs,
      teamInner(a, aHas, i1.runs, i1.wkts, i1.overs, s.status === 'inn1', aDetail));
    paint($('teamB'), ' is-right', s.status === 'inn2', i2.overs,
      teamInner(b, bHas, i2.runs, i2.wkts, i2.overs, s.status === 'inn2', bDetail));

    var line = '', chips = [];
    if (s.status === 'pre') {
      line = s.toss || 'Toss done. Match starting shortly.';
      chips.push(['Inplay Pre-Model Target', s.model1.total]);
    } else if (s.status === 'inn1') {
      line = '';
    } else if (s.status === 'break') {
      line = 'Innings break. ' + esc(b.code) + ' need ' + s.inn2.target + ' to win';
      chips.push(['Target', s.inn2.target], ['Innings 1 Score', i1.runs]);
    } else if (s.status === 'inn2') {
      var need = s.inn2.target - i2.runs, balls = 120 - i2.overs * 6;
      line = esc(b.code) + ' need ' + need + ' off ' + balls + ' balls';
      chips.push(['Target', s.inn2.target]);
    } else {
      line = esc(s.result || 'Match finished');
      chips.push(['Final Score', esc(a.code) + ' ' + i1.runs + '/' + i1.wkts + ' · ' + esc(b.code) + ' ' + i2.runs + '/' + i2.wkts]);
    }

    if ($('mLine')) $('mLine').innerHTML = line;
    var sw = $('mWin');
    if (sw) {
      if ((s.status === 'inn2' || s.status === 'done') && s.inn2.win.length) {
        var wn = s.inn2.win[s.inn2.win.length - 1];
        sw.innerHTML = '<div class="top"><span>' + esc(b.code) + ' win <b>' + wn + '%</b></span><span><b>' + (Math.round((100 - wn) * 10) / 10) + '%</b> ' + esc(a.code) + '</span></div>' +
          '<div class="bar"><i style="width:' + wn + '%"></i></div>';
        sw.classList.remove('d-none');
      } else { sw.classList.add('d-none'); }
    }

    if ($('mChips')) $('mChips').innerHTML = chips.map(function (c) { return '<div class="ipl-chip">' + c[0] + ': <b>' + c[1] + '</b></div>'; }).join('');
    renderOvers(s, (s.status === 'inn2' || s.status === 'done') ? s.inn2 : s.inn1);
  }

  /* ---------- Overs Carousel ---------- */
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
    if (!el) return;
    if (!cur || k < 0 || k >= cur.runsByOver.length) { el.innerHTML = ''; return; }
    var runs = cur.runsByOver[k], w = cur.wktsByOver[k], balls = cur.ballsByOver && cur.ballsByOver[k];
    var extras = balls ? balls.filter(function (t) { return /^(Wd|Nb|Lb|B)/.test(t); }).length : 0;
    el.innerHTML = '<div class="top"><b>Over ' + (k + 1) + '</b><span>' + runs + ' runs' + (w ? ' &middot; ' + w + ' wicket' + (w > 1 ? 's' : '') : '') +
      (extras ? ' &middot; ' + extras + ' extra' + (extras > 1 ? 's' : '') : '') + '</span></div>' +
      (balls ? '<div class="ipl-balls">' + balls.map(ballHtml).join('') + '</div>' : '');
  }

  function navState() {
    var sc = $('mOvers'); if (!sc) return;
    if ($('ovPrev')) $('ovPrev').disabled = sc.scrollLeft <= 2;
    if ($('ovNext')) $('ovNext').disabled = sc.scrollLeft + sc.clientWidth >= sc.scrollWidth - 2;
  }

  function renderOvers(s, cur) {
    var n = cur.runsByOver.length, key = (s.status === 'inn2' || s.status === 'done') ? 'i2' : 'i1';
    var sc = $('mOvers'); if (!sc) return;
    var left = sc.scrollLeft;
    var atEnd = ov.first || (sc.scrollWidth - sc.clientWidth - sc.scrollLeft < 6);
    if (ov.key !== key) { ov.key = key; ov.user = false; atEnd = true; left = 0; }
    ov.s = s; ov.cur = cur; ov.first = false;
    if (!ov.user || ov.sel > n - 1) { ov.sel = n - 1; ov.user = false; }
    if ($('ovWrap')) $('ovWrap').classList.toggle('d-none', n === 0);
    if (!n) { sc.innerHTML = ''; if ($('mOverDetail')) $('mOverDetail').innerHTML = ''; return; }
    var html = '', k;
    for (k = 0; k < n; k++) {
      var balls = cur.ballsByOver && cur.ballsByOver[k];
      html += '<div class="ipl-ovgrp"><span class="ipl-ovlbl">Over ' + (k + 1) + '</span>' +
        (balls ? balls.map(ballHtml).join('') : '') +
        '<span class="ipl-ovtot">= ' + cur.runsByOver[k] + '</span></div>';
    }
    if (cur.currentBalls && cur.currentBalls.length) {
      html += '<div class="ipl-ovgrp"><span class="ipl-ovlbl">Over ' + (n + 1) + '</span>' + cur.currentBalls.map(ballHtml).join('') + '</div>';
    }
    sc.innerHTML = html;
    sc.scrollLeft = left;
    if (atEnd) sc.scrollTo({ left: sc.scrollWidth, behavior: 'smooth' });
    overDetail();
    setTimeout(navState, 400);
  }

  /* ---------- Inplay Model Monitoring Render (Clean 2-Box View) ---------- */
  function renderInplayMonitoring(s) {
    var el = $('pane-inplay');
    if (!el) return;

    var i1 = innings(s.inn1.runsByOver, s.inn1.wktsByOver);
    var i2 = innings(s.inn2.runsByOver, s.inn2.wktsByOver);

    var isInn2 = (s.status === 'inn2' || s.status === 'done') && s.inn2.win.length > 0;
    var activeInnings = isInn2 ? i2 : i1;
    var projArr = isInn2 ? s.inn2.win : s.inn1.proj;
    var nOvers = projArr.length;
    var latestVal = nOvers ? projArr[nOvers - 1] : (isInn2 ? 50 : s.model1.total);

    el.innerHTML =
      '<div class="ipl-kpis mb-0">' +
        '<div class="ipl-kpi">' +
          '<div class="v blue">' + activeInnings.runs + '/' + activeInnings.wkts + '</div>' +
          '<div class="n">Current Score</div>' +
        '</div>' +
        '<div class="ipl-kpi">' +
          '<div class="v mint">' + latestVal + (isInn2 ? '%' : '') + '</div>' +
          '<div class="n">' + (isInn2 ? 'Win Probability' : 'Projected Score') + '</div>' +
        '</div>' +
      '</div>';
  }

  /* ---------- Over-by-Over Projection Log Table & Summary Update ---------- */
  var lastLogN = -1;

  function updateProjectionLogsAndSummary(s) {
    var tbody = $('projectionLogBody');
    var badgeEl = $('logStatusBadge');
    var summaryCard = $('matchSummaryCard');
    if (!tbody) return;

    var n = s.inn1.proj ? s.inn1.proj.length : 0;

    if (n === 0) {
      tbody.innerHTML = '<tr id="noDataRow"><td colspan="4" class="text-center text-muted py-3">Match projection log will appear here over by over...</td></tr>';
      if (summaryCard) summaryCard.classList.add('d-none');
      if (badgeEl) { badgeEl.textContent = 'In Progress'; badgeEl.className = 'badge bg-primary'; }
      lastLogN = 0;
      return;
    }

    var html = '', k;
    for (k = 1; k <= n; k++) {
      var cumRuns = cumTo(s.inn1.runsByOver, k);
      var cumWkts = cumTo(s.inn1.wktsByOver, k);
      html += '<tr>' +
        '<td class="text-center fw-bold">Over ' + k + '</td>' +
        '<td>' + esc(s.teams.a.name) + '</td>' +
        '<td>' + cumRuns + '/' + cumWkts + '</td>' +
        '<td class="fw-bold text-warning">' + s.inn1.proj[k - 1] + '</td>' +
      '</tr>';
    }
    tbody.innerHTML = html;

    var wrap = $('logScroll');
    if (wrap && n !== lastLogN) wrap.scrollTop = wrap.scrollHeight;
    lastLogN = n;

    // Innings 1 khatam: summary row (best over sirf 1-12 overs me se)
    if (s.status === 'done' || n >= 20) {
      var actualFinalScore = sum(s.inn1.runsByOver);
      var minError = Infinity;
      var bestOver = -1;
      var bestProj = -1;
      var limit = Math.min(n, 12);

      for (k = 0; k < limit; k++) {
        var err = Math.abs(s.inn1.proj[k] - actualFinalScore);
        if (err < minError) {
          minError = err;
          bestOver = k + 1;
          bestProj = s.inn1.proj[k];
        }
      }

      if (badgeEl) {
        badgeEl.textContent = 'Match Finished';
        badgeEl.className = 'badge bg-success';
      }

      var setTxt = function (id, v) { if ($(id)) $(id).textContent = v; };
      var dt = s.date ? new Date(s.date) : null;
      setTxt('summaryDate', dt && !isNaN(dt) ? dt.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) : '-');
      setTxt('summaryBat', s.teams.a.name);
      setTxt('summaryBowl', s.teams.b.name);
      setTxt('summaryOver', bestOver > 0 ? 'Over ' + bestOver : '-');
      setTxt('summaryScore', bestOver > 0 ? cumTo(s.inn1.runsByOver, bestOver) + '/' + cumTo(s.inn1.wktsByOver, bestOver) : '-');
      setTxt('summaryProj', bestOver > 0 ? bestProj : '-');
      setTxt('summaryActual', actualFinalScore);
      setTxt('summaryError', bestOver > 0 ? (minError === 0 ? 'Exact (0)' : '\u00b1' + minError + ' runs') : '-');

      if (summaryCard) summaryCard.classList.remove('d-none');
    } else {
      if (summaryCard) summaryCard.classList.add('d-none');
      if (badgeEl) { badgeEl.textContent = 'In Progress'; badgeEl.className = 'badge bg-primary'; }
    }
  }

  var lastState = null;
  function render(s) {
    lastState = s;
    var none = !s || s.status === 'none' || !s.teams;
    if ($('mMain')) $('mMain').classList.toggle('d-none', none);
    if ($('mEmpty')) $('mEmpty').classList.toggle('d-none', !none);
    if (none) {
      if ($('mEmpty')) $('mEmpty').innerHTML = '<h3>No match in progress</h3><p>' + esc((s && s.next) || 'Live scores and predictions appear here when the next T20 match starts.') + '</p>';
      if ($('mStatus')) { $('mStatus').textContent = 'NO LIVE MATCH'; $('mStatus').className = 'ipl-status'; }
      return;
    }
    s = normalize(s);
    renderStrip(s);
    renderInplayMonitoring(s);
    updateProjectionLogsAndSummary(s);
  }

  /* ---------- Demo Mode Controls ---------- */
  var t = 10, timer = null;
  var STAGE_LABEL = function (x) {
    if (x === 0) return 'Toss + XI';
    if (x <= 20) return 'Innings 1, over ' + x;
    if (x === 21) return 'Innings break';
    if (x <= 41) return 'Innings 2, over ' + (x - 21);
    return 'Finished';
  };

  function demoRender() {
    render(buildDemo(t));
    if ($('dLabel')) $('dLabel').textContent = STAGE_LABEL(t);
    var sel = $('dJump'); if (!sel) return;
    var found = false, i;
    for (i = 0; i < sel.options.length; i++) if (+sel.options[i].value === t) { sel.selectedIndex = i; found = true; }
    if (!found) sel.selectedIndex = -1;
  }

  function demoStop() { if (timer) { clearInterval(timer); timer = null; } if ($('dPlay')) $('dPlay').textContent = 'Play'; }
  function demoStart() {
    if (t >= 42) t = 0;
    if ($('dPlay')) $('dPlay').textContent = 'Pause';
    timer = setInterval(function () { if (t >= 42) { demoStop(); return; } t++; demoRender(); }, DEMO_STEP_MS);
  }

  function initDemo() {
    if ($('demoBar')) $('demoBar').classList.remove('d-none');
    if ($('dPlay')) $('dPlay').onclick = function () { timer ? demoStop() : demoStart(); };
    if ($('dNext')) $('dNext').onclick = function () { demoStop(); t = Math.min(42, t + 1); demoRender(); };
    if ($('dPrev')) $('dPrev').onclick = function () { demoStop(); t = Math.max(0, t - 1); demoRender(); };
    if ($('dJump')) $('dJump').onchange = function () { demoStop(); t = +this.value; demoRender(); };

    t = 10;
    demoRender();
  }

  /* Live fetch: fail hone par demo NAHI chalta, sirf console me error */
  function fetchLive() {
    fetch(API_BASE + '/api/t20/live', { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (s) { try { render(s); } catch (e) { console.error('render failed', e, s); } })
      .catch(function (e) { console.error('fetch failed', e); });
  }

  function startApp() {
    if ($('mOvers')) {
      $('mOvers').addEventListener('click', function (e) {
        var b = e.target.closest('.ipl-over'); if (!b || !ov.cur) return;
        ov.sel = +b.getAttribute('data-k');
        ov.user = ov.sel !== ov.cur.runsByOver.length - 1;
        Array.prototype.forEach.call($('mOvers').children, function (c) { c.classList.toggle('is-sel', +c.getAttribute('data-k') === ov.sel); });
        overDetail();
      });
      $('mOvers').addEventListener('scroll', navState);
    }
    // strip ko mouse se drag karo (ya touch / shift+wheel)
    var strip = $('mOvers');
    if (strip) {
      var down = false, sx = 0, sl = 0;
      strip.addEventListener('mousedown', function (e) { down = true; sx = e.pageX; sl = strip.scrollLeft; strip.classList.add('is-drag'); });
      window.addEventListener('mouseup', function () { down = false; strip.classList.remove('is-drag'); });
      window.addEventListener('mousemove', function (e) { if (down) strip.scrollLeft = sl - (e.pageX - sx); });
    }

    if (DEMO || !API_BASE) {
      initDemo();
    } else {
      fetchLive();
      setInterval(fetchLive, POLL_MS);
    }
  }

  /* Execution on DOM Ready */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', startApp);
  } else {
    startApp();
  }

  window.addEventListener('resize', function () {
    if (lastState) render(lastState);
  });
})();