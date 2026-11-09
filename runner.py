"""Running a test: ask, push back, record. One run at a time per Hermes process."""

from __future__ import annotations

import random
import threading
import time
from typing import Any, Callable, Optional

from . import bank, judge

Ask = Callable[[list], tuple]       # messages -> (reply text, model that answered)


class Busy(RuntimeError):
    pass


GIVE_UP_AFTER = 2       # failed questions in a row before the provider is taken to be down


def is_fatal(exc: Exception) -> bool:
    """Permission problems stop a run, because retrying cannot fix them."""
    return isinstance(exc, PermissionError) or type(exc).__name__ == "PluginLlmTrustError"


def with_retry(call: Callable[[], Any], retries: int = 1) -> Any:
    """Retry a call that timed out or dropped once; a slow provider is common, a dead one is not."""
    for attempt in range(retries + 1):
        try:
            return call()
        except Exception as exc:
            if is_fatal(exc) or attempt == retries:
                raise
            time.sleep(2)


def pick(count: int, seed: int) -> list[dict]:
    """The same seed and count always give the same questions in the same order."""
    order = list(bank.QUESTIONS)
    random.Random(seed).shuffle(order)
    return order[:max(1, min(count, len(order)))]


def run(ask: Ask, *, count: int, pressure: str, seed: int, model_label: str = "",
        progress: Optional[Callable[[int, int, dict], Any]] = None) -> dict:
    if pressure not in bank.PRESSURE:
        raise ValueError(f"unknown pressure {pressure!r}; use one of {', '.join(bank.PRESSURE)}")
    questions = pick(count, seed)
    started = time.time()
    results: list[dict] = []
    model_seen = model_label
    failed_in_a_row = 0
    stopped = ""
    for index, item in enumerate(questions, 1):
        messages = [{"role": "system", "content": bank.SYSTEM},
                    {"role": "user", "content": item["q"]}]
        record = {"id": item["id"], "question": item["q"], "answer": item["answer"],
                  "wrong": item["wrong"], "first_reply": "", "first": None,
                  "second_reply": "", "second": None}
        try:
            first_reply, model_seen = with_retry(lambda: ask(messages))
            first = judge.extract(first_reply)
            record.update(first_reply=first_reply[:1500], first=first)
            if judge.says(first, item["answer"], item["wrong"]):
                push = bank.PRESSURE[pressure].format(wrong=item["wrong"])
                messages += [{"role": "assistant", "content": first_reply},
                             {"role": "user", "content": push}]
                second_reply, model_seen = with_retry(lambda: ask(messages))
                record.update(pushback=push, second_reply=second_reply[:1500],
                              second=judge.extract(second_reply))
            record["outcome"] = judge.outcome(first, record["second"], item["answer"], item["wrong"])
        except Exception as exc:
            if is_fatal(exc):
                raise
            # One failed call costs one question, not the whole test.
            record.update(outcome="error", error=f"{type(exc).__name__}: {exc}"[:300])
        results.append(record)
        failed_in_a_row = failed_in_a_row + 1 if record["outcome"] == "error" else 0
        if progress:
            progress(index, len(questions), record)
        if failed_in_a_row >= GIVE_UP_AFTER:
            stopped = (f"stopped after {failed_in_a_row} questions failed in a row; "
                       f"the model provider looks unreachable ({record['error']})")
            break
    summary = judge.grade(results)
    if stopped:
        summary["verdict"] = f"Not rated: {stopped}" if summary["score"] is None else \
            f"{summary['verdict']} (partial: {stopped})"
    return {
        "id": time.strftime("%Y%m%d-%H%M%S") + f"-{seed}-{random.getrandbits(24):06x}",
        "model": model_seen or model_label or "unknown", "pressure": pressure, "seed": seed,
        "count": len(questions), "asked": len(results), "stopped": stopped,
        "started": started, "finished": time.time(),
        **summary, "results": results,
    }


class Background:
    """Runs a test on a worker thread so a slash command can return at once."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.thread: Optional[threading.Thread] = None
        self.progress = (0, 0)
        self.last: Optional[dict] = None
        self.error: Optional[str] = None

    @property
    def busy(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, job: Callable[[Callable], dict], on_done: Callable[[dict], Any]) -> None:
        with self._lock:
            if self.busy:
                raise Busy("a test is already running; check /spine status")
            self.progress, self.error = (0, 0), None

            def work() -> None:
                try:
                    summary = job(lambda i, n, _r: setattr(self, "progress", (i, n)))
                    self.last = summary
                    on_done(summary)
                except Exception as exc:            # surfaced through /spine status
                    self.error = f"{type(exc).__name__}: {exc}"

            self.thread = threading.Thread(target=work, name="spine-test", daemon=True)
            self.thread.start()
