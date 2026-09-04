from __future__ import annotations

import asyncio
import json

import pytest

from backend.core.price_notifications import PriceNotificationHub, PriceNotificationListener


def test_hub_publish_delivers_only_to_subscribers_of_that_symbol() -> None:
    hub = PriceNotificationHub()
    spy_queue = hub.subscribe("SPY")
    qqq_queue = hub.subscribe("QQQ")

    hub.publish("SPY", '{"symbol": "SPY", "price": "552.25"}')

    assert spy_queue.get_nowait() == '{"symbol": "SPY", "price": "552.25"}'
    assert qqq_queue.empty()


def test_hub_publish_is_case_insensitive_on_symbol() -> None:
    hub = PriceNotificationHub()
    queue = hub.subscribe("spy")

    hub.publish("SPY", "payload")

    assert queue.get_nowait() == "payload"


def test_hub_publish_fans_out_to_every_subscriber_of_the_same_symbol() -> None:
    hub = PriceNotificationHub()
    first = hub.subscribe("SPY")
    second = hub.subscribe("SPY")

    hub.publish("SPY", "payload")

    assert first.get_nowait() == "payload"
    assert second.get_nowait() == "payload"


def test_hub_publish_with_no_subscribers_does_not_raise() -> None:
    hub = PriceNotificationHub()
    hub.publish("SPY", "payload")  # no subscribers -- must be a no-op, not an error


def test_hub_unsubscribe_stops_further_delivery() -> None:
    hub = PriceNotificationHub()
    queue = hub.subscribe("SPY")

    hub.unsubscribe("SPY", queue)
    hub.publish("SPY", "payload")

    assert queue.empty()


def test_hub_unsubscribe_of_an_unknown_symbol_does_not_raise() -> None:
    hub = PriceNotificationHub()
    queue: asyncio.Queue[str] = asyncio.Queue()
    hub.unsubscribe("SPY", queue)  # never subscribed -- must not raise


def test_listener_on_notify_publishes_the_symbol_from_the_payload() -> None:
    hub = PriceNotificationHub()
    listener = PriceNotificationListener("postgresql://unused/db", hub)
    queue = hub.subscribe("SPY")
    payload = json.dumps({"symbol": "SPY", "price": "552.25", "as_of": "2026-09-05T14:30:00+00:00"})

    listener._on_notify(None, 0, "market_price_updates", payload)  # type: ignore[arg-type]

    assert queue.get_nowait() == payload


def test_listener_on_notify_ignores_malformed_payload_without_raising() -> None:
    hub = PriceNotificationHub()
    listener = PriceNotificationListener("postgresql://unused/db", hub)
    queue = hub.subscribe("SPY")

    listener._on_notify(None, 0, "market_price_updates", "not json")  # type: ignore[arg-type]
    listener._on_notify(None, 0, "market_price_updates", "{}")  # type: ignore[arg-type]

    assert queue.empty()


@pytest.mark.asyncio
async def test_listener_stop_before_start_is_a_no_op() -> None:
    hub = PriceNotificationHub()
    listener = PriceNotificationListener("postgresql://unused/db", hub)
    await listener.stop()  # never started -- must not raise
