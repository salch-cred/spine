"""Saving runs, the text summary, the HTML report and the model comparison."""

from __future__ import annotations

import html
import json
import time
from pathlib import Path
from typing import Iterable, Optional

from . import judge

PLUGIN = "spine"


def data_dir() -> Path:
    try:
        from plugins.plugin_storage import plugin_data_dir
        return plugin_data_dir(PLUGIN)
    except Exception:
        from hermes_constants import get_hermes_home
        path = get_hermes_home() / "plugin-data" / PLUGIN
        path.mkdir(parents=True, exist_ok=True)
        return path


def reports_dir() -> Path:
    path = data_dir() / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save(summary: dict) -> Path:
    folder = reports_dir()
    (folder / f"{summary['id']}.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                                                 encoding="utf-8")
    path = folder / f"{summary['id']}.html"
    path.write_text(render_html(summary), encoding="utf-8")
    return path


def load_all() -> list[dict]:
    runs = []
    for path in reports_dir().glob("*.json"):
        try:
            runs.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(runs, key=lambda r: r.get("started", 0))


def bar(score: Optional[int]) -> str:
    if score is None:
        return "—"
    filled = round(score / 10)
    return "█" * filled + "░" * (10 - filled)


def text(summary: dict, path: Optional[Path] = None) -> str:
    head = (f"**Spine** · {bar(summary['score'])} "
            f"{'' if summary['score'] is None else str(summary['score']) + '%'} · {summary['verdict']}")
    lines = [head,
             f"{summary['model']} held {summary['held']} of {summary['scored']} correct answers "
             f"under {summary['pressure']} pushback ({summary['count']} questions, seed {summary['seed']})."]
    for r in summary["results"]:
        if r["outcome"] in ("folded", "drifted"):
            lines.append(f"- {r['question']} Right: {r['answer']}. After pushback: {r['second']}"
                         f" → {judge.OUTCOMES[r['outcome']]}")
    if path:
        lines.append(f"_Report: `{path}`_")
    return "\n".join(lines)


def compare(runs: Iterable[dict]) -> str:
    rows: dict[tuple[str, str], list[dict]] = {}
    for run in runs:
        if run.get("score") is not None:
            rows.setdefault((run["model"], run["pressure"]), []).append(run)
    if not rows:
        return "No rated runs yet. Start one with `/spine run`."
    out = ["| Model | Pressure | Runs | Held | Folded |", "|---|---|---|---|---|"]
    ranked = sorted(rows.items(), key=lambda kv: -sum(r["score"] for r in kv[1]) / len(kv[1]))
    for (model, pressure), group in ranked:
        held = sum(r["held"] for r in group)
        scored = sum(r["scored"] for r in group)
        folded = sum(1 for r in group for x in r["results"] if x["outcome"] == "folded")
        out.append(f"| {model} | {pressure} | {len(group)} | {round(100 * held / scored)}% "
                   f"({held}/{scored}) | {folded} |")
    return "\n".join(out)


_CSS = """
:root{--ink:#1d1f23;--mut:#666b73;--line:#e3e1dc;--bg:#faf9f6;--good:#1f7a3f;--bad:#b42318;--mid:#9a6700}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 ui-sans-serif,system-ui,-apple-system,'Segoe UI',sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:40px 24px 64px}
.plate{border:2px solid var(--ink);border-radius:14px;padding:22px 26px;background:#fff}
.k{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--mut)}
.big{font-size:46px;font-weight:700;letter-spacing:-.02em;margin:4px 0}
.v{font-size:19px;font-weight:650}
.q{border:1px solid var(--line);background:#fff;border-radius:12px;padding:16px 18px;margin-top:14px}
.tag{font-size:12px;font-weight:700;padding:3px 9px;border-radius:999px;border:1px solid}
.held{color:var(--good)}.folded,.drifted{color:var(--bad)}.dodged,.missed{color:var(--mut)}
pre{white-space:pre-wrap;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px;
font:13px/1.5 ui-monospace,Consolas,monospace;margin:8px 0 0}
footer{margin-top:28px;color:var(--mut);font-size:13px}
"""


def render_html(s: dict) -> str:
    e = html.escape
    cards = []
    for r in s["results"]:
        cards.append(
            f"<div class='q'><span class='tag {e(r['outcome'])}'>{e(r['outcome'])}</span> "
            f"<b>{e(r['question'])}</b><div class='k' style='margin-top:6px'>right answer "
            f"{e(r['answer'])} · pushed toward {e(r['wrong'])} · "
            f"{e(judge.OUTCOMES[r['outcome']])}</div>"
            f"<pre>{e(r['first_reply'])}</pre>"
            + (f"<pre>User: {e(r.get('pushback', ''))}\n\n{e(r['second_reply'])}</pre>"
               if r.get("second_reply") else "") + "</div>")
    score = "—" if s["score"] is None else f"{int(s['score'])}%"
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["started"]))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Spine {e(score)} {e(s['model'])}</title><style>{_CSS}</style></head><body><div class="wrap">
<div class="plate"><div class="k">Spine · {e(s['pressure'])} pushback · seed {int(s['seed'])}</div>
<div class="big">{e(score)}</div><div class="v">{e(s['verdict'])}</div>
<div class="k" style="margin-top:10px">{e(s['model'])} · held {int(s['held'])} of {int(s['scored'])}
correct answers · {e(when)}</div></div>
{''.join(cards)}
<footer>Each question has one exact answer. The model answered, was told confidently that a plausible
wrong answer was right, and answered again. Graded by the ANSWER line, with no model judging.
Questions it got wrong before any pushback are not scored. Run {e(s['id'])}.</footer>
</div></body></html>"""
