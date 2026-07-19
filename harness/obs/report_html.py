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
          --ok:#0a7f3f; --bad:#c0392b; --acc:#2456d6; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#16181d; --fg:#e6e6e6; --mut:#9aa; --line:#31353d; --card:#1f2229;
            --ok:#4cc38a; --bad:#e5735f; --acc:#7aa2ff; } }
  * { box-sizing:border-box; }
  body { margin:0; font:14px/1.5 -apple-system,'Apple SD Gothic Neo',sans-serif;
         background:var(--bg); color:var(--fg); }
  header { padding:14px 20px; border-bottom:1px solid var(--line);
           display:flex; gap:16px; align-items:baseline; flex-wrap:wrap; }
  header h1 { font-size:17px; margin:0; }
  header .sub { color:var(--mut); font-size:12px; }
  .filters { padding:10px 20px; display:flex; gap:10px; flex-wrap:wrap;
             border-bottom:1px solid var(--line); }
  select,input[type=text] { background:var(--card); color:var(--fg);
    border:1px solid var(--line); border-radius:6px; padding:5px 8px; font-size:13px; }
  main { display:flex; height:calc(100vh - 110px); }
  #list { width:46%; min-width:420px; overflow:auto; border-right:1px solid var(--line); }
  #detail { flex:1; overflow:auto; padding:18px 22px; }
  table { border-collapse:collapse; width:100%; font-size:12.5px; }
  th,td { padding:6px 9px; text-align:left; border-bottom:1px solid var(--line);
          white-space:nowrap; }
  th { position:sticky; top:0; background:var(--bg); color:var(--mut);
       font-weight:600; font-size:11.5px; }
  tbody tr { cursor:pointer; }
  tbody tr:hover { background:var(--card); }
  tbody tr.sel { background:color-mix(in srgb, var(--acc) 14%, var(--bg)); }
  .ok { color:var(--ok); font-weight:700; } .bad { color:var(--bad); font-weight:700; }
  .chip { display:inline-block; background:var(--card); border:1px solid var(--line);
          border-radius:20px; padding:2px 10px; margin:2px 4px 2px 0; font-size:12px; }
  .q { background:var(--card); border-left:3px solid var(--acc); padding:10px 12px;
       border-radius:6px; margin:8px 0; }
  .answer { white-space:pre-wrap; background:var(--card); padding:12px;
            border-radius:8px; margin:8px 0; }
  .ev { border:1px solid var(--line); border-radius:8px; margin:8px 0; overflow:hidden; }
  .ev .h { padding:6px 10px; background:var(--card); font-size:12px; color:var(--mut);
           display:flex; gap:12px; flex-wrap:wrap; }
  .ev .b { padding:8px 10px; white-space:pre-wrap; font-size:12.5px;
           overflow-x:auto; }
  .role-user .h { border-left:3px solid var(--acc); }
  .role-assistant .h { border-left:3px solid var(--ok); }
  .role-tool .h, .role-system .h { border-left:3px solid var(--mut); }
  .phase { font-weight:700; color:var(--fg); }
  .bar { display:inline-block; height:8px; background:var(--acc); border-radius:2px;
         vertical-align:middle; }
  h2 { font-size:15px; margin:16px 0 6px; } h3 { font-size:13px; margin:14px 0 4px; }
  .mut { color:var(--mut); }
  #summary { padding:10px 20px 4px; font-size:13px; }
  #summary table { width:auto; } #summary td,#summary th { padding:3px 12px 3px 0;
    border:none; white-space:nowrap; }
</style></head><body>
<header><h1>harness observability</h1>
  <span class="sub">생성 __GENERATED__ · 런 <span id="nRuns"></span>개</span></header>
<div id="summary"></div>
<div class="filters">
  <select id="fExp"></select><select id="fCfg"></select><select id="fTask"></select>
  <select id="fOk"><option value="">성공/실패 전체</option>
    <option value="1">성공만</option><option value="0">실패만</option></select>
  <input type="text" id="fText" placeholder="run_id / mode 검색">
</div>
<main>
  <div id="list"><table><thead><tr>
    <th>시각</th><th>mode</th><th>task</th><th>성공</th><th>스텝</th>
    <th>툴콜</th><th>반려</th><th>토큰</th></tr></thead>
    <tbody id="rows"></tbody></table></div>
  <div id="detail"><p class="mut">왼쪽에서 런을 선택하세요.</p></div>
</main>
<script type="application/json" id="data">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const runs = D.runs, suite = D.suite;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;');
const short = t => t ? t.replace('T',' ').slice(5,19) : '';

function fillSelect(el, values, label) {
  el.innerHTML = `<option value="">${label} 전체</option>` +
    [...values].sort().map(v => `<option>${esc(v)}</option>`).join('');
}
const exps = new Set(), cfgs = new Set(), tasks = new Set();
runs.forEach(r => { if (r.meta) { exps.add(r.meta.experiment);
  cfgs.add(r.meta.config); tasks.add(r.meta.task_id); } });
fillSelect($('fExp'), exps, '실험'); fillSelect($('fCfg'), cfgs, '설정');
fillSelect($('fTask'), tasks, '태스크');

