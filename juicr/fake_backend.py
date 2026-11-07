"""Fake Lemonade backend for Phase 1 checks: records starts/cancels, no network."""
import itertools
import threading


class FakeBackend:
    def __init__(self):
        self._n = itertools.count(1)
        self._lock = threading.Lock()
        self.started, self.cancelled = [], []

    def start(self, trial_id):
        with self._lock:
            h = f"h{next(self._n)}"
            self.started.append((trial_id, h))
            return h

    def cancel(self, handle):
        with self._lock:
            self.cancelled.append(handle)
