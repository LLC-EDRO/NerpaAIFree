"""Execution-scoped provider usage, including failed responses and worker threads."""
import logging
from concurrent.futures import ThreadPoolExecutor as BaseExecutor
from contextvars import ContextVar, copy_context
from threading import RLock

from app.runtime.cancellation import check_cancelled

_current = ContextVar("provider_usage", default=None)


class UsageLedger:
    def __init__(self, on_update=None):
        self.on_update = on_update
        self.lock = RLock()
        self.calls = []

    def metrics(self):
        with self.lock:
            calls = list(self.calls)
        return {
            "token_usage_recorded": True,
            "input_tokens": sum(c["input_tokens"] for c in calls),
            "output_tokens": sum(c["output_tokens"] for c in calls),
            "cached_input_tokens": sum(c["cached_input_tokens"] for c in calls),
            "reasoning_tokens": sum(c["reasoning_tokens"] for c in calls),
            "provider_calls": len(calls),
            "usage_missing_calls": sum(not c["usage_available"] for c in calls),
        }


def begin_usage(on_update=None):
    ledger = UsageLedger(on_update=on_update)
    return ledger, _current.set(ledger)


def end_usage(token):
    _current.reset(token)


def record_usage(response, *, model, operation):
    ledger = _current.get()
    if ledger is None:
        return
    def get(value, key, default=None):
        return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)
    usage = get(response, "usage")
    input_tokens = get(usage, "input_tokens", get(usage, "prompt_tokens"))
    output_tokens = get(usage, "output_tokens", 0 if operation == "embeddings" else None)
    entry = {
        "model": model, "operation": operation,
        "usage_available": usage is not None and input_tokens is not None and output_tokens is not None,
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "cached_input_tokens": int(get(get(usage, "input_tokens_details"), "cached_tokens", 0) or 0),
        "reasoning_tokens": int(get(get(usage, "output_tokens_details"), "reasoning_tokens", 0) or 0),
    }
    with ledger.lock:
        ledger.calls.append(entry)
        if ledger.on_update is not None:
            try:
                ledger.on_update(ledger.metrics(), list(ledger.calls))
            except Exception:
                # Accounting persistence must not cause a paid request to retry.
                logging.getLogger(__name__).exception("Could not persist incremental token usage")


class ThreadPoolExecutor(BaseExecutor):
    """Carry each submitting execution's ledger into its workers."""
    def submit(self, fn, /, *args, **kwargs):
        check_cancelled()
        def checked():
            check_cancelled()
            return fn(*args, **kwargs)
        return super().submit(copy_context().run, checked)


def aggregate_usage(metrics_rows):
    """Sum authoritative execution ledgers, never the last agent's totals."""
    rows = list(metrics_rows)
    recorded = [row for row in rows if row.get("token_usage_recorded") is True]
    totals = {key: sum(int(row.get(key, 0) or 0) for row in recorded) for key in (
        "input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens",
        "provider_calls", "usage_missing_calls",
    )}
    return {
        **totals,
        "token_usage_recorded": bool(recorded),
        "token_usage_complete": bool(rows) and len(recorded) == len(rows) and totals["usage_missing_calls"] == 0
            and all(row.get("token_usage_complete") is not False for row in rows),
        "usage_unrecorded_executions": len(rows) - len(recorded),
    }
