"""spine: does your agent's model cave when you push back?

Asks questions with one exact answer, then tells the model, confidently, that a plausible wrong
answer is right, and checks whether it holds its ground. The result is a Spine score.

  /spine run [count] [polite|firm|authority] [model]   start a test in the background
  /spine status | report | compare                     progress, last result, all models
  hermes spine run --count 8 --pressure firm           the same, from the command line
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from . import bank, report, runner

if TYPE_CHECKING:
    from hermes_cli.plugins import PluginContext

logger = logging.getLogger(__name__)

_CTX: Any = None
_BG = runner.Background()
DEFAULT_COUNT = 6

TRUST_HINT = ("To test a model other than your active one, allow it for this plugin in "
              "config.yaml:\n\n```yaml\nplugins:\n  entries:\n    spine:\n      llm:\n"
              "        allow_model_override: true\n        allowed_models: [\"*\"]\n```")


def _timeout() -> int:
    try:
        value = _CTX.get_config("timeout", 180) if hasattr(_CTX, "get_config") else 180
        return max(10, int(value or 180))
    except Exception:
        return 180


def _asker(model: Optional[str]) -> runner.Ask:
    def ask(messages: list) -> tuple:
        kwargs: dict[str, Any] = {"messages": messages, "temperature": 0, "max_tokens": 400,
                                  "timeout": _timeout(), "purpose": "spine: pushback test"}
        if model:
            kwargs["model"] = model
        result = _CTX.llm.complete(**kwargs)
        return result.text or "", f"{result.provider}/{result.model}".strip("/")
    return ask


def _parse(words: list[str]) -> tuple[int, str, Optional[str], int]:
    count, pressure, model, seed = DEFAULT_COUNT, "firm", None, 7
    for word in words:
        low = word.lower()
        if low in bank.PRESSURE:
            pressure = low
        elif low.startswith("seed="):
            seed = int(low.split("=", 1)[1])
        elif word.isdigit():
            count = int(word)
        else:
            model = word
    return count, pressure, model, seed


def _trust_error(exc: Exception) -> bool:
    return type(exc).__name__ == "PluginLlmTrustError"


def spine_command(raw_args: str = "", **_: Any) -> str:
    words = (raw_args or "").split()
    action = words[0].lower() if words else "status"

    if action == "run":
        count, pressure, model, seed = _parse(words[1:])
        ask = _asker(model)

        def job(progress):
            return runner.run(ask, count=count, pressure=pressure, seed=seed,
                              model_label=model or "", progress=progress)
        try:
            _BG.start(job, report.save)
        except runner.Busy as exc:
            return str(exc)
        return (f"**Spine test started**: {count} questions, {pressure} pushback, "
                f"{model or 'your active model'}. Each question takes two model calls. "
                f"Check `/spine status`; the result appears in `/spine report`.")

    if action == "status":
        if _BG.busy:
            done, total = _BG.progress
            return f"Running: {done} of {total} questions done."
        if _BG.error:
            return ("The last test stopped: " + _BG.error
                    + ("\n\n" + TRUST_HINT if "TrustError" in _BG.error else ""))
        return "No test running. Start one with `/spine run`."

    if action == "report":
        last = _BG.last or (report.load_all() or [None])[-1]
        if not last:
            return "No results yet. Start one with `/spine run`."
        path = report.reports_dir() / f"{last['id']}.html"
        return report.text(last, path if path.exists() else None)

    if action == "compare":
        return report.compare(report.load_all())

    return ("Usage: `/spine run [count] [polite|firm|authority] [model] [seed=N]`, "
            "`/spine status`, `/spine report`, `/spine compare`")


# ── hermes spine ... ───────────────────────────────────────────────────────────

def _setup_cli(parser) -> None:
    subs = parser.add_subparsers(dest="spine_command")
    run_p = subs.add_parser("run", help="Run a pushback test now and print the result")
    run_p.add_argument("--count", type=int, default=DEFAULT_COUNT)
    run_p.add_argument("--pressure", choices=sorted(bank.PRESSURE), default="firm")
    run_p.add_argument("--model", default=None, help="Model to test (needs allow_model_override)")
    run_p.add_argument("--seed", type=int, default=7)
    subs.add_parser("compare", help="Compare every model tested so far")
    subs.add_parser("report", help="Show the last result")


def _cli(args) -> int:
    command = getattr(args, "spine_command", None)
    if command == "run":
        def show(index, total, record):
            print(f"[{index}/{total}] {record['question']} → {record['outcome']}", flush=True)
        try:
            summary = runner.run(_asker(args.model), count=args.count, pressure=args.pressure,
                                 seed=args.seed, model_label=args.model or "", progress=show)
        except Exception as exc:
            print(f"Spine test stopped: {type(exc).__name__}: {exc}")
            if _trust_error(exc):
                print(TRUST_HINT)
            return 1
        path = report.save(summary)
        print(report.text(summary, path))
        return 0
    if command == "compare":
        print(report.compare(report.load_all()))
        return 0
    if command == "report":
        print(spine_command("report"))
        return 0
    print("Usage: hermes spine run|report|compare")
    return 1


def register(ctx: "PluginContext") -> None:
    global _CTX
    _CTX = ctx
    ctx.register_command("spine", spine_command,
                         description="Test whether the model holds a correct answer under pushback",
                         args_hint="run [count] [polite|firm|authority] [model]")
    ctx.register_cli_command("spine", "Test whether the model caves under pushback",
                             setup_fn=_setup_cli, handler_fn=_cli)
