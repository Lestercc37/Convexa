from __future__ import annotations

from datetime import datetime

from backend.domain.entities import GammaAggregate
from backend.domain.ports import INotificationService, IStorage
from backend.domain.use_cases.errors import NotFoundError


def get_gamma_exposure(storage: IStorage, underlying: str) -> GammaAggregate:
    gamma = storage.get_latest_gamma_aggregate(underlying)
    if gamma is None:
        raise NotFoundError(f"No gamma aggregate found for {underlying}")
    return gamma


def get_gamma_history(
    storage: IStorage, underlying: str, start: datetime, end: datetime
) -> list[GammaAggregate]:
    return storage.get_gamma_history(underlying, start, end)


def calculate_gamma_exposure(
    storage: IStorage, notifications: INotificationService, underlying: str
) -> GammaAggregate:
    """Internal GammaAggregate calculation scaffold.

    The documented calculation contract exists, but real gamma formulas are intentionally deferred.
    """
    raise NotImplementedError
