#!/usr/bin/env python3
"""Render a MAGI council result JSON into one self-contained HTML page.

Usage: python3 render.py <input.json> --out <file.html>
Exit codes: 0 ok (prints the output path), 2 invalid input (one line on stderr).
All strings in the JSON are untrusted: every interpolated value is HTML-escaped.
"""
import argparse
import html
import json
import re
import sys
from pathlib import Path

# name -> (label, role, panel x, panel y) in the 400x275 SVG viewBox
MEMBERS = {
    "MELCHIOR": ("MELCHIOR-1", "Architect", 204, 150),
    "BALTHASAR": ("BALTHASAR-2", "Falsifier", 105, 8),
    "CASPER": ("CASPER-3", "Auditor", 6, 150),
}
PANEL_W, PANEL_H = 190, 112
GOALS = {"JUSTIFIED": "目的: 妥当", "NOT_JUSTIFIED": "目的: 不当"}
ID_RE = re.compile(r"A\d+")
MAX_TASK, MAX_NAME, MAX_REASON = 160, 40, 80


class InputError(ValueError):
    """Invalid input; the message names the bad field."""


def clip(value: str, limit: int) -> str:
    """Collapse whitespace and truncate with an ellipsis."""
    text = " ".join(value.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def e(value: object) -> str:
    return html.escape(str(value), quote=True)


def need_str(value: object, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{field}: must be a non-empty string")
    return clip(value, limit)


def need_token(value: object, approaches: dict[str, str], field: str, nullable: bool) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or (value != "DONT" and value not in approaches):
        raise InputError(f"{field}: {value!r} is not DONT or a key of approaches")
    return value


def tally(votes: list[str | None]) -> str:
    """Presumption of rejection: only 2+ identical A<n> votes win, anything else is DONT."""
    for token in sorted(set(votes) - {None, "DONT"}):
        if votes.count(token) >= 2:
            return token
    return "DONT"


def validate(data: object) -> dict:
    """Return a cleaned copy of the input or raise InputError."""
    if not isinstance(data, dict):
        raise InputError("root: must be a JSON object")
    task = need_str(data.get("task"), "task", MAX_TASK)

    raw_appr = data.get("approaches")
    if not isinstance(raw_appr, dict) or "A0" not in raw_appr:
        raise InputError("approaches: must be an object containing A0")
    approaches = {}
    for key, name in raw_appr.items():
        if not ID_RE.fullmatch(key):
            raise InputError(f"approaches.{clip(key, 20)}: key must look like A<n>")
        approaches[key] = need_str(name, f"approaches.{key}", MAX_NAME)

    raw_members = data.get("members")
    if not isinstance(raw_members, dict) or set(raw_members) != set(MEMBERS):
        raise InputError("members: must have exactly MELCHIOR, BALTHASAR, CASPER")
    members = {}
    n_held = 0
    for name in MEMBERS:
        m = raw_members[name]
        if not isinstance(m, dict):
            raise InputError(f"members.{name}: must be an object")
        rounds = m.get("rounds")
        if not isinstance(rounds, list) or not 1 <= len(rounds) <= 3:
            raise InputError(f"members.{name}.rounds: must be a list of 1 to 3 items")
        if members and len(rounds) != n_held:
            raise InputError(f"members.{name}.rounds: all members must have the same number of rounds")
        n_held = len(rounds)
        rounds = [need_token(r, approaches, f"members.{name}.rounds[{i}]", False)
                  for i, r in enumerate(rounds)]
        vote = need_token(m.get("vote"), approaches, f"members.{name}.vote", True)
        goal = m.get("goal")
        if goal is not None and goal not in GOALS:
            raise InputError(f"members.{name}.goal: must be JUSTIFIED or NOT_JUSTIFIED")
        raw_changes = m.get("changes")
        if not isinstance(raw_changes, list):
            raise InputError(f"members.{name}.changes: must be a list")
        changes = []
        for i, c in enumerate(raw_changes):
            field = f"members.{name}.changes[{i}]"
            rnd = c.get("round") if isinstance(c, dict) else None
            if isinstance(rnd, bool) or rnd not in (1, 2, 3):
                raise InputError(f"{field}.round: must be 1, 2 or 3")
            changes.append((rnd, need_str(c.get("reason"), f"{field}.reason", MAX_REASON)))
        members[name] = {"rounds": rounds, "vote": vote, "goal": goal, "changes": changes}

    verdict = need_token(data.get("verdict"), approaches, "verdict", False)
    votes = [m["vote"] for m in members.values()]
    expected = tally(votes)
    if verdict != expected:
        raise InputError(f"verdict: {verdict!r} does not match the tally {expected!r}")
    return {"task": task, "approaches": approaches, "members": members,
            "verdict": verdict, "held": n_held, "dont_majority": votes.count("DONT") >= 2}


def status(token: str | None) -> tuple[str, str]:
    """Token -> (css class, kanji)."""
    if token is None:
        return "na", "応答なし"
    if token == "A0":
        return "ok", "承認"
    if token == "DONT":
        return "no", "否決"
    return "mod", "修正"


def hexagon(x: int, y: int, w: int, h: int, c: int = 22) -> str:
    pts = [(x + c, y), (x + w - c, y), (x + w, y + h / 2), (x + w - c, y + h),
           (x + c, y + h), (x, y + h / 2)]
    return " ".join(f"{px:g},{py:g}" for px, py in pts)


def panel(name: str, vote: str | None, goal: str | None) -> str:
    label, role, x, y = MEMBERS[name]
    cls, kanji = status(vote)
    pts = hexagon(x, y, PANEL_W, PANEL_H)
    cx = x + PANEL_W / 2
    size = 22 if cls == "na" else 40
    vid = "NO ANSWER" if vote is None else vote
    role_line = f"{role} · {GOALS[goal]}" if goal else role
    return (
        f'<g class="p {cls}"><polygon class="shape" points="{pts}"/>'
        f'<polygon class="fill reveal" points="{pts}"/>'
        f'<text class="nm" x="{cx:g}" y="{y + 22}">{e(label)}</text>'
        f'<text class="rl" x="{cx:g}" y="{y + 36}">{e(role_line)}</text>'
        f'<text class="pend" x="{cx:g}" y="{y + 80}">審議中</text>'
        f'<g class="reveal"><text class="kj" x="{cx:g}" y="{y + 82}" font-size="{size}">{e(kanji)}</text>'
        f'<text class="id" x="{cx:g}" y="{y + 102}">{e(vid)}</text></g></g>'
    )


def svg(members: dict) -> str:
    lines = ('<path class="ln" d="M170 120 L120 150 M230 120 L280 150 M101 206 H204 M299 206 H204"/>'
             '<path class="ln" d="M200 120 V124 M200 148 V150"/>')
    panels = "".join(panel(n, members[n]["vote"], members[n]["goal"]) for n in MEMBERS)
    return (f'<svg viewBox="0 0 400 275" class="magi">{lines}{panels}'
            '<rect x="152" y="124" width="96" height="24"/><text class="core" x="200" y="142">MAGI</text></svg>')


def verdict_html(verdict: str, held: int, dont_majority: bool) -> str:
    cls, kanji = status(verdict)
    if verdict == "A0":
        return f'<div class="verdict reveal {cls}"><b>{e(kanji)} A0</b><span>APPROVED AS STATED</span></div>'
    if verdict != "DONT":
        return (f'<div class="verdict reveal {cls}"><b>修正可決 {e(verdict)}</b>'
                '<span>APPROVED WITH MODIFICATION</span></div>')
    if held == 1:
        note = "REJECTED AT GOAL TRIAL"
    elif dont_majority:
        note = "DO NOT BUILD"
    else:
        note = "NO MAJORITY — REJECTED BY DEFAULT"
    return f'<div class="verdict reveal {cls}"><b>{e(kanji)}</b><span>{note}</span></div>'


def cell(token: str | None, changed: bool) -> str:
    cls, kanji = status(token)
    mark = " chg" if changed else ""
    return f'<td class="c {cls}{mark}"><b>{e(token or "—")}</b><i>{e(kanji)}</i></td>'


def goal_mark(goal: str | None) -> str:
    if goal is None:
        return ""
    return f'<small class="gm">{"GOAL ✓" if goal == "JUSTIFIED" else "GOAL ✗"}</small>'


def timeline(members: dict, held: int) -> str:
    head = "<th></th><th>R0</th><th>R1</th><th>R2</th><th>VOTE</th>"
    rows = []
    for name, m in members.items():
        rounds = m["rounds"]
        cells = "".join(cell(t, i > 0 and t != rounds[i - 1]) for i, t in enumerate(rounds))
        cells += '<td class="c skip">—</td>' * (3 - held)
        vote_changed = m["vote"] is not None and m["vote"] != rounds[-1]
        rows.append(f'<tr><th class="who">{e(name)}{goal_mark(m["goal"])}</th>{cells}{cell(m["vote"], vote_changed)}</tr>')
        if m["changes"]:
            notes = "".join(f"<div>▲ R{r}: {e(why)}</div>" for r, why in m["changes"])
            rows.append(f'<tr class="rs"><td colspan="5">{notes}</td></tr>')
    return f'<table><tr>{head}</tr>{"".join(rows)}</table>'


def legend(approaches: dict) -> str:
    items = "".join(f"<li><b>{e(k)}</b> {e(v)}</li>"
                    for k, v in sorted(approaches.items(), key=lambda kv: int(kv[0][1:])))
    return f'<ul class="lg">{items}<li><b>DONT</b> 否決 — do not build</li></ul>'


CSS = """
*{box-sizing:border-box;min-width:0}
html,body{max-width:100%}
body{margin:0;background:#000;color:#ff8c1a;font:14px/1.4 "Hiragino Sans","Hiragino Kaku Gothic ProN","Noto Sans CJK JP","Yu Gothic",sans-serif}
.wrap{width:100%;max-width:640px;margin:0 auto;padding:10px}
header{border:2px solid #ff8c1a;padding:6px 10px;display:flex;flex-wrap:wrap;gap:0 10px;justify-content:space-between;align-items:baseline;font-weight:700;letter-spacing:.12em}
header small{font-size:12px;letter-spacing:.05em}
.task{margin:8px 0;padding:6px 10px;border-left:4px solid #ff8c1a;overflow-wrap:anywhere}
.task small{display:block;font-size:11px;opacity:.7}
.ok{--c:#2de2b0}.mod{--c:#ffb000}.no{--c:#ff2a2a}.na{--c:#8a8a8a}
svg.magi{display:block;width:100%;max-width:100%;height:auto}
svg.magi rect{fill:#000}
.shape{fill:#000;stroke:#ff8c1a;stroke-width:2}
.ln{stroke:#ff8c1a;stroke-width:1.5;fill:none;opacity:.6}
.fill{fill:var(--c);fill-opacity:.22;stroke:var(--c);stroke-width:2}
text{text-anchor:middle;fill:#ff8c1a}
.nm{font:700 14px ui-monospace,Menlo,monospace}
.rl{font:10px ui-monospace,Menlo,monospace;opacity:.8}
.kj{font-weight:900;fill:var(--c)}
.id{font:700 14px ui-monospace,Menlo,monospace;fill:var(--c)}
.pend{font-size:24px;font-weight:700;animation:pend 1.5s forwards}
.core{font:700 20px ui-monospace,Menlo,monospace;letter-spacing:.3em}
.reveal{opacity:0;animation:show .01s 1.5s forwards}
@keyframes pend{0%,40%,80%{opacity:1}20%,60%{opacity:.1}90%{opacity:1}100%{opacity:0}}
@keyframes show{to{opacity:1}}
.verdict{margin:8px 0;padding:8px;border:3px solid var(--c);color:var(--c);background:rgba(255,255,255,.04);text-align:center}
.verdict b{display:block;font-size:28px;letter-spacing:.1em}
.verdict span{font:11px ui-monospace,Menlo,monospace;letter-spacing:.2em}
table{width:100%;table-layout:fixed;border-collapse:collapse;font:12px ui-monospace,Menlo,"Noto Sans Mono CJK JP",monospace}
th{font-weight:400;opacity:.7;padding:3px 2px;text-align:center}
th.who{text-align:left;opacity:1;font-weight:700;width:24%;overflow:hidden}
td.c{border:1px solid #333;text-align:center;padding:3px 1px;color:var(--c);background:rgba(255,255,255,.04)}
td.c b{display:block}td.c i{display:block;font:10px "Hiragino Sans","Noto Sans CJK JP",sans-serif}
th small.gm{display:block;font-size:10px;font-weight:400}
td.skip{color:#8a8a8a;font-size:10px}
td.chg{outline:2px solid #ff8c1a;outline-offset:-2px}
td.chg b::after{content:" ▲";font-size:9px;color:#ff8c1a}
tr.rs td{padding:2px 4px 6px;font-size:11px;opacity:.85;overflow-wrap:anywhere;word-break:break-word}
ul.lg{list-style:none;margin:10px 0 0;padding:0;font-size:12px;border-top:1px solid #ff8c1a}
ul.lg li{padding:2px 0;overflow-wrap:anywhere}ul.lg b{font-family:ui-monospace,Menlo,monospace;display:inline-block;min-width:3.5em}
@media (prefers-reduced-motion:reduce){.pend{display:none}.reveal{opacity:1;animation:none}}
"""


def render(d: dict) -> str:
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>MAGI SYSTEM</title><style>{CSS}</style></head><body><div class="wrap">'
        '<header><span>MAGI SYSTEM</span><small>提訴 / 決議</small></header>'
        f'<div class="task"><small>提訴</small>{e(d["task"])}</div>'
        f'{svg(d["members"])}{verdict_html(d["verdict"], d["held"], d["dont_majority"])}'
        f'{timeline(d["members"], d["held"])}{legend(d["approaches"])}'
        '</div></body></html>'
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Render a MAGI result JSON to HTML.")
    ap.add_argument("input")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    try:
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
        page = render(validate(data))
    except (OSError, json.JSONDecodeError, InputError) as exc:
        print(f"magi-render: invalid input: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.write_text(page, encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
