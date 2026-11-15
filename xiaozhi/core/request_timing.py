"""Stage timings for monitored web requests."""

from contextlib import contextmanager
from time import perf_counter


@contextmanager
def measure(request, name, *, excluding=None):
    timings = getattr(request.state, "request_timings", None)
    if timings is None:
        yield
        return
    start = perf_counter()
    excluded_before = timings.get(excluding, 0)
    try:
        yield
    finally:
        elapsed = (perf_counter() - start) * 1000 - (timings.get(excluding, 0) - excluded_before)
        timings[name] = timings.get(name, 0) + elapsed