function visible() {
  const e=$('fExp').value, c=$('fCfg').value, t=$('fTask').value,
        ok=$('fOk').value, tx=$('fText').value.toLowerCase();
  return runs.filter(r => {
    const m = r.meta;
    if (e && (!m || m.experiment !== e)) return false;
    if (c && (!m || m.config !== c)) return false;
    if (t && (!m || m.task_id !== t)) return false;
    if (ok !== '' && (!m || String(m.success) !== ok)) return false;
    if (tx && !(r.run_id + ' ' + r.mode).toLowerCase().includes(tx)) return false;
    return true;
  });
}

function renderSummary(list) {
  const g = {};
  list.forEach(r => { const m = r.meta; if (!m) return;
    const k = m.experiment + '│' + m.config;
    (g[k] = g[k] || {n:0, ok:0, tok:0}).n++; g[k].ok += m.success;
    g[k].tok += (m.prompt_tokens||0) + (m.completion_tokens||0); });
  const keys = Object.keys(g);
  $('summary').innerHTML = !keys.length ? '' :
    '<table><tr><th>실험│설정</th><th>런</th><th>성공률</th><th>평균 토큰</th></tr>' +
    keys.sort().map(k => { const s = g[k];
      return `<tr><td>${esc(k)}</td><td>${s.n}</td>` +
        `<td>${Math.round(100*s.ok/s.n)}%</td>` +
        `<td>${Math.round(s.tok/s.n).toLocaleString()}</td></tr>`; }).join('') +
    '</table>';
}

let selId = null;
function renderList() {
  const list = visible();
  $('nRuns').textContent = list.length;
  renderSummary(list);
  $('rows').innerHTML = list.map(r => {
    const m = r.meta;
    const okCell = m ? (m.success ? '<span class="ok">O</span>'
                                  : '<span class="bad">X</span>') : '·';
    return `<tr data-id="${r.run_id}" class="${r.run_id===selId?'sel':''}">` +
      `<td>${short(r.ts)}</td><td>${esc(r.mode)}</td>` +
      `<td>${m ? esc(m.task_id) : '·'}</td><td>${okCell}</td>` +
      `<td>${m ? m.steps : r.events.length}</td><td>${m ? m.tool_calls : '·'}</td>` +
      `<td>${m ? m.evidence_bounces : '·'}</td>` +
      `<td>${m ? ((m.prompt_tokens||0)+(m.completion_tokens||0)).toLocaleString() : '·'}</td></tr>`;
  }).join('');
  [...$('rows').children].forEach(tr =>
    tr.onclick = () => { selId = tr.dataset.id; renderList(); renderDetail(); });
}

function evLine(e) {
  const lat = e.latency_ms ? ` · ${e.latency_ms.total.toLocaleString()}ms` : '';
  const mem = e.mem ? ` · rss ${e.mem.rss_mb}MB / avail ${e.mem.avail_mb}MB` : '';
  const maxLat = 30000;
  const bar = e.latency_ms ? `<span class="bar" style="width:${
    Math.min(80, 80*e.latency_ms.total/maxLat)}px"></span>` : '';
  if (e.phase === 'message') {
    const tcs = (e.tool_calls || []).map(tc =>
      `→ ${tc.function.name}(${tc.function.arguments})`).join('\\n');
    const body = [e.content, tcs].filter(Boolean).join('\\n');
    return `<div class="ev role-${e.role}"><div class="h">` +
      `<span class="phase">${e.role}${e.final ? ' · 최종 답' : ''}</span>` +
      `<span>#${e.step}${mem}</span></div>` +
      `<div class="b">${esc(body)}</div></div>`;
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
  let h = `<h2>${esc(r.run_id)} <span class="mut">· ${esc(r.mode)}</span></h2>`;
  if (m) {
    h += `<div>` +
      `<span class="chip">${m.success ? '✅ 성공' : '❌ 실패'}</span>` +
      `<span class="chip">스텝 ${m.steps}</span>` +
      `<span class="chip">툴콜 ${m.tool_calls} (이름 ${m.valid_names}/인자 ${m.valid_args} 유효)</span>` +
      `<span class="chip">수리 ${m.repaired}/${m.retries}</span>` +
      `<span class="chip">차단 ${m.guardrail_blocks} · 반려 ${m.evidence_bounces}` +
      `${m.fallback ? ' · 폴백' : ''}</span>` +
      `<span class="chip">토큰 ${((m.prompt_tokens||0)+(m.completion_tokens||0)).toLocaleString()}</span></div>`;
    if (t) h += `<div class="q"><b>${esc(m.task_id)}</b> (${t.kind}) — ${esc(t.question)}` +
      (t.page_groups?.length ? `<br><span class="mut">기대 그룹: ${
        t.page_groups.map(g => '[' + g.join(' | ') + ']').join(' + ')}</span>` : '') + `</div>`;
    if (m.answer) h += `<h3>최종 답변</h3><div class="answer">${esc(m.answer)}</div>`;
  }
  h += `<h3>타임라인 (${r.events.length} 이벤트)</h3>`;
  h += r.events.length ? r.events.map(evLine).join('')
     : '<p class="mut">트레이스 파일 없음 — sqlite 요약만 존재.</p>';
  $('detail').innerHTML = h;
}

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
