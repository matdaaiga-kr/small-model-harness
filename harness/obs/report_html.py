"""옵저버빌리티 UI — metrics.sqlite + traces/runs/*.jsonl → 자립형 HTML 뷰어.

실험 → 설정 → 태스크 → 런 → 스텝(이벤트/메시지)으로 드릴다운한다.
외부 의존성 없는 단일 파일 산출물 — 브라우저로 열면 끝.
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
  :root { --bg:#fff; --fg:#1a1a1a; --mut:#777; --line:#e3e3e3; --card:#f7f7f8;
          --ok:#0a7f3f; --bad:#c0392b; --acc:#2456d6; --warn:#b26a00; --fs:14px; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#16181d; --fg:#e6e6e6; --mut:#9aa; --line:#31353d; --card:#1f2229;
            --ok:#4cc38a; --bad:#e5735f; --acc:#7aa2ff; --warn:#e0a95c; } }
  * { box-sizing:border-box; }
  body { margin:0; font:var(--fs)/1.6 -apple-system,'Apple SD Gothic Neo',sans-serif;
         background:var(--bg); color:var(--fg);
         display:flex; flex-direction:column; height:100vh; }
  header { padding:12px 20px; border-bottom:1px solid var(--line);
           display:flex; gap:14px; align-items:center; flex-wrap:wrap; flex:none; }
  header h1 { font-size:1.2em; margin:0; }
  header .sub { color:var(--mut); font-size:.86em; margin-right:auto; }
  header button { background:var(--card); color:var(--fg); border:1px solid var(--line);
    border-radius:6px; padding:4px 11px; font-size:.9em; cursor:pointer; }
  header button:hover { border-color:var(--acc); color:var(--acc); }
  .filters { padding:9px 20px; display:flex; gap:8px; flex-wrap:wrap;
             border-bottom:1px solid var(--line); align-items:center; flex:none; }
  select,input[type=text] { background:var(--card); color:var(--fg);
    border:1px solid var(--line); border-radius:6px; padding:5px 8px; font-size:.93em; }
  #fReset { background:none; border:none; color:var(--acc); cursor:pointer;
            font-size:.9em; padding:4px; }
  /* ── 도움말 ───────────────────────────────────────────────── */
  dialog { max-width:760px; width:calc(100vw - 48px); max-height:84vh;
    border:1px solid var(--line); border-radius:12px; background:var(--bg);
    color:var(--fg); padding:24px 28px; font-size:var(--fs); line-height:1.65; }
  dialog::backdrop { background:rgba(0,0,0,.45); }
  dialog h2 { font-size:1.15em; margin:0 0 10px; }
  dialog h3 { font-size:1em; margin:20px 0 6px; }
  dialog p { margin:6px 0; }
  dialog dl { margin:4px 0; }
  dialog dt { font-weight:700; margin-top:9px; }
  dialog dd { margin:1px 0 0 0; color:var(--mut); }
  dialog .close { float:right; }
  .ladder-step { display:flex; gap:10px; margin:5px 0; align-items:baseline; }
  .ladder-step b { flex:none; width:96px; text-align:right; color:var(--acc); }
  .ladder-step span { color:var(--mut); }
  /* ── 대시보드 ─────────────────────────────────────────────── */
  #dash { border-bottom:1px solid var(--line); flex:none; max-height:48vh; overflow:auto; }
  #dash > summary { cursor:pointer; padding:9px 20px; font-size:.9em;
                    color:var(--mut); user-select:none; }
  #dashHint { padding:0 20px 6px; color:var(--mut); font-size:.86em; }
  #dashBody { display:flex; gap:14px; flex-wrap:wrap; padding:0 20px 16px;
              align-items:flex-start; }
  .expcard { border:1px solid var(--line); border-radius:10px; background:var(--bg);
             max-width:100%; }
  .expcard > summary { cursor:pointer; padding:9px 14px; user-select:none;
    font-size:.95em; white-space:nowrap; }
  .expcard > summary .mut { font-size:.9em; }
  .cardbody { padding:2px 14px 13px; overflow-x:auto; }
  .barrow { display:flex; align-items:center; gap:8px; margin:4px 0; cursor:pointer;
            font-size:.9em; }
  .barrow:hover .blabel { color:var(--acc); }
  .blabel { width:88px; text-align:right; flex:none; border-bottom:1px dotted var(--mut);
            overflow:hidden; text-overflow:ellipsis; white-space:nowrap; cursor:help; }
  .btrack { width:190px; height:13px; background:var(--card); border-radius:3px;
            flex:none; overflow:hidden; }
  .bfill { display:block; height:100%; background:var(--acc); }
  .bfill.hi { background:var(--ok); } .bfill.lo { background:var(--bad); }
  .bfill.mid { background:var(--warn); }
  .bval { color:var(--mut); font-size:.88em; white-space:nowrap; }
  table.matrix { border-collapse:collapse; font-size:.84em; margin-top:10px; }
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
  #list { width:44%; min-width:280px; overflow:auto; }
  #split { flex:none; width:6px; cursor:col-resize; background:var(--line);
           opacity:.55; }
  #split:hover { background:var(--acc); opacity:1; }
  #detail { flex:1; min-width:280px; overflow:auto; padding:0 22px 28px; }
  .dwrap { max-width:940px; }
  table.runs { border-collapse:collapse; width:100%; font-size:.9em; }
  table.runs th, table.runs td { padding:6px 8px; text-align:left;
    border-bottom:1px solid var(--line); white-space:nowrap; }
  table.runs th { position:sticky; top:0; background:var(--bg); color:var(--mut);
    font-weight:600; font-size:.86em; cursor:pointer; user-select:none; z-index:1; }
  table.runs th[title] { text-decoration:underline dotted; text-underline-offset:3px; }
  table.runs th:hover { color:var(--acc); }
  table.runs tbody tr { cursor:pointer; }
  table.runs tbody tr:hover { background:var(--card); }
  table.runs tbody tr.sel { background:color-mix(in srgb, var(--acc) 14%, var(--bg)); }
  .ok { color:var(--ok); font-weight:700; } .bad { color:var(--bad); font-weight:700; }
  .tag { display:inline-block; font-size:.8em; border:1px solid var(--line);
         border-radius:4px; padding:0 5px; background:var(--card); color:var(--mut); }
  .dhead { position:sticky; top:0; background:var(--bg); padding:16px 0 10px;
           border-bottom:1px solid var(--line); z-index:2; }
  .dhead h2 { font-size:1.05em; margin:0 0 3px; word-break:break-all; }
  .cfgdesc { color:var(--mut); font-size:.9em; margin:0 0 8px; }
  .chip { display:inline-block; background:var(--card); border:1px solid var(--line);
          border-radius:20px; padding:2px 10px; margin:2px 5px 2px 0; font-size:.86em; }
  .chip[title] { cursor:help; }
  .chip.good { border-color:var(--ok); color:var(--ok); }
  .chip.fail { border-color:var(--bad); color:var(--bad); }
  .grp { display:inline-block; border-radius:6px; padding:3px 9px; margin:3px 5px 3px 0;
         font-size:.88em; border:1px solid; }
  .grp.hit { border-color:var(--ok); background:color-mix(in srgb, var(--ok) 10%, var(--bg)); }
  .grp.miss { border-color:var(--bad); background:color-mix(in srgb, var(--bad) 10%, var(--bg)); }
  .note { color:var(--mut); font-size:.88em; margin:3px 0 7px; }
  .q { background:var(--card); border-left:3px solid var(--acc); padding:10px 12px;
       border-radius:6px; margin:12px 0; }
  .answer { white-space:pre-wrap; background:var(--card); padding:12px;
            border-radius:8px; margin:8px 0; word-break:break-word; }
  .cite { color:var(--acc); font-weight:600; }
  .ev { border:1px solid var(--line); border-radius:8px; margin:10px 0; overflow:hidden; }
  .ev .h { padding:6px 10px; background:var(--card); font-size:.86em; color:var(--mut);
           display:flex; gap:12px; flex-wrap:wrap; align-items:center; }
  .ev .b { padding:9px 11px; white-space:pre-wrap; font-size:.92em;
           overflow-x:auto; word-break:break-word; }
  .ev details.fold > summary { padding:6px 10px; font-size:.86em; color:var(--acc);
    cursor:pointer; user-select:none; }
  .role-user { border-left:3px solid var(--acc); }
  .role-assistant { border-left:3px solid var(--ok); }
  .role-tool { border-left:3px solid var(--warn); }
  .role-system { border-left:3px solid var(--mut); }
  .phase { font-weight:700; color:var(--fg); cursor:help;
           text-decoration:underline dotted; text-underline-offset:3px; }
  .bar { display:inline-block; height:8px; background:var(--acc); border-radius:2px;
         vertical-align:middle; }
  h3 { font-size:.95em; margin:18px 0 4px; }
  .mut { color:var(--mut); }
  /* ── 반응형: 발표 배율 150% 등 좁은 화면 ─────────────────────
     가로폭이 좁아지면 목록/상세를 좌우 대신 위아래로 쌓고,
     스플리터는 상하(높이) 조절로 동작한다. */
  @media (max-width: 1100px) {
    header { padding:10px 14px; gap:10px; }
    .filters, #dash > summary, #dashHint { padding-left:14px; padding-right:14px; }
    #dashBody { padding:0 14px 14px; }
    #dash { max-height:42vh; }
    .btrack { width:130px; }
    .blabel { width:80px; }
    main { flex-direction:column; }
    #list { width:100% !important; min-width:0; height:36vh; }
    #split { width:auto; height:7px; cursor:row-resize; }
    #detail { min-width:0; padding:0 14px 22px; }
  }
</style></head><body>
<header><h1>하네스 실험 뷰어</h1>
  <span class="sub">생성 __GENERATED__ · 표시 <span id="nRuns"></span> /
    전체 <span id="nAll"></span>런</span>
  <button id="fsMinus" title="글자 작게">가−</button>
  <button id="fsPlus" title="글자 크게">가＋</button>
  <button id="btnHelp">❓ 도움말 · 용어 사전</button></header>

<dialog id="help">
  <button class="close" onclick="this.closest('dialog').close()">닫기 ✕</button>
  <h2>이 화면은 무엇인가요?</h2>
  <p>내 노트북에서 도는 작은 AI 모델(Qwen3-8B)에게 <b>개인 위키에 관한 질문</b>을
    시키고, 모델을 돕는 보조 장치인 <b>하네스(harness)</b>를 한 단계씩 붙일 때마다
    정답률이 어떻게 변하는지 기록한 실험 결과입니다. 위쪽 <b>실험 요약</b>에서
    큰 그림을 보고, 아래 <b>목록에서 런(실행 1회)을 클릭</b>하면 그때 모델과 실제로
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
  <p>이 프로젝트는 단계(마일스톤)를 M1, M2, M3…으로 셉니다. 실험 이름 앞의
    <b>m3 / m4</b>는 "3단계 / 4단계에서 한 실험"이라는 뜻일 뿐, 모델 이름이
    아닙니다. 이름 끝의 <b>-r2</b>는 round 2, 즉 <b>같은 실험의 2회차
    재측정</b>입니다 — 1회차 때는 점수 같은 요약 숫자만 저장했는데, 나중에
    모델과 오간 대화 원문까지 통째로 기록하도록 프로그램을 고친 뒤 한 번 더
    돌린 것입니다. 그래서 대화 원문이 보고 싶으면 -r2 쪽 런을 열면 됩니다.</p>
  <dl>
    <dt>m3-validity — 검증 장치 실험</dt><dd>도구 요청 형식을 검사·수리하는
      장치(validator·retry·가드레일)를 하나씩 켜며 오류가 줄어드는지 측정.
      결과: 장치가 없어도 오류가 0이었음 — 검증은 성능 향상이 아니라 보험</dd>
    <dt>m4-planner — 개입 실험</dt><dd>여러 페이지를 이어 봐야 답이 나오는
      어려운 질문에서, "먼저 계획을 세워라"(+planner) 같은 적극 개입이 도움이
      되는지 측정. 결과: 오히려 점수가 떨어질 수 있음</dd>
    <dt>m4-floor — 바닥 측정</dt><dd>하네스를 아예 안 붙인 밑바닥 두 조건
      (생 모델 raw, 도구만 준 baseline)을 같은 어려운 질문으로 측정.
      결과: 검색 도구를 주는 것만으로 0% → 79%</dd>
    <dt>…-r2 — 2회차 재측정</dt><dd>위 설명대로, 대화 원문까지 기록되는
      개선판으로 같은 실험을 다시 돌린 것 (숫자가 1회차와 약간 다를 수 있음)</dd>
  </dl>

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
  <p>· 요약의 막대나 표 칸을 클릭 → 아래 목록이 그 조건으로 좁혀집니다<br>
     · 목록에서 행 클릭 또는 <b>↑↓</b> 키 → 오른쪽에 상세(대화 원문)<br>
     · 목록과 상세 사이 구분선을 드래그 → 크기 조절 (더블클릭 = 원위치).
       화면이 좁으면(발표 배율 150% 등) 목록·상세가 위아래로 쌓입니다<br>
     · 오른쪽 위 <b>가− / 가＋</b> → 글자 크기 조절</p>
</dialog>

<div class="filters">
  <select id="fExp"></select><select id="fCfg"></select><select id="fTask"></select>
  <select id="fOk"><option value="">성공/실패 전체</option>
    <option value="1">성공만</option><option value="0">실패만</option></select>
  <input type="text" id="fText" placeholder="검색: 답변 내용·run id">
  <button id="fReset">필터 초기화</button>
</div>
<details id="dash" open><summary>📊 실험 요약 (접기/펼치기)</summary>
  <div id="dashHint">막대 = 성공률 · 표 = 설정×질문별 성공 횟수 —
    클릭하면 아래 목록이 그 조건으로 좁혀집니다. 실험 카드도 접을 수 있어요.</div>
  <div id="dashBody"></div></details>
<main>
  <div id="list"><table class="runs"><thead><tr>
    <th data-k="ts" title="실행된 시각">시각</th>
    <th data-k="exp" title="어떤 실험 묶음인지 (도움말 참고)">실험</th>
    <th data-k="cfg" title="하네스 장치를 어디까지 켰는지 (도움말의 사다리 참고)">설정</th>
    <th data-k="task" title="모델에게 낸 질문 번호">질문</th>
    <th data-k="ok" title="기대한 위키 페이지를 인용했는지 자동 채점">성공</th>
    <th data-k="steps" title="모델을 호출한 횟수 (검색할수록 늘어남)">왕복</th>
    <th data-k="calls" title="모델이 검색·읽기 도구를 쓰겠다고 요청한 횟수">도구</th>
    <th data-k="bounce" title="근거 없는 답을 하네스가 돌려보낸 횟수">반려</th>
    <th data-k="tok" title="모델이 읽고 쓴 글 분량 (한글 1~2자 ≈ 1토큰)">토큰</th>
    </tr></thead>
    <tbody id="rows"></tbody></table></div>
  <div id="split" title="드래그로 폭 조절 · 더블클릭 = 원위치"></div>
  <div id="detail"><p class="mut" style="padding-top:14px">왼쪽 목록에서 런을
    선택하면 상세와 대화 원문이 여기 표시됩니다.</p></div>
</main>
<script type="application/json" id="data">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const runs = D.runs, suite = D.suite;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const short = t => t ? t.replace('T',' ').slice(5,19) : '';
const tokOf = m => (m.prompt_tokens||0) + (m.completion_tokens||0);

// ── 초보자용 설명 사전 ────────────────────────────────────────
const CONFIG_DESC = {
  'raw': '생 모델 — 위키를 볼 방법이 아예 없음 (책 없이 시험 보기)',
  'baseline': '검색·읽기 도구만 줌 — 보호 장치 없음',
  '+validator': 'baseline + 도구 요청 형식 검사',
  '+retry': '+validator + 형식이 깨지면 고쳐서 재시도',
  'full': '검사·재시도·가드레일 전부 켬 — 기본 하네스 완성형',
  'control': '검사·재시도·가드레일 전부 켬 — 기본 하네스 완성형 (비교 기준)',
  '+planner': 'control + "먼저 계획을 세워라" 지시 추가',
  '+evidence': 'control + 근거 인용 없는 답은 반려',
  '+conditional': 'control + 문제가 있을 때만 조건부로 반려',
};
// 실험 코드네임 → [한글 제목, 처음 보는 사람용 설명]
const EXP_KO = {
  'm3-validity': ['검증 장치 실험',
    '도구 요청 형식을 검사·수리하는 장치를 하나씩 켜며 오류가 줄어드는지 측정'],
  'm3-validity-r2': ['검증 장치 실험 · 2회차 재측정',
    '같은 실험을 한 번 더 돌린 것 — 1회차엔 요약 숫자만 남아서, 대화 원문까지 ' +
    '기록되는 개선판으로 재측정 (r2 = round 2)'],
  'm4-planner': ['개입 실험',
    '"먼저 계획을 세워라"(+planner), "근거 없으면 반려"(+evidence) 같은 적극 ' +
    '개입이 어려운 질문에 도움 되는지 측정'],
  'm4-planner-r2': ['개입 실험 · 2회차 재측정',
    '같은 실험을 한 번 더 돌린 것 — 대화 원문까지 기록되는 개선판으로 재측정 ' +
    '(r2 = round 2)'],
  'm4-floor': ['바닥 측정 — 생 모델 vs 도구만 준 모델',
    '하네스를 아예 안 붙인 밑바닥 두 조건을 측정 — 검색 도구를 주는 것만으로 ' +
    '성공률이 0%에서 79%로 오른다'],
};
// 좁은 칸(목록·필터)용 축약 이름
const EXP_SHORT = {
  'm3-validity': '검증 장치', 'm3-validity-r2': '검증 장치 ②',
  'm4-planner': '개입', 'm4-planner-r2': '개입 ②', 'm4-floor': '바닥 측정',
};
const expTitle = e => EXP_KO[e] ? EXP_KO[e][0] : e;
const expDesc = e => EXP_KO[e] ? EXP_KO[e][1] : '';
const expShort = e => EXP_SHORT[e] || e;
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

// 하네스 사다리 순서 — 대시보드에서 설정을 이 순서로 정렬한다
const LADDER = ['raw','baseline','+validator','+retry','full','control','+planner',
                '+evidence','+conditional'];
const rank = c => { const i = LADDER.indexOf(c); return i < 0 ? 99 : i; };

// ── 글자 크기 조절 ────────────────────────────────────────────
let fs = +(localStorage.getItem('obs-fs') || 14);
function applyFs() {
  document.documentElement.style.setProperty('--fs', fs + 'px');
  localStorage.setItem('obs-fs', fs);
}
$('fsMinus').onclick = () => { fs = Math.max(11, fs - 1); applyFs(); };
$('fsPlus').onclick = () => { fs = Math.min(20, fs + 1); applyFs(); };
applyFs();
$('btnHelp').onclick = () => $('help').showModal();

// ── 리스트/상세 크기 조절 스플리터 ────────────────────────────
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
    const w = Math.min(Math.max(e.clientX, 280), window.innerWidth - 300);
    $('list').style.width = w + 'px';
  } });
window.addEventListener('mouseup', () => dragging = false);
$('split').ondblclick = () => { $('list').style.width = ''; $('list').style.height = ''; };
// 배율/창 크기 변경으로 레이아웃 모드가 바뀌면 수동 조절값 초기화
narrowMq.addEventListener('change', () => {
  $('list').style.width = ''; $('list').style.height = ''; });

function fillSelect(el, counts, label) {
  const keys = Object.keys(counts).sort((a,b) =>
    el.id === 'fCfg' ? (rank(a)-rank(b) || a.localeCompare(b)) : a.localeCompare(b));
  const disp = el.id === 'fExp' ? expShort : (v => v);
  el.innerHTML = `<option value="">${label} 전체</option>` +
    keys.map(v =>
      `<option value="${esc(v)}">${esc(disp(v))} (${counts[v]})</option>`).join('');
}
const cnt = f => runs.reduce((a,r) => { if (r.meta) {
  const k = r.meta[f]; a[k] = (a[k]||0)+1; } return a; }, {});
fillSelect($('fExp'), cnt('experiment'), '실험');
fillSelect($('fCfg'), cnt('config'), '설정');
fillSelect($('fTask'), cnt('task_id'), '질문');
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

// ── 대시보드: 실험 카드(접이식) — 사다리 바 + 설정×질문 매트릭스 ──
function setFilter(exp, cfg, task) {
  $('fExp').value = exp || ''; $('fCfg').value = cfg || '';
  $('fTask').value = task || ''; renderList();
}
// 기본으로 펼쳐 둘 카드: 가장 최근에 실행된 실험 하나
const openExps = new Set();
{ let best = '', bestTs = '';
  runs.forEach(r => { if (r.meta && r.ts > bestTs) {
    bestTs = r.ts; best = r.meta.experiment; } });
  if (best) openExps.add(best); }

function renderDash(list) {
  const byExp = {};
  list.forEach(r => { const m = r.meta; if (!m) return;
    const e = byExp[m.experiment] = byExp[m.experiment] || {};
    const c = e[m.config] = e[m.config] || { n:0, ok:0, tok:0, tasks:{} };
    c.n++; c.ok += m.success; c.tok += tokOf(m);
    const t = c.tasks[m.task_id] = c.tasks[m.task_id] || { n:0, ok:0 };
    t.n++; t.ok += m.success; });
  // 최근 실행 순으로 카드 정렬
  const lastTs = {};
  list.forEach(r => { if (r.meta && (!lastTs[r.meta.experiment] ||
    r.ts > lastTs[r.meta.experiment])) lastTs[r.meta.experiment] = r.ts; });
  const exps = Object.keys(byExp).sort((a,b) =>
    (lastTs[b]||'').localeCompare(lastTs[a]||''));
  $('dashBody').innerHTML = exps.map(exp => {
    const cfgsO = Object.keys(byExp[exp]).sort((a,b) =>
      rank(a)-rank(b) || a.localeCompare(b));
    const nRuns = cfgsO.reduce((s,c) => s + byExp[exp][c].n, 0);
    const bars = cfgsO.map(cfg => { const s = byExp[exp][cfg];
      const p = Math.round(100*s.ok/s.n);
      const cls = p >= 75 ? 'hi' : p >= 40 ? 'mid' : 'lo';
      return `<div class="barrow" data-exp="${esc(exp)}" data-cfg="${esc(cfg)}">` +
        `<span class="blabel" title="${esc(CONFIG_DESC[cfg] || cfg)}">${esc(cfg)}</span>` +
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
            ` data-cfg="${esc(cfg)}" data-task="${esc(t)}" title="${esc(t)} · ${
            esc(cfg)} · ${s.n}번 중 ${s.ok}번 성공">${s.ok}/${s.n}</td>`;
        }).join('') + `</tr>`).join('') + `</table>`;
    }
    const desc = expDesc(exp);
    return `<details class="expcard" data-exp="${esc(exp)}"${
        openExps.has(exp) ? ' open' : ''}>` +
      `<summary><b>${esc(expTitle(exp))}</b> <span class="mut">(${esc(exp)}) · ${
        nRuns}런</span></summary>` +
      `<div class="cardbody">${desc ? `<p class="note">${esc(desc)}</p>` : ''}${
        bars}${matrix}</div></details>`;
  }).join('') || '<p class="mut" style="padding:0 20px 12px">표시할 실험 런이 없습니다.</p>';
  document.querySelectorAll('.expcard').forEach(d =>
    d.addEventListener('toggle', () => {
      if (d.open) openExps.add(d.dataset.exp); else openExps.delete(d.dataset.exp); }));
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
      `<td${m ? ` title="${esc(m.experiment)} — ${esc(expDesc(m.experiment))}"` : ''}>${
        m ? esc(expShort(m.experiment))
          : `<span class="tag">${esc(r.mode)}</span>`}</td>` +
      `<td${m && CONFIG_DESC[m.config] ? ` title="${esc(CONFIG_DESC[m.config])}"` : ''}>${
        m ? esc(m.config) : '·'}</td>` +
      `<td${m && suite[m.task_id] ? ` title="${esc(suite[m.task_id].question)}"` : ''}>${
        m ? esc(m.task_id) : '·'}</td><td>${okCell}</td>` +
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
  if (!t) return '';
  if (t.kind === 'probe')
    return '<h3>채점</h3><p class="note">이 질문은 정답 인용이 아니라 "없는 도구를 ' +
      '지어내지 않는지"를 검사합니다. 도구 요청 이름이 전부 실제 존재하면 성공.</p>';
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
    e.tokens?.completion} · 속도 ${e.tok_per_s} tok/s`;
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
  const r = runs.find(x => x.run_id === selId);
  if (!r) return;
  const m = r.meta, t = m && suite[m.task_id];
  let dur = '';
  if (r.events.length > 1) {
    const s = (new Date(r.events[r.events.length-1].ts) - new Date(r.events[0].ts))/1000;
    if (s > 0) dur = `<span class="chip" title="이 런에 걸린 시간">소요 ${s >= 60
      ? Math.floor(s/60)+'분 '+Math.round(s%60)+'초' : s.toFixed(1)+'초'}</span>`;
  }
  let h = `<div class="dhead"><h2>${m
    ? `${esc(expTitle(m.experiment))} · <span class="cite">${esc(m.config)}</span> · ${
        esc(m.task_id)}`
    : esc(r.run_id)} <span class="mut" style="font-weight:400;font-size:.8em">${
      esc(r.run_id)}</span></h2>`;
  if (m && expDesc(m.experiment))
    h += `<p class="cfgdesc">${esc(m.experiment)} — ${esc(expDesc(m.experiment))}</p>`;
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
      `돌려보냄">차단 ${m.guardrail_blocks} · 반려 ${m.evidence_bounces}` +
      `${m.fallback ? ' · 폴백' : ''}</span>` +
      `<span class="chip" title="모델이 읽고 쓴 글 분량 (한글 1~2자 ≈ 1토큰)">토큰 ${
        tokOf(m).toLocaleString()}</span></div>`;
  } else { h += dur; }
  h += `</div><div class="dwrap">`;
  if (m) {
    if (t) h += `<div class="q"><b>${esc(m.task_id)}</b> — ${esc(t.question)}</div>`;
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
