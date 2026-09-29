"""Per-analysis scheduling, request accounting and observable quality completion."""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import as_completed
from contextvars import ContextVar
from functools import wraps

from app.llm.usage import ThreadPoolExecutor
from app.runtime.cancellation import check_cancelled

_current: ContextVar[AnalysisWork | None] = ContextVar("template_analysis_work", default=None)


class AnalysisBudgetExceeded(RuntimeError):
    pass


class IncompleteBatchError(ValueError):
    def __init__(self, result, missing):
        super().__init__(f"Provider omitted requested IDs: {', '.join(missing)}")
        self.result = result
        self.missing = missing


class AnalysisWork:
    def __init__(self, config, progress=None, *, clock=time.monotonic):
        self.config = config
        self.clock = clock
        self.started = clock()
        self.requests = 0
        self.lock = threading.RLock()
        self.progress = progress
        self.stages: dict[str, dict[str, int]] = {}
        self.pending: set[str] = set()
        self.exhausted = False

    def remaining(self):
        return max(0.0, self.config.analysis_time_budget_seconds - (self.clock() - self.started))

    def reserve(self):
        check_cancelled()
        with self.lock:
            if self.remaining() <= 0 or self.requests >= self.config.analysis_request_budget:
                self.exhausted = True
                self.pending.add("budget:remaining_checks")
                raise AnalysisBudgetExceeded("Template analysis request/time budget exhausted; remaining checks must be resumed")
            self.requests += 1
            return self.remaining()

    def plan(self, phase, total):
        self.stages[phase] = {"total": total, "completed": 0, "cached": 0, "failed": 0}
        self.emit(phase)

    def finish(self, phase, count, *, cached=False, failed=False):
        stage = self.stages[phase]
        stage["failed" if failed else "completed"] += count
        stage["cached"] += count if cached else 0
        self.emit(phase)

    def emit(self, phase, *, status="running"):
        stage = self.stages.get(phase, {"total": 0, "completed": 0, "cached": 0, "failed": 0})
        if phase == "complete":
            stage = {key: sum(item[key] for item in self.stages.values()) for key in stage}
        event = {"phase": phase, "status": status, **stage, "requests": self.requests,
                 "request_limit": self.config.analysis_request_budget,
                 "elapsed_seconds": round(self.clock() - self.started, 2),
                 "time_limit_seconds": self.config.analysis_time_budget_seconds,
                 "message": f"{phase}: проверено {stage['completed']} из {stage['total']}"}
        if status == "partial":
            event["message"] = "Анализ сохранён частично; незавершённые проверки нужно продолжить."
        elif phase == "complete":
            event["message"] = "Все запланированные проверки завершены."
        if self.progress:
            try:
                self.progress(event)
            except Exception:
                logging.getLogger(__name__).exception("Could not persist template analysis progress")

    def jobs(self, phase, jobs, operation, units):
        """Fuse on the caller thread; workers only produce independently validated results."""
        if not jobs:
            return []
        results = []
        with ThreadPoolExecutor(max_workers=min(self.config.semantic_max_parallelism, len(jobs)),
                                thread_name_prefix=f"template-{phase}") as executor:
            futures = {executor.submit(operation, job): job for job in jobs}
            for future in as_completed(futures):
                check_cancelled()
                job = futures[future]
                try:
                    result = future.result()
                except IncompleteBatchError as exc:
                    self.pending.update(f"{phase}:{value}" for value in exc.missing)
                    self.finish(phase, len(units(job)) - len(exc.missing))
                    self.finish(phase, len(exc.missing), failed=True)
                    results.append((job, exc.result, exc))
                except Exception as exc:
                    self.pending.update(f"{phase}:{value}" for value in units(job))
                    self.finish(phase, len(units(job)), failed=True)
                    results.append((job, None, exc))
                else:
                    self.finish(phase, len(units(job)))
                    results.append((job, result, None))
        return results


def current_work() -> AnalysisWork:
    work = _current.get()
    if work is None:
        raise RuntimeError("Template work requires an active analysis session")
    return work


def call_provider(provider, method, *args, **kwargs):
    work = _current.get()
    if work is not None and not getattr(provider, "accounts_transport_requests", False):
        work.reserve()
    return method(*args, **kwargs)


class BudgetClient:
    """Account every actual HTTP attempt, including retries and fallback models."""
    def __init__(self, client, request_timeout):
        self.client = client
        self.request_timeout = request_timeout
        self.responses = self

    def create(self, **kwargs):
        work = _current.get()
        if work is not None:
            remaining = work.reserve()
            kwargs["timeout"] = min(float(kwargs.get("timeout", self.request_timeout)), remaining)
        return self.client.responses.create(**kwargs)


def analysis_session(method):
    @wraps(method)
    def wrapped(self, *args, progress_callback=None, **kwargs):
        work = AnalysisWork(self.config, progress_callback)
        token = _current.set(work)
        try:
            work.emit("preparing")
            result = method(self, *args, **kwargs)
            completion = {"status": "partial" if work.pending else "complete",
                          "pending_work": sorted(work.pending), "requests": work.requests,
                          "budget_exhausted": work.exhausted,
                          "request_limit": self.config.analysis_request_budget,
                          "time_limit_seconds": self.config.analysis_time_budget_seconds,
                          "stages": work.stages}
            result.template_model.metadata["analysis_completion"] = completion
            result.metrics.update(analysis_provider_requests=work.requests,
                                  analysis_budget_exhausted=work.exhausted,
                                  analysis_pending_checks=len(work.pending))
            if work.pending:
                warning = "Не все проверки анализа завершены; результаты сохранены, повторный запуск продолжит проверки из кэша."
                result.warnings.append(warning)
                result.template_model.diagnostics.status = "partial"
                readiness = result.template_model.readiness
                readiness.status = "partial"
                readiness.ready_for_planner = readiness.ready_for_clone_and_replace = readiness.ready_for_compose_from_slots = False
                readiness.blocking_issues.append("analysis_checks_incomplete")
                readiness.warnings.append(warning)
                result.diagnostic_events.append({"level": "warning", "code": "TEMPLATE_ANALYSIS_INCOMPLETE",
                                                 "message": warning, "data": completion})
            result.metrics["warnings"] = len(result.warnings)
            work.emit("complete", status=completion["status"])
            return result
        finally:
            _current.reset(token)
    return wrapped


def validate_coverage(values, expected, attribute="element_id"):
    identifiers = [getattr(item, attribute) for item in values]
    if len(identifiers) != len(set(identifiers)) or set(identifiers) != set(expected):
        raise ValueError("Provider response must cover every requested ID exactly once, without foreign IDs")


def validate_subset(values, expected, attribute="element_id"):
    identifiers = [getattr(item, attribute) for item in values]
    if len(identifiers) != len(set(identifiers)) or not set(identifiers).issubset(expected):
        raise ValueError("Provider response contains duplicate or foreign IDs")
    return sorted(set(expected) - set(identifiers))
