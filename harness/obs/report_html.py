"""옵저버빌리티 UI — metrics.sqlite + traces/runs/*.jsonl → 자립형 HTML 뷰어.

좌측 사이드바에서 실험을 선택하면 그 실험 전용 화면(설명·요약 차트·필터·
런 목록/상세)이 표시된다. 외부 의존성 없는 단일 파일 산출물 — 브라우저로 열면 끝.
스타일은 shadcn/ui 디자인 토큰(zinc)을 내장 CSS로 구현했다.
LLM 용어를 모르는 사람도 읽을 수 있게 용어 사전·툴팁·도움말을 내장한다.
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
            "rubric": t.expect.rubric,
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
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>하네스 실험 뷰어</title>
<style>
  /* shadcn/ui 디자인 토큰(zinc 테마)을 내장 CSS로 구현 — 단일 파일·오프라인 유지 */
  :root { --bg:#ffffff; --fg:#09090b; --mut:#71717a; --line:#e4e4e7; --card:#f4f4f5;
          --ok:#059669; --bad:#dc2626; --acc:#2563eb; --warn:#d97706;
          --fs:14px; --radius:10px;
          --shadow-sm:0 1px 2px 0 rgb(0 0 0 / .05);
          --ring:color-mix(in srgb, var(--acc) 35%, transparent); }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#09090b; --fg:#fafafa; --mut:#a1a1aa; --line:#27272a; --card:#18181b;
            --ok:#34d399; --bad:#f87171; --acc:#60a5fa; --warn:#fbbf24;
            --shadow-sm:0 1px 2px 0 rgb(0 0 0 / .4); } }
  * { box-sizing:border-box; }
  body { margin:0; font:var(--fs)/1.6 ui-sans-serif,-apple-system,'Pretendard',
         'Apple SD Gothic Neo','Segoe UI',sans-serif;
         background:var(--bg); color:var(--fg);
         -webkit-font-smoothing:antialiased;
         display:flex; height:100vh; }
  button { font:inherit; }
  .mut { color:var(--mut); }
  .mono { font-family:ui-monospace,'SF Mono',monospace; font-size:.92em; }
  /* ── 좌측 사이드바: 실험 탭 ───────────────────────────────── */
  #side { width:236px; flex:none; border-right:1px solid var(--line);
          display:flex; flex-direction:column; background:
          color-mix(in srgb, var(--card) 45%, var(--bg)); }
  .side-h { padding:16px 16px 10px; }
  .side-h h1 { font-size:1.05em; font-weight:650; letter-spacing:-.01em; margin:0; }
  .side-h .sub { font-size:.82em; }
  #nav { flex:1; overflow:auto; padding:4px 8px; }
  .navlabel { padding:10px 8px 4px; font-size:.76em; font-weight:600;
              color:var(--mut); text-transform:uppercase; letter-spacing:.05em; }
  .navitem { padding:8px 10px; border-radius:8px; cursor:pointer; margin:2px 0;
             border:1px solid transparent; transition:background .12s; }
  .navitem:hover { background:var(--card); }
  .navitem.active { background:var(--bg); border-color:var(--line);
                    box-shadow:var(--shadow-sm); }
  .navitem.active .nt { color:var(--acc); }
  .nt { font-weight:600; font-size:.92em; line-height:1.35; }
  .ns { font-size:.8em; margin-top:1px; }
  .side-f { padding:10px 12px 14px; border-top:1px solid var(--line);
            display:flex; flex-direction:column; gap:6px; }
  .side-f button { background:var(--bg); color:var(--fg); border:1px solid var(--line);
    border-radius:8px; padding:5px 10px; font-size:.86em; font-weight:500;
    cursor:pointer; box-shadow:var(--shadow-sm); text-align:left;
    transition:background .15s; }
  .side-f button:hover { background:var(--card); }
  .fsrow { display:flex; gap:6px; }
  .fsrow button { flex:1; text-align:center; }
  .gen { font-size:.76em; padding-top:2px; }
  /* ── 우측 컨텐츠 ─────────────────────────────────────────── */
  #content { flex:1; min-width:0; display:flex; flex-direction:column; }
  #exphead { padding:14px 24px 10px; border-bottom:1px solid var(--line); flex:none; }
  #exphead h2 { font-size:1.12em; font-weight:650; letter-spacing:-.01em;
                margin:0 0 2px; display:flex; gap:8px; align-items:center;
                flex-wrap:wrap; }
  #expDesc { color:var(--mut); font-size:.9em; margin:0; max-width:900px; }
  /* ── 실험 요약: 좌(성공률·매트릭스) / 우(설정 설명) 2열 상시 표시 ── */
  #dash { border-bottom:1px solid var(--line); flex:none; max-height:46vh;
          overflow:auto; }
  #dashBody { padding:12px 24px 16px; display:flex; gap:12px 40px; flex-wrap:wrap;
              align-items:flex-start; }
  .dashcol { min-width:0; }
  .dashcol.ladders { flex:1; min-width:300px; max-width:660px; }
  .dashcol h4 { margin:0 0 6px; font-size:.78em; font-weight:600; color:var(--mut);
                text-transform:uppercase; letter-spacing:.05em; }
  .dashcol h4:not(:first-child) { margin-top:14px; }
  .dashhint { color:var(--mut); font-size:.82em; margin:10px 0 0; }
  .barrow { display:flex; align-items:center; gap:10px; margin:5px 0; cursor:pointer;
            font-size:.9em; }
  .barrow:hover .blabel { color:var(--acc); }
  .blabel { width:92px; text-align:right; flex:none;
            font-family:ui-monospace,'SF Mono',monospace; font-size:.88em;
            border-bottom:1px dotted var(--mut); overflow:hidden;
            text-overflow:ellipsis; white-space:nowrap; cursor:help; }
  .btrack { width:190px; height:10px; background:var(--card); border-radius:99px;
            flex:none; overflow:hidden; border:1px solid var(--line); }
  .bfill { display:block; height:100%; background:var(--acc); border-radius:99px; }
  .bfill.hi { background:var(--ok); } .bfill.lo { background:var(--bad); }
  .bfill.mid { background:var(--warn); }
  .bval { color:var(--mut); font-size:.86em; white-space:nowrap; }
  table.matrix { border-collapse:separate; border-spacing:0; font-size:.84em;
    border:1px solid var(--line); border-radius:8px; overflow:hidden; }
  table.matrix th, table.matrix td { border-right:1px solid var(--line);
    border-bottom:1px solid var(--line); padding:3px 8px;
    text-align:center; white-space:nowrap; }
  table.matrix tr > :last-child { border-right:none; }
  table.matrix tr:last-child > * { border-bottom:none; }
  table.matrix th { color:var(--mut); font-weight:600; background:var(--card); }
  table.matrix td.mcell { cursor:pointer; }
  table.matrix td.mcell:hover { outline:2px solid var(--acc); outline-offset:-2px; }
  .c100 { background:color-mix(in srgb, var(--ok) 34%, var(--bg)); }
  .c66  { background:color-mix(in srgb, var(--ok) 16%, var(--bg)); }
  .c33  { background:color-mix(in srgb, var(--bad) 16%, var(--bg)); }
  .c0   { background:color-mix(in srgb, var(--bad) 32%, var(--bg)); }
  .ladder-step { display:flex; gap:12px; margin:5px 0; align-items:baseline; }
  .ladder-step b { flex:none; width:96px; text-align:right; color:var(--acc);
                   font-family:ui-monospace,'SF Mono',monospace; font-size:.92em; }
  .ladder-step span { color:var(--mut); font-size:.9em; line-height:1.5; }
  /* ── 필터 ─────────────────────────────────────────────────── */
  .filters { padding:9px 24px; display:flex; gap:8px; flex-wrap:wrap;
             border-bottom:1px solid var(--line); align-items:center; flex:none; }
  select,input[type=text] { background:var(--bg); color:var(--fg); font:inherit;
    border:1px solid var(--line); border-radius:8px; padding:4px 10px;
    font-size:.9em; box-shadow:var(--shadow-sm); transition:border-color .15s; }
  select:hover,input[type=text]:hover { border-color:var(--mut); }
  select:focus-visible,input[type=text]:focus-visible {
    outline:2px solid var(--ring); outline-offset:1px; }
  input[type=text]::placeholder { color:var(--mut); }
  #fReset { background:none; border:none; color:var(--acc); cursor:pointer;
            font-size:.88em; font-weight:500; padding:4px 6px; border-radius:6px; }
  #fReset:hover { background:var(--card); }
  #cnt { font-size:.85em; margin-left:auto; }
  /* ── 리스트 / 상세 ────────────────────────────────────────── */
  main { display:flex; flex:1; min-height:0; }
  #list { width:42%; min-width:260px; overflow:auto; }
  #split { flex:none; width:5px; cursor:col-resize; background:var(--line);
           opacity:.6; transition:background .15s; }
  #split:hover { background:var(--acc); opacity:1; }
  #detail { flex:1; min-width:280px; overflow:auto; padding:0 24px 28px; }
  .dwrap { max-width:940px; }
  table.runs { border-collapse:collapse; width:100%; font-size:.9em; }
  table.runs th, table.runs td { padding:7px 10px; text-align:left;
    border-bottom:1px solid var(--line); white-space:nowrap; }
  table.runs th { position:sticky; top:0; background:var(--bg); color:var(--mut);
    font-weight:500; font-size:.82em; cursor:pointer; user-select:none; z-index:1; }
  table.runs th[title] { text-decoration:underline dotted; text-underline-offset:3px; }
  table.runs th:hover { color:var(--fg); }
  table.runs tbody tr { cursor:pointer; transition:background .1s; }
  table.runs tbody tr:hover { background:var(--card); }
  table.runs tbody tr.sel { background:color-mix(in srgb, var(--acc) 13%, var(--bg)); }
  .ok { color:var(--ok); font-weight:700; } .bad { color:var(--bad); font-weight:700; }
  .tag { display:inline-block; font-size:.78em; font-weight:500;
         border:1px solid var(--line); border-radius:99px; padding:1px 8px;
         background:var(--card); color:var(--mut); }
  .tag.qa { border-color:color-mix(in srgb, var(--acc) 45%, transparent);
            color:var(--acc); background:color-mix(in srgb, var(--acc) 8%, var(--bg)); }
  .tag.probe { border-color:color-mix(in srgb, var(--warn) 55%, transparent);
            color:var(--warn); background:color-mix(in srgb, var(--warn) 8%, var(--bg)); }
  .dhead { position:sticky; top:0; background:var(--bg); padding:16px 0 12px;
           border-bottom:1px solid var(--line); z-index:2; }
  .dhead h2 { font-size:1.05em; font-weight:650; letter-spacing:-.01em;
              margin:0 0 3px; word-break:break-all; }
  .cfgdesc { color:var(--mut); font-size:.9em; margin:0 0 8px; }
  .chip { display:inline-block; background:var(--card); border:1px solid var(--line);
          border-radius:99px; padding:2px 11px; margin:2px 5px 2px 0;
          font-size:.85em; font-weight:500; }
  .chip[title] { cursor:help; }
  .chip.good { border-color:color-mix(in srgb, var(--ok) 50%, transparent);
    color:var(--ok); background:color-mix(in srgb, var(--ok) 9%, var(--bg)); }
  .chip.fail { border-color:color-mix(in srgb, var(--bad) 50%, transparent);
    color:var(--bad); background:color-mix(in srgb, var(--bad) 9%, var(--bg)); }
  .grp { display:inline-block; border-radius:8px; padding:3px 10px;
         margin:3px 5px 3px 0; font-size:.88em; border:1px solid; }
  .grp.hit { border-color:color-mix(in srgb, var(--ok) 55%, transparent);
    background:color-mix(in srgb, var(--ok) 10%, var(--bg)); }
  .grp.miss { border-color:color-mix(in srgb, var(--bad) 55%, transparent);
    background:color-mix(in srgb, var(--bad) 10%, var(--bg)); }
  .note { color:var(--mut); font-size:.88em; margin:3px 0 7px; }
  .q { background:var(--card); border-left:3px solid var(--acc); padding:11px 14px;
       border-radius:0 var(--radius) var(--radius) 0; margin:12px 0; }
  .answer { white-space:pre-wrap; background:var(--card); padding:13px 14px;
            border-radius:var(--radius); margin:8px 0; word-break:break-word; }
  .cite { color:var(--acc); font-weight:600; }
  .ev { border:1px solid var(--line); border-radius:var(--radius); margin:10px 0;
        overflow:hidden; box-shadow:var(--shadow-sm); }
  .ev .h { padding:6px 12px; background:var(--card); font-size:.85em; color:var(--mut);
           display:flex; gap:12px; flex-wrap:wrap; align-items:center; }
  .ev .b { padding:10px 12px; white-space:pre-wrap; font-size:.92em;
           overflow-x:auto; word-break:break-word; }
  .ev details.fold > summary { padding:6px 12px; font-size:.86em; color:var(--acc);
    cursor:pointer; user-select:none; }
  .role-user { border-left:3px solid var(--acc); }
  .role-assistant { border-left:3px solid var(--ok); }
  .role-tool { border-left:3px solid var(--warn); }
  .role-system { border-left:3px solid var(--mut); }
  .phase { font-weight:650; color:var(--fg); cursor:help;
           text-decoration:underline dotted; text-underline-offset:3px; }
  .bar { display:inline-block; height:7px; background:var(--acc); border-radius:99px;
         vertical-align:middle; }
  h3 { font-size:.95em; font-weight:600; margin:18px 0 4px; }
  /* ── 다이얼로그 (도움말·시나리오 사전) ─────────────────────── */
  dialog { max-width:780px; width:calc(100vw - 48px); max-height:84vh;
    border:1px solid var(--line); border-radius:14px; background:var(--bg);
    color:var(--fg); padding:24px 28px; font-size:var(--fs); line-height:1.65;
    box-shadow:0 10px 38px -10px rgb(0 0 0 / .35); }
  dialog::backdrop { background:rgba(0,0,0,.55); }
  dialog h2 { font-size:1.15em; font-weight:650; letter-spacing:-.01em; margin:0 0 10px; }
  dialog h3 { font-size:1em; font-weight:600; margin:20px 0 6px; }
  dialog p { margin:6px 0; }
  dialog dl { margin:4px 0; }
  dialog dt { font-weight:650; margin-top:9px; }
  dialog dd { margin:1px 0 0 0; color:var(--mut); }
  dialog .close { float:right; background:var(--bg); color:var(--fg);
    border:1px solid var(--line); border-radius:8px; padding:4px 10px;
    font-size:.88em; cursor:pointer; }
  dialog .close:hover { background:var(--card); }
  .scen { border:1px solid var(--line); border-radius:var(--radius);
          padding:10px 14px; margin:8px 0; background:var(--bg);
          box-shadow:var(--shadow-sm); }
  .scen-h { display:flex; gap:8px; align-items:center; margin-bottom:2px; }
  .scen-h b { font-family:ui-monospace,'SF Mono',monospace; font-size:.92em; }
  .scen-q { font-weight:500; }
  /* ── 반응형: 발표 배율 150% 등 좁은 화면 ─────────────────────
     사이드바는 상단 가로 탭으로, 목록/상세는 위아래로 쌓인다. */
  @media (max-width: 1100px) {
    body { flex-direction:column; }
    #side { width:100%; flex-direction:row; align-items:center;
            border-right:none; border-bottom:1px solid var(--line); }
    .side-h { padding:10px 14px; flex:none; }
    #nav { display:flex; padding:6px 8px; gap:4px; overflow-x:auto; }
    .navlabel { display:none; }
    .navitem { white-space:nowrap; flex:none; padding:5px 10px; }
    .ns { display:none; }
    .side-f { flex-direction:row; border-top:none; padding:8px 12px; }
    .gen { display:none; }
    #exphead { padding:10px 14px 8px; }
    .filters, #dash > summary { padding-left:14px; padding-right:14px; }
    #dashBody { padding:0 14px 12px; }
    #dash { max-height:40vh; }
    .btrack { width:130px; }
    .blabel { width:80px; }
    main { flex-direction:column; }
    #list { width:100% !important; min-width:0; height:34vh; }
    #split { width:auto; height:7px; cursor:row-resize; }
    #detail { min-width:0; padding:0 14px 22px; }
  }
</style></head><body>

<aside id="side">
  <div class="side-h"><h1>하네스 실험 뷰어</h1>
    <div class="sub mut">실험을 선택하세요</div></div>
  <nav id="nav"></nav>
  <div class="side-f">
    <button id="btnScen">🧪 시나리오 사전</button>
    <button id="btnHelp">❓ 도움말 · 용어 사전</button>
    <div class="fsrow">
      <button id="fsMinus" title="글자 작게">가−</button>
      <button id="fsPlus" title="글자 크게">가＋</button></div>
    <div class="gen mut">생성 __GENERATED__</div>
  </div>
</aside>

<div id="content">
  <div id="exphead"><h2 id="expTitle"></h2><p id="expDesc"></p></div>
  <div id="dash"><div id="dashBody"></div></div>
  <div class="filters">
    <select id="fCfg"></select><select id="fTask"></select>
    <select id="fOk"><option value="">성공/실패 전체</option>
      <option value="1">성공만</option><option value="0">실패만</option></select>
    <input type="text" id="fText" placeholder="검색: 답변 내용·실행 ID">
    <button id="fReset">필터 초기화</button>
    <span id="cnt" class="mut"></span>
  </div>
  <main>
    <div id="list"><table class="runs"><thead><tr>
      <th data-k="ts" title="실행된 시각">시각</th>
      <th data-k="cfg" title="하네스 장치를 어디까지 켰는지 (도움말의 사다리 참고)">설정</th>
      <th data-k="task" title="모델에게 낸 질문 번호">질문</th>
      <th data-k="ok" title="기대한 위키 페이지를 인용했는지 자동 채점">성공</th>
      <th data-k="steps" title="모델을 호출한 횟수 (검색할수록 늘어남)">왕복</th>
      <th data-k="calls" title="모델이 검색·읽기 도구를 쓰겠다고 요청한 횟수">도구</th>
      <th data-k="bounce" title="근거 없는 답을 하네스가 돌려보낸 횟수">반려</th>
      <th data-k="tok" title="모델이 읽고 쓴 글 분량 (한글 1~2자 ≈ 1토큰)">토큰</th>
      </tr></thead>
      <tbody id="rows"></tbody></table></div>
    <div id="split" title="드래그로 크기 조절 · 더블클릭 = 원위치"></div>
    <div id="detail"><p class="mut" style="padding-top:14px">왼쪽 목록에서 런을
      선택하면 상세와 대화 원문이 여기 표시됩니다.</p></div>
  </main>
</div>

<dialog id="scen">
  <button class="close" onclick="this.closest('dialog').close()">닫기 ✕</button>
  <h2>테스트 시나리오 사전</h2>
  <p class="note">모델에게 낸 질문 전체 목록입니다. 각 항목에 질문 원문·성공 기준·
    기대 근거 페이지가 적혀 있습니다. <span class="tag qa">지식 질문</span>은 기대한
    위키 페이지를 [[페이지명]]으로 인용하면 성공,
    <span class="tag probe">압박 테스트</span>는 없는 도구를 지어내지 않으면
    성공입니다.</p>
  <div id="scenBody"></div>
</dialog>

<dialog id="help">
  <button class="close" onclick="this.closest('dialog').close()">닫기 ✕</button>
  <h2>이 화면은 무엇인가요?</h2>
  <p>내 노트북에서 도는 작은 AI 모델(Qwen3-8B)에게 <b>개인 위키에 관한 질문</b>을
    시키고, 모델을 돕는 보조 장치인 <b>하네스(harness)</b>를 한 단계씩 붙일 때마다
    정답률이 어떻게 변하는지 기록한 실험 결과입니다. <b>왼쪽에서 실험을 선택</b>하면
    그 실험의 요약과 런(실행 1회) 목록이 나오고, 런을 클릭하면 그때 모델과 실제로
    오간 대화 전체를 볼 수 있습니다.</p>

  <h3>설정(하네스 사다리) — 아래로 갈수록 장치가 많아집니다</h3>
  <div class="ladder-step"><b>raw</b><span>생 모델. 위키를 볼 방법이 아예 없이
    혼자 기억만으로 답함 (책 없이 시험 보기)</span></div>
  <div class="ladder-step"><b>baseline</b><span>위키 검색·읽기 도구만 줌.
    보호 장치는 전혀 없음 (책은 주되 감독 없음)</span></div>
  <div class="ladder-step"><b>+validator</b><span>baseline + 모델의 도구 요청
    형식이 올바른지 검사</span></div>
  <div class="ladder-step"><b>+retry</b><span>+validator + 형식이 깨지면
    고쳐서 재시도</span></div>
  <div class="ladder-step"><b>full / control</b><span>검사 + 재시도 + 가드레일
    (같은 검색 반복 차단)까지 전부 켬 — 기본 하네스 완성형</span></div>
  <div class="ladder-step"><b>+planner</b><span>control + "답하기 전에 먼저
    계획을 세워라"는 지시를 추가</span></div>
  <div class="ladder-step"><b>+evidence</b><span>control + 답에 근거 인용이
    없으면 "다시 써오라"고 돌려보냄</span></div>

  <h3>실험 이름 읽는 법</h3>
  <p>실험 이름 앞의 <b>m3 / m4</b>는 프로젝트가 진행된 단계(마일스톤) 번호입니다
    — 3단계에서 한 실험이 m3. 모델 이름이 아닙니다. 이름 끝의 <b>-r2</b>는
    round 2, 즉 2회차 측정이라는 뜻입니다. 각 실험은 두 번 돌렸는데 1회차는
    점수 같은 요약 숫자만 남겼고, 2회차는 모델과 오간 <b>대화 원문까지 통째로
    기록</b>했습니다. 이 화면에는 대화 원문이 있는 2회차만 표시합니다.</p>

  <h3>용어 사전</h3>
  <dl>
    <dt>런 (run)</dt><dd>질문 1개를 모델에게 시킨 실행 1회. 같은 질문을 3번
      반복하기도 함 (작은 모델은 결과가 흔들려서)</dd>
    <dt>토큰 (token)</dt><dd>모델이 글을 세는 단위. 한글 1~2글자 ≈ 1토큰.
      많이 쓸수록 느려짐</dd>
    <dt>왕복 (스텝)</dt><dd>모델을 호출한 횟수. 모델이 "검색해줘"라고 하면
      결과를 주고 다시 호출하므로 왕복이 늘어남</dd>
    <dt>도구 요청 (툴콜)</dt><dd>모델이 위키 검색(search_wiki)·페이지 읽기
      (read_page) 같은 도구를 쓰겠다고 요청한 것</dd>
    <dt>수리 / 차단 / 반려</dt><dd>하네스의 개입 3종 — 깨진 도구 요청을 고침(수리),
      같은 검색 반복 등 낭비를 막음(차단), 근거 없는 답을 돌려보냄(반려)</dd>
    <dt>성공</dt><dd>모델의 답이 기대한 위키 페이지를 [[페이지명]] 형태로
      인용했는지 자동 채점한 결과</dd>
    <dt>환각 (hallucination)</dt><dd>모델이 모르는 것을 아는 것처럼 그럴듯하게
      지어내는 현상. raw 설정의 답이 전형적인 예</dd>
  </dl>

  <h3>조작법</h3>
  <p>· 왼쪽 사이드바 → 실험 선택 · <b>🧪 시나리오 사전</b> → 질문 목록과 성공 기준<br>
     · 요약의 막대나 표 칸을 클릭 → 목록이 그 조건으로 좁혀집니다<br>
     · 목록에서 행 클릭 또는 <b>↑↓</b> 키 → 오른쪽에 상세(대화 원문)<br>
     · 목록과 상세 사이 구분선을 드래그 → 크기 조절 (더블클릭 = 원위치).
       화면이 좁으면(발표 배율 150% 등) 사이드바가 상단 탭으로 바뀝니다<br>
     · <b>가− / 가＋</b> → 글자 크기 조절</p>
</dialog>

<script type="application/json" id="data">__DATA__</script>
<script>
'use strict';
const D = JSON.parse(document.getElementById('data').textContent);
// 발표용 정리: 같은 실험의 1회차(요약 숫자만 기록)는 숨기고, 대화 원문까지
// 기록된 2회차(-r2)만 보여준다. 데이터 자체는 파일 안에 그대로 남아 있다.
const HIDDEN_EXPS = new Set(['m3-validity', 'm4-planner']);
const runs = D.runs.filter(r => !(r.meta && HIDDEN_EXPS.has(r.meta.experiment)));
const suite = D.suite;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const short = t => t ? t.replace('T',' ').slice(5,19) : '';
const tokOf = m => (m.prompt_tokens||0) + (m.completion_tokens||0);

// ══ 설명 사전 ═════════════════════════════════════════════════
const CONFIG_DESC = {
  'raw': '생 모델 — 위키를 볼 수단이 전혀 없이 기억만으로 답함 (책 없이 시험 보기). ' +
    '"개인 지식은 모델 파라미터에 없다"를 보여주는 대조군',
  'baseline': '검색(search_wiki)·읽기(read_page)·목록(list_pages) 도구만 줌 — ' +
    '검증·재시도·가드레일 등 보호 장치는 전부 끔 (책은 주되 감독 없음)',
  '+validator': 'baseline + 도구 요청의 이름·인자 형식을 실행 전에 검사 — ' +
    '깨진 요청이 그대로 실행되는 사고를 막는다',
  '+retry': '+validator + 형식이 깨진 요청은 오류를 알려주고 최대 2회 고쳐서 재시도',
  'full': '검사 + 재시도 + 가드레일(같은 검색 반복·실패한 호출 재시도 차단)까지 ' +
    '전부 켬 — 기본 하네스 완성형',
  'control': 'full과 동일 구성 — 개입 실험에서 비교 기준(대조군) 역할일 때의 이름',
  '+planner': 'control + "비교·열거 질문은 대상마다 근거를 확인한 뒤 답하라"는 ' +
    '계획 지시 한 줄을 시스템 프롬프트에 추가',
  '+evidence': 'control + 답에 인용한 페이지를 read_page로 실제로 읽지 않았으면 ' +
    '한 번 돌려보냄(반려)',
  '+conditional': 'control + 문제가 감지될 때만 조건부로 반려',
};
const KIND_KO = {
  qa: ['지식 질문', '기대한 위키 페이지를 인용하면 성공'],
  probe: ['압박 테스트', '없는 도구를 지어내지 않으면 성공'],
};
// 실험 코드네임 → [한글 제목, 처음 보는 사람용 설명]
const EXP_KO = {
  'm4-floor': ['바닥 측정 — 생 모델 vs 도구만 준 모델',
    '하네스를 아예 안 붙인 밑바닥 두 조건을 같은 어려운 질문으로 측정 — ' +
    '위키를 볼 수단이 전혀 없는 생 모델(raw)과, 검색 도구만 준 모델(baseline). ' +
    '결과: 도구를 주는 것만으로 성공률 0% → 79%'],
  'm3-validity-r2': ['검증 장치 실험',
    '모델이 검색·읽기 도구를 요청할 때 형식이 깨지는지 보고, 이를 막는 장치를 ' +
    '하나씩 켜며 비교 — 아무 장치 없음(baseline) → 형식 검사(+validator) → ' +
    '깨지면 재시도(+retry) → 반복 낭비 차단까지(full). 결과: 장치가 없어도 ' +
    '형식 오류 0건 — 검증 장치는 점수를 올리는 게 아니라 만일에 대비한 보험'],
  'm4-planner-r2': ['개입 실험',
    '여러 페이지를 이어 봐야 답이 나오는 어려운 질문에서, 기본 하네스(control) ' +
    '위에 개입을 얹어 비교 — "답하기 전에 계획부터 세워라"(+planner), ' +
    '"근거 인용이 없으면 다시 써와라"(+evidence). 결과: 개입이 오히려 점수를 ' +
    '떨어뜨릴 수 있음 (83% → 79% → 63%)'],
  'm3-validity': ['검증 장치 실험 · 1회차', '요약 숫자만 기록된 첫 측정'],
  'm4-planner': ['개입 실험 · 1회차', '요약 숫자만 기록된 첫 측정'],
};
const expTitle = e => e === '__other' ? '기타 트레이스'
  : (EXP_KO[e] ? EXP_KO[e][0] : e);
const expDesc = e => e === '__other'
  ? '어블레이션 실험에 속하지 않는 실행 기록 — 개발 중 수동 실행, 검색 평가 등'
  : (EXP_KO[e] ? EXP_KO[e][1] : '');
const ROLE_KO = {
  system: ['시스템', '하네스가 모델에게 준 지시문'],
  user: ['질문·요청', '사용자 질문, 또는 하네스가 끼워 넣은 요청(수리·반려 등)'],
  assistant: ['모델 출력', '모델(qwen3:8b)이 말한 차례 — 도구 요청 또는 답변'],
  tool: ['도구 결과', '하네스가 도구를 실제 실행해 모델에게 돌려준 결과'],
};
const PHASE_KO = {
  llm: ['모델 호출', '모델을 1번 호출한 기록 (토큰 수·속도)'],
  retrieve: ['검색', '위키 검색 실행 기록'],
  tool_call: ['도구 요청 점검', '모델의 도구 요청이 올바른지 검사한 기록'],
  assemble: ['컨텍스트 조립', '모델에게 줄 자료를 짜맞춘 기록'],
};
// 하네스 사다리 순서 — 요약에서 설정을 이 순서로 정렬한다
const LADDER = ['raw','baseline','+validator','+retry','full','control','+planner',
                '+evidence','+conditional'];
const rank = c => { const i = LADDER.indexOf(c); return i < 0 ? 99 : i; };

// ══ 실험 단위 그룹핑 + 사이드바 ═══════════════════════════════
const expOf = r => r.meta ? r.meta.experiment : '__other';
const byExp = {};
runs.forEach(r => (byExp[expOf(r)] = byExp[expOf(r)] || []).push(r));
const lastTs = {};
runs.forEach(r => { const e = expOf(r);
  if (!lastTs[e] || r.ts > lastTs[e]) lastTs[e] = r.ts; });
// 사이드바 순서: 이야기 순(바닥→검증→개입) 우선, 모르는 실험은 최근 순, 기타는 끝
const NAV_ORDER = ['m4-floor', 'm3-validity-r2', 'm4-planner-r2'];
const expOrder = Object.keys(byExp).sort((a, b) => {
  if (a === '__other') return 1; if (b === '__other') return -1;
  const ia = NAV_ORDER.indexOf(a), ib = NAV_ORDER.indexOf(b);
  if (ia < 0 && ib < 0) return (lastTs[b]||'').localeCompare(lastTs[a]||'');
  if (ia < 0) return 1; if (ib < 0) return -1;
  return ia - ib;
});

let curExp = localStorage.getItem('obs-exp');
if (!byExp[curExp]) curExp = expOrder[0];

function renderNav() {
  $('nav').innerHTML = '<div class="navlabel">실험</div>' + expOrder.map(e => {
    const rs = byExp[e], withMeta = rs.filter(r => r.meta);
    const okRate = withMeta.length ? Math.round(
      100 * withMeta.reduce((s, r) => s + r.meta.success, 0) / withMeta.length) : null;
    const sub = e === '__other' ? `${rs.length}런`
      : `${e} · ${rs.length}런${okRate !== null ? ` · 성공 ${okRate}%` : ''}`;
    return `<div class="navitem${e === curExp ? ' active' : ''}" data-exp="${esc(e)}">` +
      `<div class="nt">${esc(expTitle(e))}</div>` +
      `<div class="ns mut">${esc(sub)}</div></div>`;
  }).join('');
  document.querySelectorAll('.navitem').forEach(el =>
    el.onclick = () => selectExp(el.dataset.exp));
}

function selectExp(e) {
  curExp = e;
  localStorage.setItem('obs-exp', e);
  selId = null;
  resetFilters(false);
  renderAll();
}

// ══ 실험 헤더 + 요약 ══════════════════════════════════════════
function renderHead() {
  $('expTitle').innerHTML = esc(expTitle(curExp)) + (curExp === '__other' ? ''
    : ` <span class="tag mono">${esc(curExp)}</span>`);
  $('expDesc').textContent = expDesc(curExp);
}

function renderDash() {
  const list = byExp[curExp].filter(r => r.meta);
  $('dash').style.display = list.length ? '' : 'none';
  if (!list.length) return;
  const cfgs = {};
  list.forEach(r => { const m = r.meta;
    const c = cfgs[m.config] = cfgs[m.config] || { n:0, ok:0, tok:0, tasks:{} };
    c.n++; c.ok += m.success; c.tok += tokOf(m);
    const t = c.tasks[m.task_id] = c.tasks[m.task_id] || { n:0, ok:0 };
    t.n++; t.ok += m.success; });
  const cfgsO = Object.keys(cfgs).sort((a,b) => rank(a)-rank(b) || a.localeCompare(b));

  const bars = cfgsO.map(cfg => { const s = cfgs[cfg];
    const p = Math.round(100*s.ok/s.n);
    const cls = p >= 75 ? 'hi' : p >= 40 ? 'mid' : 'lo';
    return `<div class="barrow" data-cfg="${esc(cfg)}">` +
      `<span class="blabel" title="${esc(CONFIG_DESC[cfg] || cfg)}">${esc(cfg)}</span>` +
      `<span class="btrack"><span class="bfill ${cls}" style="width:${p}%"></span></span>` +
      `<span class="bval">${p}% (${s.ok}/${s.n}) · 평균 ${
        Math.round(s.tok/s.n).toLocaleString()}토큰</span></div>`; }).join('');

  const taskIds = [...new Set(cfgsO.flatMap(c => Object.keys(cfgs[c].tasks)))].sort();
  let matrix = '';
  if (taskIds.length > 1) {
    matrix = `<table class="matrix"><tr><th></th>` + taskIds.map(t => {
      const s = suite[t] || {};
      const tip = (s.question || '') + (s.rubric ? ` — 성공 기준: ${s.rubric}` : '');
      return `<th title="${esc(tip)}">${esc(t.replace('q-',''))}</th>`;
      }).join('') + `</tr>` +
      cfgsO.map(cfg => `<tr><th>${esc(cfg)}</th>` + taskIds.map(t => {
        const s = cfgs[cfg].tasks[t];
        if (!s) return '<td></td>';
        const p = 100*s.ok/s.n;
        const cls = p===100?'c100':p>=50?'c66':p>0?'c33':'c0';
        return `<td class="mcell ${cls}" data-cfg="${esc(cfg)}" data-task="${esc(t)}"` +
          ` title="${esc(t)} · ${esc(cfg)} · ${s.n}번 중 ${s.ok}번 성공">${s.ok}/${s.n}</td>`;
      }).join('') + `</tr>`).join('') + `</table>`;
  }

  const ladder = cfgsO.map(c => `<div class="ladder-step"><b>${esc(c)}</b><span>${
    esc(CONFIG_DESC[c] || '')}</span></div>`).join('');

  $('dashBody').innerHTML =
    `<div class="dashcol"><h4>설정별 성공률</h4>${bars}` +
    (matrix ? `<h4>설정 × 질문 성공 횟수</h4>${matrix}` : '') +
    `<p class="dashhint">막대나 표 칸을 클릭하면 아래 목록이 그 조건으로 ` +
    `좁혀집니다.</p></div>` +
    `<div class="dashcol ladders"><h4>🪜 설정 설명 — 하네스 사다리</h4>${
      ladder}</div>`;
  document.querySelectorAll('.barrow').forEach(el =>
    el.onclick = () => setFilter(el.dataset.cfg, ''));
  document.querySelectorAll('.mcell').forEach(el =>
    el.onclick = () => setFilter(el.dataset.cfg, el.dataset.task));
}

// ══ 필터 ══════════════════════════════════════════════════════
function fillSelect(el, counts, label, useLadder) {
  const keys = Object.keys(counts).sort((a,b) =>
    useLadder ? (rank(a)-rank(b) || a.localeCompare(b)) : a.localeCompare(b));
  el.innerHTML = `<option value="">${label} 전체</option>` +
    keys.map(v => `<option value="${esc(v)}">${esc(v)} (${counts[v]})</option>`).join('');
}
function renderFilters() {
  const cnt = f => byExp[curExp].reduce((a, r) => { if (r.meta) {
    const k = r.meta[f]; a[k] = (a[k]||0)+1; } return a; }, {});
  fillSelect($('fCfg'), cnt('config'), '설정', true);
  fillSelect($('fTask'), cnt('task_id'), '질문', false);
  const hasMeta = byExp[curExp].some(r => r.meta);
  ['fCfg','fTask','fOk'].forEach(id => $(id).style.display = hasMeta ? '' : 'none');
}
function setFilter(cfg, task) {
  $('fCfg').value = cfg || ''; $('fTask').value = task || '';
  renderList();
}
function resetFilters(rerender = true) {
  ['fCfg','fTask','fOk'].forEach(id => $(id).value = '');
  $('fText').value = '';
  if (rerender) renderList();
}

function visible() {
  const c=$('fCfg').value, t=$('fTask').value,
        ok=$('fOk').value, tx=$('fText').value.toLowerCase();
  return byExp[curExp].filter(r => {
    const m = r.meta;
    if (c && (!m || m.config !== c)) return false;
    if (t && (!m || m.task_id !== t)) return false;
    if (ok !== '' && (!m || String(m.success) !== ok)) return false;
    if (tx && !((r.run_id + ' ' + r.mode + ' ' + (m && m.answer || ''))
        .toLowerCase().includes(tx))) return false;
    return true;
  });
}

// ══ 런 목록 (정렬 + 렌더) ═════════════════════════════════════
let sortKey = 'ts', sortDir = -1;
function sortVal(r, k) {
  const m = r.meta;
  switch (k) {
    case 'ts': return r.ts;
    case 'cfg': return m ? m.config : r.mode;
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

let selId = null, curVis = [];
function renderList() {
  let list = visible();
  list = list.slice().sort((a,b) => {
    const x = sortVal(a, sortKey), y = sortVal(b, sortKey);
    return (x < y ? -1 : x > y ? 1 : 0) * sortDir; });
  curVis = list;
  $('cnt').textContent = `표시 ${list.length} / ${byExp[curExp].length}런`;
  $('rows').innerHTML = list.map(r => {
    const m = r.meta;
    const okCell = m ? (m.success ? '<span class="ok">O</span>'
                                  : '<span class="bad">X</span>') : '·';
    return `<tr data-id="${r.run_id}" class="${r.run_id===selId?'sel':''}">` +
      `<td>${short(r.ts)}</td>` +
      `<td${m && CONFIG_DESC[m.config] ? ` title="${esc(CONFIG_DESC[m.config])}"` : ''}>${
        m ? esc(m.config) : `<span class="tag">${esc(r.mode)}</span>`}</td>` +
      `<td${m && suite[m.task_id] ? ` title="${esc(suite[m.task_id].question +
        (suite[m.task_id].rubric ? ' — 성공 기준: ' + suite[m.task_id].rubric : ''))}"`
        : ''}>${m ? esc(m.task_id) : '·'}</td><td>${okCell}</td>` +
      `<td>${m ? m.steps : r.events.length}</td><td>${m ? m.tool_calls : '·'}</td>` +
      `<td>${m && m.evidence_bounces ? m.evidence_bounces : ''}</td>` +
      `<td>${m ? tokOf(m).toLocaleString() : '·'}</td></tr>`;
  }).join('');
  [...$('rows').children].forEach(tr =>
    tr.onclick = () => { selId = tr.dataset.id; renderList(); renderDetail(); });
}

// ══ 상세 ══════════════════════════════════════════════════════
function hlAnswer(text) {
  return esc(text).replace(/\\[\\[([^\\]]+)\\]\\]/g, '<span class="cite">[[$1]]</span>');
}
function gradePanel(t, answer) {
  if (!t) return '';
  if (t.kind === 'probe')
    return '<h3>채점</h3><p class="note">이 질문은 정답 인용이 아니라 "없는 도구를 ' +
      '지어내지 않는지"를 검사합니다. 도구 요청 이름이 전부 실제 존재하면 성공.' +
      (t.rubric ? `<br>기대 행동: ${esc(t.rubric)}` : '') + '</p>';
  const ans = (answer || '').toLowerCase();
  let groups = t.page_groups && t.page_groups.length ? t.page_groups
    : t.require_all ? (t.pages||[]).map(p => [p])
    : (t.pages && t.pages.length ? [t.pages] : []);
  if (!groups.length) return '';
  return '<h3>채점</h3><p class="note">아래 묶음마다 위키 페이지를 최소 1개 ' +
    '인용해야 성공입니다. 초록 ✓ = 인용한 페이지, 빨강 ✗ = 하나도 인용 못한 ' +
    '묶음(후보 나열).</p>' + groups.map(g => {
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
  const mem = e.mem ? ` · 여유 메모리 ${e.mem.avail_mb}MB` : '';
  const bar = e.latency_ms && maxLat ? `<span class="bar" style="width:${
    Math.max(2, Math.round(120*e.latency_ms.total/maxLat))}px"></span>` : '';
  if (e.phase === 'message') {
    const tcs = (e.tool_calls || []).map(tc =>
      `도구 요청 → ${tc.function.name}(${tc.function.arguments})`).join('\\n');
    const body = [e.content, tcs].filter(Boolean).join('\\n');
    const rk = ROLE_KO[e.role] || [e.role, ''];
    return `<div class="ev role-${e.role}"><div class="h">` +
      `<span class="phase" title="${esc(rk[1])}">${esc(rk[0])}` +
      `${e.final ? ' · 최종 답변' : ''}</span>` +
      `<span>${e.step}번째 왕복${mem}</span></div>` +
      fold(e.final ? hlAnswer(body) : esc(body), body.length, e.role !== 'tool') +
      `</div>`;
  }
  let info = '';
  if (e.phase === 'llm') info = `읽은 토큰 ${e.tokens?.prompt} · 쓴 토큰 ${
    e.tokens?.completion} · 속도 초당 ${e.tok_per_s}토큰`;
  else if (e.phase === 'retrieve') info = `${e.op}${e.hits ? ' → ' +
    e.hits.map(h => h.split('#')[0]).join(', ') : ''}`;
  else if (e.phase === 'tool_call') info = e.tool
    ? `${e.tool.name} · 이름 ${e.tool.valid_name ? '유효' : '무효'} · 인자 ${
        e.tool.valid_args ? '유효' : '무효'}` +
      (e.guardrail?.repeat_blocked ? ' · 반복이라 차단됨' : '')
    : Object.entries(e.guardrail || {}).filter(([,v]) => v).map(([k]) => k).join(',') +
      (e.unread ? ` · 안 읽은 페이지: ${e.unread.join(', ')}` : '');
  else if (e.phase === 'assemble') info = `${e.op} · ${JSON.stringify(e.context)}`;
  const pk = PHASE_KO[e.phase] || [e.phase, ''];
  return `<div class="ev"><div class="h"><span class="phase" title="${
    esc(pk[1])}">${esc(pk[0])}</span>` +
    `<span>${e.step}번째 왕복${lat}${mem}</span>${bar}</div>` +
    (info ? `<div class="b mut">${esc(info)}</div>` : '') + `</div>`;
}

function renderDetail() {
  const r = byExp[curExp].find(x => x.run_id === selId);
  if (!r) { $('detail').innerHTML = '<p class="mut" style="padding-top:14px">' +
    '왼쪽 목록에서 런을 선택하면 상세와 대화 원문이 여기 표시됩니다.</p>'; return; }
  const m = r.meta, t = m && suite[m.task_id];
  let dur = '';
  if (r.events.length > 1) {
    const s = (new Date(r.events[r.events.length-1].ts) - new Date(r.events[0].ts))/1000;
    if (s > 0) dur = `<span class="chip" title="이 런에 걸린 시간">소요 ${s >= 60
      ? Math.floor(s/60)+'분 '+Math.round(s%60)+'초' : s.toFixed(1)+'초'}</span>`;
  }
  let h = `<div class="dhead"><h2>${m
    ? `<span class="cite">${esc(m.config)}</span> · ${esc(m.task_id)}`
    : esc(r.mode)} <span class="mut" style="font-weight:400;font-size:.8em">${
      esc(r.run_id)}</span></h2>`;
  if (m && CONFIG_DESC[m.config])
    h += `<p class="cfgdesc">${esc(m.config)} 설정 = ${esc(CONFIG_DESC[m.config])}</p>`;
  if (m) {
    h += `<div>` +
      `<span class="chip ${m.success ? 'good' : 'fail'}" title="기대한 위키 페이지를 ` +
      `인용했는지 자동 채점한 결과">${m.success ? '성공' : '실패'}</span>` + dur +
      `<span class="chip" title="모델을 호출한 횟수">왕복 ${m.steps}</span>` +
      `<span class="chip" title="모델이 도구를 쓰겠다고 요청한 횟수와, 그중 형식이 ` +
      `올바른 개수">도구 요청 ${m.tool_calls}건 (이름 ${m.valid_names}·인자 ${
        m.valid_args} 유효)</span>` +
      `<span class="chip" title="깨진 도구 요청을 하네스가 고친 횟수 / 재시도 횟수">` +
      `수리 ${m.repaired}/${m.retries}</span>` +
      `<span class="chip" title="차단 = 반복 검색 등을 막음 · 반려 = 근거 없는 답을 ` +
      `돌려보냄 · 비상 마무리 = 정상 흐름이 막혀 마지막 수단으로 답을 낸 경우">` +
      `차단 ${m.guardrail_blocks} · 반려 ${m.evidence_bounces}` +
      `${m.fallback ? ' · 비상 마무리' : ''}</span>` +
      `<span class="chip" title="모델이 읽고 쓴 글 분량 (한글 1~2자 ≈ 1토큰)">토큰 ${
        tokOf(m).toLocaleString()}</span></div>`;
  } else { h += dur; }
  h += `</div><div class="dwrap">`;
  if (m) {
    if (t) h += `<div class="q"><b>${esc(m.task_id)}</b>` +
      ` <span class="tag ${esc(t.kind)}">${esc((KIND_KO[t.kind]||[t.kind])[0])}</span>` +
      ` — ${esc(t.question)}${t.rubric ? `<div class="note" style="margin:6px 0 0">` +
      `성공 기준: ${esc(t.rubric)}</div>` : ''}</div>`;
    h += gradePanel(t, m.answer);
    if (m.answer) h += `<h3>최종 답변</h3><div class="answer">${
      hlAnswer(m.answer)}</div>`;
  }
  const maxLat = Math.max(0, ...r.events.map(e => e.latency_ms?.total || 0));
  h += `<h3>대화 타임라인 <span class="mut" style="font-weight:400">— 모델과 실제로 ` +
    `오간 순서 그대로 (${r.events.length}건)</span></h3>`;
  h += r.events.length ? r.events.map(e => evLine(e, maxLat)).join('')
     : '<p class="mut">대화 기록 파일 없음 — 요약 수치만 남아 있습니다.</p>';
  h += `</div>`;
  $('detail').innerHTML = h;
  $('detail').scrollTop = 0;
}

// ══ 시나리오 사전 다이얼로그 ══════════════════════════════════
function renderScenarios() {
  $('scenBody').innerHTML = Object.keys(suite).sort().map(id => {
    const t = suite[id];
    const kind = KIND_KO[t.kind] || [t.kind, ''];
    const groups = (t.page_groups && t.page_groups.length) ? t.page_groups
      : (t.pages && t.pages.length ? [t.pages] : []);
    return `<div class="scen"><div class="scen-h"><b>${esc(id)}</b>` +
      `<span class="tag ${esc(t.kind)}" title="${esc(kind[1])}">${esc(kind[0])}</span>` +
      `</div><div class="scen-q">${esc(t.question)}</div>` +
      (t.rubric ? `<div class="note">성공 기준: ${esc(t.rubric)}</div>` : '') +
      (groups.length ? `<div class="note">기대 근거 페이지: ${groups.map(g =>
        esc(g.join(' 또는 '))).join(' — 그리고 — ')}</div>` : '') +
      `</div>`;
  }).join('');
}

// ══ 공통 UI: 글자 크기·스플리터·키보드·다이얼로그 ═════════════
let fs = +(localStorage.getItem('obs-fs') || 14);
function applyFs() {
  document.documentElement.style.setProperty('--fs', fs + 'px');
  localStorage.setItem('obs-fs', fs);
}
$('fsMinus').onclick = () => { fs = Math.max(11, fs - 1); applyFs(); };
$('fsPlus').onclick = () => { fs = Math.min(20, fs + 1); applyFs(); };
applyFs();
$('btnHelp').onclick = () => $('help').showModal();
$('btnScen').onclick = () => $('scen').showModal();

// 넓은 화면: 좌우 폭 조절 · 좁은 화면(발표 배율 150% 등): 상하 높이 조절
const narrowMq = window.matchMedia('(max-width: 1100px)');
let dragging = false;
$('split').onmousedown = e => { dragging = true; e.preventDefault(); };
window.addEventListener('mousemove', e => { if (!dragging) return;
  if (narrowMq.matches) {
    const top = $('list').getBoundingClientRect().top;
    const h = Math.min(Math.max(e.clientY - top, 110), window.innerHeight - top - 160);
    $('list').style.height = h + 'px';
  } else {
    const left = $('content').getBoundingClientRect().left;
    const w = Math.min(Math.max(e.clientX - left, 260), window.innerWidth - left - 300);
    $('list').style.width = w + 'px';
  } });
window.addEventListener('mouseup', () => dragging = false);
$('split').ondblclick = () => { $('list').style.width = ''; $('list').style.height = ''; };
narrowMq.addEventListener('change', () => {
  $('list').style.width = ''; $('list').style.height = ''; });

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

$('fReset').onclick = () => resetFilters();
['fCfg','fTask','fOk','fText'].forEach(id => $(id).oninput = renderList);

// ══ 진입점 ════════════════════════════════════════════════════
function renderAll() {
  renderNav(); renderHead(); renderDash(); renderFilters(); renderList();
  renderDetail();
}
renderScenarios();
renderAll();
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
