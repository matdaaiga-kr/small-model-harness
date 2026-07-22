"""옵저버빌리티 UI — metrics.sqlite + traces/runs/*.jsonl → 자립형 HTML 뷰어.

실험 → 설정 → 태스크 → 런 → 스텝(이벤트/메시지)으로 드릴다운한다.
외부 의존성 없는 단일 파일 산출물 — 브라우저로 열면 끝.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from harness import config

OUT_DEFAULT = config.PROJECT_ROOT / "reports" / "obs.html"


def _agent_rows() -> dict[str, dict]:
    """metrics.sqlite의 런 요약 — run_id로 조인 가능한 메타데이터."""
    if not config.METRICS_DB.exists():
        return {}
    con = sqlite3.connect(config.METRICS_DB)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT * FROM agent_runs ORDER BY ts").fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        con.close()
    return {r["run_id"]: dict(r) for r in rows}


def _suite_map() -> dict[str, dict]:
    from harness.eval.suite import load_suite

    out = {}
    for t in load_suite():
        out[t.id] = {
            "question": t.question,
            "kind": t.kind,
            "pages": t.expect.pages,
            "page_groups": t.expect.page_groups,
            "require_all": t.expect.require_all,
        }
    return out


def _trace_runs() -> list[dict]:
    """트레이스 파일 전부 — 이벤트 리스트로 파싱, 시간 역순."""
    runs = []
    for p in sorted(config.TRACES_DIR.glob("*.jsonl"), reverse=True):
        events = []
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if not events:
            continue
        runs.append({
            "run_id": events[0].get("run_id", p.stem),
            "mode": events[0].get("mode", "?"),
            "ts": events[0].get("ts", ""),
            "events": events,
        })
    return runs


def collect() -> dict:
    meta = _agent_rows()
    runs = _trace_runs()
    traced_ids = {r["run_id"] for r in runs}
    # 트레이스 파일이 지워졌어도 sqlite 행은 남는다 — 메타만으로 노출
    for rid, m in meta.items():
        if rid not in traced_ids:
            runs.append({"run_id": rid, "mode": f"ablation:{m['experiment']}:{m['config']}",
                         "ts": m["ts"], "events": []})
    runs.sort(key=lambda r: r["ts"], reverse=True)
    for r in runs:
        r["meta"] = meta.get(r["run_id"])
    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "suite": _suite_map(),
        "runs": runs,
    }


_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<title>harness obs</title>
<style>
  :root { --bg:#fff; --fg:#1a1a1a; --mut:#777; --line:#e3e3e3; --card:#f7f7f8;
          --ok:#0a7f3f; --bad:#c0392b; --acc:#2456d6; --warn:#b26a00; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#16181d; --fg:#e6e6e6; --mut:#9aa; --line:#31353d; --card:#1f2229;
            --ok:#4cc38a; --bad:#e5735f; --acc:#7aa2ff; --warn:#e0a95c; } }
  * { box-sizing:border-box; }
  body { margin:0; font:14px/1.5 -apple-system,'Apple SD Gothic Neo',sans-serif;
         background:var(--bg); color:var(--fg);
         display:flex; flex-direction:column; height:100vh; }
  header { padding:12px 20px; border-bottom:1px solid var(--line);
           display:flex; gap:16px; align-items:baseline; flex-wrap:wrap; flex:none; }
  header h1 { font-size:17px; margin:0; }
  header .sub { color:var(--mut); font-size:12px; }
  header kbd { background:var(--card); border:1px solid var(--line); border-radius:4px;
               padding:0 5px; font-size:11px; }
  .filters { padding:8px 20px; display:flex; gap:8px; flex-wrap:wrap;
             border-bottom:1px solid var(--line); align-items:center; flex:none; }
  select,input[type=text] { background:var(--card); color:var(--fg);
    border:1px solid var(--line); border-radius:6px; padding:5px 8px; font-size:13px; }
  #fReset { background:none; border:none; color:var(--acc); cursor:pointer;
            font-size:12.5px; padding:4px; }
  /* ── 대시보드 ─────────────────────────────────────────────── */
  #dash { border-bottom:1px solid var(--line); flex:none; max-height:44vh; overflow:auto; }
  #dash > summary { cursor:pointer; padding:8px 20px; font-size:12.5px;
                    color:var(--mut); user-select:none; }
  #dashBody { display:flex; gap:28px; flex-wrap:wrap; padding:4px 20px 14px; }
  .exp-block h3 { font-size:13px; margin:8px 0 6px; }
  .exp-block h3 .mut { font-weight:400; }
  .barrow { display:flex; align-items:center; gap:8px; margin:3px 0; cursor:pointer;
            font-size:12.5px; }
  .barrow:hover .blabel { color:var(--acc); }
  .blabel { width:86px; text-align:right; flex:none;
            overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .btrack { width:180px; height:13px; background:var(--card); border-radius:3px;
            flex:none; overflow:hidden; }
  .bfill { display:block; height:100%; background:var(--acc); }
  .bfill.hi { background:var(--ok); } .bfill.lo { background:var(--bad); }
  .bfill.mid { background:var(--warn); }
  .bval { color:var(--mut); font-size:12px; white-space:nowrap; }
  table.matrix { border-collapse:collapse; font-size:11.5px; margin-top:8px; }
  table.matrix th, table.matrix td { border:1px solid var(--line); padding:3px 7px;
    text-align:center; white-space:nowrap; }
  table.matrix th { color:var(--mut); font-weight:600; background:var(--bg); }
  table.matrix td.mcell { cursor:pointer; }
  table.matrix td.mcell:hover { outline:2px solid var(--acc); outline-offset:-2px; }
  .c100 { background:color-mix(in srgb, var(--ok) 34%, var(--bg)); }
  .c66  { background:color-mix(in srgb, var(--ok) 16%, var(--bg)); }
  .c33  { background:color-mix(in srgb, var(--bad) 16%, var(--bg)); }
  .c0   { background:color-mix(in srgb, var(--bad) 32%, var(--bg)); }
  /* ── 리스트 / 상세 ────────────────────────────────────────── */
  main { display:flex; flex:1; min-height:0; }
  #list { width:44%; min-width:400px; overflow:auto; border-right:1px solid var(--line); }
  #detail { flex:1; overflow:auto; padding:0 22px 24px; }
  table.runs { border-collapse:collapse; width:100%; font-size:12.5px; }
  table.runs th, table.runs td { padding:5px 8px; text-align:left;
    border-bottom:1px solid var(--line); white-space:nowrap; }
  table.runs th { position:sticky; top:0; background:var(--bg); color:var(--mut);
    font-weight:600; font-size:11.5px; cursor:pointer; user-select:none; z-index:1; }
  table.runs th:hover { color:var(--acc); }
  table.runs tbody tr { cursor:pointer; }
  table.runs tbody tr:hover { background:var(--card); }
  table.runs tbody tr.sel { background:color-mix(in srgb, var(--acc) 14%, var(--bg)); }
  .ok { color:var(--ok); font-weight:700; } .bad { color:var(--bad); font-weight:700; }
  .tag { display:inline-block; font-size:11px; border:1px solid var(--line);
         border-radius:4px; padding:0 5px; background:var(--card); color:var(--mut); }
  .dhead { position:sticky; top:0; background:var(--bg); padding:14px 0 8px;
           border-bottom:1px solid var(--line); z-index:2; }
  .dhead h2 { font-size:14.5px; margin:0 0 6px; word-break:break-all; }
  .chip { display:inline-block; background:var(--card); border:1px solid var(--line);
          border-radius:20px; padding:2px 10px; margin:2px 4px 2px 0; font-size:12px; }
  .chip.good { border-color:var(--ok); color:var(--ok); }
  .chip.fail { border-color:var(--bad); color:var(--bad); }
  .grp { display:inline-block; border-radius:6px; padding:3px 9px; margin:3px 5px 3px 0;
         font-size:12px; border:1px solid; }
  .grp.hit { border-color:var(--ok); background:color-mix(in srgb, var(--ok) 10%, var(--bg)); }
  .grp.miss { border-color:var(--bad); background:color-mix(in srgb, var(--bad) 10%, var(--bg)); }
  .q { background:var(--card); border-left:3px solid var(--acc); padding:10px 12px;
       border-radius:6px; margin:10px 0; }
  .answer { white-space:pre-wrap; background:var(--card); padding:12px;
            border-radius:8px; margin:8px 0; word-break:break-word; }
  .cite { color:var(--acc); font-weight:600; }
  .ev { border:1px solid var(--line); border-radius:8px; margin:8px 0; overflow:hidden; }
  .ev .h { padding:5px 10px; background:var(--card); font-size:12px; color:var(--mut);
           display:flex; gap:12px; flex-wrap:wrap; align-items:center; }
  .ev .b { padding:8px 10px; white-space:pre-wrap; font-size:12.5px;
           overflow-x:auto; word-break:break-word; }
  .ev details.fold > summary { padding:6px 10px; font-size:12px; color:var(--acc);
    cursor:pointer; user-select:none; }
  .role-user { border-left:3px solid var(--acc); }
  .role-assistant { border-left:3px solid var(--ok); }
  .role-tool { border-left:3px solid var(--warn); }
  .role-system { border-left:3px solid var(--mut); }
  .phase { font-weight:700; color:var(--fg); }
  .bar { display:inline-block; height:8px; background:var(--acc); border-radius:2px;
         vertical-align:middle; }
  h3 { font-size:13px; margin:16px 0 4px; }
  .mut { color:var(--mut); }
</style></head><body>
<header><h1>harness observability</h1>
  <span class="sub">생성 __GENERATED__ · 표시 <span id="nRuns"></span> / 전체
    <span id="nAll"></span>런 · <kbd>↑</kbd><kbd>↓</kbd> 런 이동 ·
    막대/매트릭스 셀 클릭 = 필터</span></header>
<div class="filters">
  <select id="fExp"></select><select id="fCfg"></select><select id="fTask"></select>
  <select id="fOk"><option value="">성공/실패 전체</option>
    <option value="1">성공만</option><option value="0">실패만</option></select>
  <input type="text" id="fText" placeholder="run_id / mode / 답변 검색">
  <button id="fReset">필터 초기화</button>
</div>
<details id="dash" open><summary>대시보드 — 성공률 사다리 · 설정 × 태스크 매트릭스</summary>
  <div id="dashBody"></div></details>
<main>
  <div id="list"><table class="runs"><thead><tr>
    <th data-k="ts">시각</th><th data-k="exp">실험</th><th data-k="cfg">설정</th>
    <th data-k="task">task</th><th data-k="ok">성공</th><th data-k="steps">스텝</th>
    <th data-k="calls">툴콜</th><th data-k="bounce">반려</th><th data-k="tok">토큰</th>
    </tr></thead>
    <tbody id="rows"></tbody></table></div>
  <div id="detail"><p class="mut" style="padding-top:14px">왼쪽에서 런을 선택하세요.</p></div>
</main>
<script type="application/json" id="data">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const runs = D.runs, suite = D.suite;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const short = t => t ? t.replace('T',' ').slice(5,19) : '';
const tokOf = m => (m.prompt_tokens||0) + (m.completion_tokens||0);
// 하네스 사다리 순서 — 대시보드에서 설정을 이 순서로 정렬한다
const LADDER = ['raw','baseline','+validator','+retry','full','control','+planner',
                '+evidence','+conditional'];
const rank = c => { const i = LADDER.indexOf(c); return i < 0 ? 99 : i; };

function fillSelect(el, counts, label) {
  const keys = Object.keys(counts).sort((a,b) =>
    el.id === 'fCfg' ? (rank(a)-rank(b) || a.localeCompare(b)) : a.localeCompare(b));
  el.innerHTML = `<option value="">${label} 전체</option>` +
    keys.map(v => `<option value="${esc(v)}">${esc(v)} (${counts[v]})</option>`).join('');
}
const cnt = f => runs.reduce((a,r) => { if (r.meta) {
  const k = r.meta[f]; a[k] = (a[k]||0)+1; } return a; }, {});
fillSelect($('fExp'), cnt('experiment'), '실험');
fillSelect($('fCfg'), cnt('config'), '설정');
fillSelect($('fTask'), cnt('task_id'), '태스크');
$('nAll').textContent = runs.length;

function visible() {
  const e=$('fExp').value, c=$('fCfg').value, t=$('fTask').value,
        ok=$('fOk').value, tx=$('fText').value.toLowerCase();
  return runs.filter(r => {
    const m = r.meta;
    if (e && (!m || m.experiment !== e)) return false;
    if (c && (!m || m.config !== c)) return false;
    if (t && (!m || m.task_id !== t)) return false;
    if (ok !== '' && (!m || String(m.success) !== ok)) return false;
    if (tx && !((r.run_id + ' ' + r.mode + ' ' + (m && m.answer || ''))
        .toLowerCase().includes(tx))) return false;
    return true;
  });
}

// ── 정렬 ──────────────────────────────────────────────────────
let sortKey = 'ts', sortDir = -1;
function sortVal(r, k) {
  const m = r.meta;
  switch (k) {
    case 'ts': return r.ts;
    case 'exp': return m ? m.experiment : r.mode;
    case 'cfg': return m ? m.config : '';
    case 'task': return m ? m.task_id : '';
    case 'ok': return m ? m.success : -1;
    case 'steps': return m ? m.steps : r.events.length;
    case 'calls': return m ? m.tool_calls : -1;
    case 'bounce': return m ? m.evidence_bounces : -1;
    case 'tok': return m ? tokOf(m) : -1;
  }
}
document.querySelectorAll('table.runs th').forEach(th => th.onclick = () => {
  const k = th.dataset.k;
  if (sortKey === k) sortDir = -sortDir; else { sortKey = k; sortDir = k==='ts'?-1:1; }
  renderList();
});

// ── 대시보드: 사다리 바 + 설정×태스크 매트릭스 ────────────────
function setFilter(exp, cfg, task) {
  $('fExp').value = exp || ''; $('fCfg').value = cfg || '';
  $('fTask').value = task || ''; renderList();
}
function renderDash(list) {
  const byExp = {};
  list.forEach(r => { const m = r.meta; if (!m) return;
    const e = byExp[m.experiment] = byExp[m.experiment] || {};
    const c = e[m.config] = e[m.config] || { n:0, ok:0, tok:0, tasks:{} };
    c.n++; c.ok += m.success; c.tok += tokOf(m);
    const t = c.tasks[m.task_id] = c.tasks[m.task_id] || { n:0, ok:0 };
    t.n++; t.ok += m.success; });
  $('dashBody').innerHTML = Object.keys(byExp).sort().map(exp => {
    const cfgsO = Object.keys(byExp[exp]).sort((a,b) =>
      rank(a)-rank(b) || a.localeCompare(b));
    const bars = cfgsO.map(cfg => { const s = byExp[exp][cfg];
      const p = Math.round(100*s.ok/s.n);
      const cls = p >= 75 ? 'hi' : p >= 40 ? 'mid' : 'lo';
      return `<div class="barrow" data-exp="${esc(exp)}" data-cfg="${esc(cfg)}">` +
        `<span class="blabel" title="${esc(cfg)}">${esc(cfg)}</span>` +
        `<span class="btrack"><span class="bfill ${cls}" style="width:${p}%"></span></span>` +
        `<span class="bval">${p}% (${s.ok}/${s.n}) · ${
          Math.round(s.tok/s.n).toLocaleString()} tok</span></div>`; }).join('');
    const taskIds = [...new Set(cfgsO.flatMap(c =>
      Object.keys(byExp[exp][c].tasks)))].sort();
    let matrix = '';
    if (taskIds.length > 1) {
      matrix = `<table class="matrix"><tr><th></th>` + taskIds.map(t =>
        `<th title="${esc((suite[t]||{}).question||'')}">${esc(t.replace('q-',''))}</th>`
        ).join('') + `</tr>` +
        cfgsO.map(cfg => `<tr><th>${esc(cfg)}</th>` + taskIds.map(t => {
          const s = byExp[exp][cfg].tasks[t];
          if (!s) return '<td></td>';
          const p = 100*s.ok/s.n;
          const cls = p===100?'c100':p>=50?'c66':p>0?'c33':'c0';
          return `<td class="mcell ${cls}" data-exp="${esc(exp)}"` +
            ` data-cfg="${esc(cfg)}" data-task="${esc(t)}">${s.ok}/${s.n}</td>`;
        }).join('') + `</tr>`).join('') + `</table>`;
    }
    return `<div class="exp-block"><h3>${esc(exp)}</h3>${bars}${matrix}</div>`;
  }).join('') || '<p class="mut">표시할 실험 런이 없습니다.</p>';
  document.querySelectorAll('.barrow').forEach(el => el.onclick = () =>
    setFilter(el.dataset.exp, el.dataset.cfg, ''));
  document.querySelectorAll('.mcell').forEach(el => el.onclick = () =>
    setFilter(el.dataset.exp, el.dataset.cfg, el.dataset.task));
}

// ── 런 리스트 ─────────────────────────────────────────────────
let selId = null, curVis = [];
function renderList() {
  let list = visible();
  list = list.slice().sort((a,b) => {
    const x = sortVal(a, sortKey), y = sortVal(b, sortKey);
    return (x < y ? -1 : x > y ? 1 : 0) * sortDir; });
  curVis = list;
  $('nRuns').textContent = list.length;
  renderDash(list);
  $('rows').innerHTML = list.map(r => {
    const m = r.meta;
    const okCell = m ? (m.success ? '<span class="ok">O</span>'
                                  : '<span class="bad">X</span>') : '·';
    return `<tr data-id="${r.run_id}" class="${r.run_id===selId?'sel':''}">` +
      `<td>${short(r.ts)}</td>` +
      `<td>${m ? esc(m.experiment) : `<span class="tag">${esc(r.mode)}</span>`}</td>` +
      `<td>${m ? esc(m.config) : '·'}</td>` +
      `<td>${m ? esc(m.task_id) : '·'}</td><td>${okCell}</td>` +
      `<td>${m ? m.steps : r.events.length}</td><td>${m ? m.tool_calls : '·'}</td>` +
      `<td>${m && m.evidence_bounces ? m.evidence_bounces : ''}</td>` +
      `<td>${m ? tokOf(m).toLocaleString() : '·'}</td></tr>`;
  }).join('');
  [...$('rows').children].forEach(tr =>
    tr.onclick = () => { selId = tr.dataset.id; renderList(); renderDetail(); });
}

// ── 상세 ─────────────────────────────────────────────────────
function hlAnswer(text) {
  return esc(text).replace(/\\[\\[([^\\]]+)\\]\\]/g, '<span class="cite">[[$1]]</span>');
}
function gradePanel(t, answer) {
  if (!t || t.kind === 'probe')
    return t ? '<p class="mut">probe 태스크 — 채점: 환각 툴콜 없음(이름 유효율 100%)</p>' : '';
  const ans = (answer || '').toLowerCase();
  let groups = t.page_groups && t.page_groups.length ? t.page_groups
    : t.require_all ? (t.pages||[]).map(p => [p])
    : (t.pages && t.pages.length ? [t.pages] : []);
  if (!groups.length) return '';
  return '<h3>채점 — 그룹마다 1개 이상 인용해야 성공</h3>' + groups.map(g => {
    const hit = g.find(p => ans.includes(p.toLowerCase()));
    return hit
      ? `<span class="grp hit">✓ ${esc(hit)}</span>`
      : `<span class="grp miss">✗ ${g.map(esc).join(' | ')}</span>`;
  }).join('');
}
function fold(bodyHtml, len, open) {
  if (len <= 700) return `<div class="b">${bodyHtml}</div>`;
  return `<details class="fold"${open ? ' open' : ''}><summary>` +
    `${len.toLocaleString()}자 — 펼치기/접기</summary>` +
    `<div class="b">${bodyHtml}</div></details>`;
}
function evLine(e, maxLat) {
  const lat = e.latency_ms ? ` · ${e.latency_ms.total.toLocaleString()}ms` : '';
  const mem = e.mem ? ` · avail ${e.mem.avail_mb}MB` : '';
  const bar = e.latency_ms && maxLat ? `<span class="bar" style="width:${
    Math.max(2, Math.round(120*e.latency_ms.total/maxLat))}px"></span>` : '';
  if (e.phase === 'message') {
    const tcs = (e.tool_calls || []).map(tc =>
      `→ ${tc.function.name}(${tc.function.arguments})`).join('\\n');
    const body = [e.content, tcs].filter(Boolean).join('\\n');
    return `<div class="ev role-${e.role}"><div class="h">` +
      `<span class="phase">${e.role}${e.final ? ' · 최종 답' : ''}</span>` +
      `<span>#${e.step}${mem}</span></div>` +
      fold(e.final ? hlAnswer(body) : esc(body), body.length, e.role !== 'tool') +
      `</div>`;
  }
  let info = '';
  if (e.phase === 'llm') info = `prompt ${e.tokens?.prompt} · completion ${
    e.tokens?.completion} · ${e.tok_per_s} tok/s`;
  else if (e.phase === 'retrieve') info = `${e.op}${e.hits ? ' → ' +
    e.hits.map(h => h.split('#')[0]).join(', ') : ''}`;
  else if (e.phase === 'tool_call') info = e.tool
    ? `${e.tool.name} · name ${e.tool.valid_name ? '유효' : '무효'} · args ${
        e.tool.valid_args ? '유효' : '무효'}` +
      (e.guardrail?.repeat_blocked ? ' · 반복차단' : '')
    : Object.entries(e.guardrail || {}).filter(([,v]) => v).map(([k]) => k).join(',') +
      (e.unread ? ` · 미확인: ${e.unread.join(', ')}` : '');
  else if (e.phase === 'assemble') info = `${e.op} · ${JSON.stringify(e.context)}`;
  return `<div class="ev"><div class="h"><span class="phase">${e.phase}</span>` +
    `<span>#${e.step}${lat}${mem}</span>${bar}</div>` +
    (info ? `<div class="b mut">${esc(info)}</div>` : '') + `</div>`;
}

function renderDetail() {
  const r = runs.find(x => x.run_id === selId);
  if (!r) return;
  const m = r.meta, t = m && suite[m.task_id];
  let dur = '';
  if (r.events.length > 1) {
    const s = (new Date(r.events[r.events.length-1].ts) - new Date(r.events[0].ts))/1000;
    if (s > 0) dur = `<span class="chip">소요 ${s >= 60
      ? Math.floor(s/60)+'분 '+Math.round(s%60)+'초' : s.toFixed(1)+'초'}</span>`;
  }
  let h = `<div class="dhead"><h2>${esc(r.run_id)}` +
    ` <span class="mut">· ${esc(r.mode)}</span></h2>`;
  if (m) {
    h += `<div>` +
      `<span class="chip ${m.success ? 'good' : 'fail'}">${
        m.success ? '성공' : '실패'}</span>` + dur +
      `<span class="chip">스텝 ${m.steps}</span>` +
      `<span class="chip">툴콜 ${m.tool_calls} (이름 ${m.valid_names}/인자 ${
        m.valid_args} 유효)</span>` +
      `<span class="chip">수리 ${m.repaired}/${m.retries}</span>` +
      `<span class="chip">차단 ${m.guardrail_blocks} · 반려 ${m.evidence_bounces}` +
      `${m.fallback ? ' · 폴백' : ''}</span>` +
      `<span class="chip">토큰 ${tokOf(m).toLocaleString()}</span></div>`;
  } else { h += dur; }
  h += `</div>`;
  if (m) {
    if (t) h += `<div class="q"><b>${esc(m.task_id)}</b> (${t.kind}) — ${
      esc(t.question)}</div>`;
    h += gradePanel(t, m.answer);
    if (m.answer) h += `<h3>최종 답변</h3><div class="answer">${
      hlAnswer(m.answer)}</div>`;
  }
  const maxLat = Math.max(0, ...r.events.map(e => e.latency_ms?.total || 0));
  h += `<h3>타임라인 (${r.events.length} 이벤트)</h3>`;
  h += r.events.length ? r.events.map(e => evLine(e, maxLat)).join('')
     : '<p class="mut">트레이스 파일 없음 — sqlite 요약만 존재.</p>';
  $('detail').innerHTML = h;
  $('detail').scrollTop = 0;
}

// ── 키보드 탐색: ↑/↓ (j/k) 로 런 이동 ─────────────────────────
window.addEventListener('keydown', e => {
  if (['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName)) return;
  const dn = e.key === 'ArrowDown' || e.key === 'j',
        up = e.key === 'ArrowUp' || e.key === 'k';
  if (!dn && !up) return;
  if (!curVis.length) return;
  let i = curVis.findIndex(r => r.run_id === selId);
  i = i < 0 ? 0 : Math.min(curVis.length - 1, Math.max(0, i + (dn ? 1 : -1)));
  selId = curVis[i].run_id;
  renderList(); renderDetail();
  document.querySelector('tr.sel')?.scrollIntoView({ block: 'nearest' });
  e.preventDefault();
});

$('fReset').onclick = () => { ['fExp','fCfg','fTask','fOk'].forEach(id =>
  $(id).value = ''); $('fText').value = ''; renderList(); };
['fExp','fCfg','fTask','fOk','fText'].forEach(id => $(id).oninput = renderList);
renderList();
</script></body></html>
"""


def build(out: Path | None = None) -> Path:
    data = collect()
    out = out or OUT_DEFAULT
    out.parent.mkdir(exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    out.write_text(
        _HTML.replace("__GENERATED__", data["generated"]).replace("__DATA__", payload),
        encoding="utf-8",
    )
    return out
