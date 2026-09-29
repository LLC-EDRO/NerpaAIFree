"""Cooperative run cancellation that cannot be mistaken for a provider failure."""
from contextvars import ContextVar


class RunCancelled(BaseException):
    """Escape retries, repair loops and broad provider exception handlers."""


_check = ContextVar('run_cancellation_check', default=None)


def bind_cancellation(check):
    return _check.set(check)


def reset_cancellation(token):
    _check.reset(token)


def check_cancelled():
    check = _check.get()
    if check is not None and check():
        raise RunCancelled('Run stopped by user')
