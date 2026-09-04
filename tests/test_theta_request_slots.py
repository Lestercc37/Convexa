from __future__ import annotations

import threading
import time

from backend.adapters.providers.thetadata.request_slots import (
    InProcessThetaRequestSlots,
    build_theta_request_slots,
)


def test_in_process_slots_cap_concurrent_holders_at_the_limit() -> None:
    """The pre-existing threading.Semaphore behavior, preserved through
    the new hold() interface -- correct on its own whenever nothing in
    a separate process could also be calling ThetaData (every
    environment without a real Postgres behind it)."""
    slots = InProcessThetaRequestSlots(limit=3)
    in_flight = 0
    max_observed = 0
    lock = threading.Lock()

    def worker() -> None:
        nonlocal in_flight, max_observed
        with slots.hold():
            with lock:
                in_flight += 1
                max_observed = max(max_observed, in_flight)
            time.sleep(0.05)
            with lock:
                in_flight -= 1

    threads = [threading.Thread(target=worker) for _ in range(9)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert max_observed == 3


def test_in_process_slots_release_on_exception() -> None:
    slots = InProcessThetaRequestSlots(limit=1)

    class _Boom(Exception):
        pass

    try:
        with slots.hold():
            raise _Boom
    except _Boom:
        pass

    # If the first hold() didn't release on the exception, this would
    # deadlock -- bounded by the thread's own join timeout below, not
    # an infinite hang.
    acquired = threading.Event()

    def worker() -> None:
        with slots.hold():
            acquired.set()

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=2)

    assert acquired.is_set()


def test_build_theta_request_slots_falls_back_to_in_process_without_a_real_engine() -> None:
    result = build_theta_request_slots(
        storage_engine=None, sync_session_factory=None, holder="test", limit=8
    )
    assert isinstance(result, InProcessThetaRequestSlots)
