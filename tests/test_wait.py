"""Bounded waiting: back-off, timeout and which errors are retried (no AWS access, no real sleeping)."""

import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from lib import wait


class FakeClock:
    """Stands in for time.monotonic and time.sleep, recording every pause."""

    def __init__(self) -> None:
        """Start at 0 s."""
        self.now, self.pauses = 0.0, []

    def monotonic(self) -> float:
        """Return the fake time."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Advance the fake time instead of sleeping."""
        self.pauses.append(seconds)
        self.now += seconds


class WaitTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        patcher = mock.patch.object(wait, "time", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_until_returns_the_first_value_and_backs_off(self):
        answers = iter([None, None, None, None, "READY"])
        self.assertEqual(wait.until(lambda: next(answers), "x", first=2, most=5), "READY")
        self.assertEqual(self.clock.pauses, [2, 4, 5, 5])

    def test_until_gives_up_after_the_timeout(self):
        with self.assertRaises(TimeoutError):
            wait.until(lambda: None, "x", timeout=10, first=3, most=3)
        self.assertEqual(sum(self.clock.pauses), 10)  # the last pause is cut to the remaining time

    def test_retry_repeats_only_retryable_errors(self):
        calls = iter([ValueError("role not ready"), ValueError("role not ready"), None])

        def call():
            error = next(calls)
            if error:
                raise error
            return None  # a call that returns None still counts as done

        self.assertIsNone(wait.retry(call, lambda e: "not ready" in str(e), "x"))
        self.assertEqual(len(self.clock.pauses), 2)

    def test_retry_raises_other_errors_at_once(self):
        def call():
            raise KeyError("other")

        with self.assertRaises(KeyError):
            wait.retry(call, lambda e: isinstance(e, ValueError), "x")
        self.assertEqual(self.clock.pauses, [])

    def test_retry_raises_the_last_service_error_after_the_timeout(self):
        def call():
            raise ValueError("role not ready")

        with self.assertRaisesRegex(ValueError, "role not ready"):
            wait.retry(call, lambda e: True, "x", timeout=5)


if __name__ == "__main__":
    unittest.main()
