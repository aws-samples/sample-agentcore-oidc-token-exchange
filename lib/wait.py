"""Bounded waiting for AWS resources that boto3 has no waiter for."""

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def until(check: Callable[[], T | None], what: str, timeout: float = 600, first: float = 2, most: float = 15) -> T:
    """Call `check` until it returns something other than None and return that value.

    The pause between calls starts at `first` seconds and doubles up to `most`; after `timeout` seconds in total
    TimeoutError is raised, so a resource that never settles stops the script instead of hanging it.
    """
    deadline = time.monotonic() + timeout
    pause = first
    while True:
        value = check()
        if value is not None:
            return value
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError(f"gave up after {timeout:.0f} s waiting for {what}")
        time.sleep(min(pause, left))
        pause = min(pause * 2, most)


def retry(call: Callable[[], T], retryable: Callable[[Exception], bool], what: str, timeout: float = 120) -> T:
    """Run `call`, repeating it while it raises an exception for which `retryable` is true.

    Used for the first call that depends on a just-created IAM role or instance profile: IAM is eventually
    consistent and has no waiter for "usable by the service", so the service's own error is the signal.
    Any other exception, or a retryable one after `timeout` seconds, is raised unchanged.
    """
    error: list[Exception] = []

    def attempt() -> tuple[T] | None:
        try:
            return (call(),)
        except Exception as e:
            if not retryable(e):
                raise
            error[:] = [e]
            return None

    try:
        return until(attempt, what, timeout=timeout)[0]
    except TimeoutError:
        raise error[0] from None
