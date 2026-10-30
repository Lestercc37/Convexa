"""EMA cross alerts: each logged-in user manages their own rules and links their Telegram chat (migration 0051; the worker is backend/ema_alerts_worker.py)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.adapters.storage.ema_alerts_repo import ema_alert_repository, utc_now
from backend.api.deps import require_session
from backend.core.sessions import SessionPayload
from backend.domain.use_cases.ema_alerts import LINK_CODE_TTL, new_link_code

router = APIRouter(prefix="/ema-alerts", tags=["ema-alerts"])


class EmaRuleIn(BaseModel):
    symbol: str = Field(pattern="^[A-Za-z]{1,6}$", examples=["NQ"])
    timeframe: Literal["1m", "5m", "15m", "1h"] = "5m"
    fast_period: int = Field(default=8, ge=2, le=400)
    slow_period: int = Field(default=20, ge=2, le=400)
    direction: Literal["up", "down", "both"] = "both"


class EmaRuleOut(BaseModel):
    id: int
    symbol: str
    timeframe: str
    fast_period: int
    slow_period: int
    direction: str


class EmaAlertsOut(BaseModel):
    telegram_linked: bool
    bot_username: str
    rules: list[EmaRuleOut]


class LinkCodeOut(BaseModel):
    code: str
    expires_in_seconds: int
    bot_username: str
    link: str = Field(
        description="Opens the bot with the code already filled in (Telegram's start parameter)."
    )


def _out(rule) -> EmaRuleOut:
    return EmaRuleOut(
        id=rule.id,
        symbol=rule.symbol,
        timeframe=rule.timeframe,
        fast_period=rule.fast_period,
        slow_period=rule.slow_period,
        direction=rule.direction,
    )


@router.get("", response_model=EmaAlertsOut)
def list_rules(
    request: Request, session: SessionPayload = Depends(require_session)
) -> EmaAlertsOut:
    container = request.app.state.container
    repo = ema_alert_repository(container)
    return EmaAlertsOut(
        telegram_linked=repo.chat_of(session.user_id) is not None,
        bot_username=container.settings.telegram_bot_username,
        rules=[_out(rule) for rule in repo.rules(session.user_id)],
    )


@router.post("", response_model=EmaRuleOut, status_code=201)
def add_rule(
    body: EmaRuleIn, request: Request, session: SessionPayload = Depends(require_session)
) -> EmaRuleOut:
    if body.fast_period >= body.slow_period:
        raise HTTPException(
            status_code=422, detail="the fast EMA period must be smaller than the slow one"
        )
    repo = ema_alert_repository(request.app.state.container)
    if len(repo.rules(session.user_id)) >= 20:
        raise HTTPException(status_code=422, detail="at most 20 EMA alerts per user")
    rule = repo.add_rule(
        session.user_id,
        body.symbol.upper(),
        body.timeframe,
        body.fast_period,
        body.slow_period,
        body.direction,
    )
    return _out(rule)


@router.delete("/{rule_id}", status_code=204)
def delete_rule(
    rule_id: int, request: Request, session: SessionPayload = Depends(require_session)
) -> None:
    if not ema_alert_repository(request.app.state.container).delete_rule(session.user_id, rule_id):
        raise HTTPException(status_code=404, detail="no such alert")


@router.post("/telegram/code", response_model=LinkCodeOut)
def new_code(request: Request, session: SessionPayload = Depends(require_session)) -> LinkCodeOut:
    container = request.app.state.container
    code = new_link_code()
    ema_alert_repository(container).new_code(session.user_id, code, utc_now())
    bot = container.settings.telegram_bot_username
    return LinkCodeOut(
        code=code,
        expires_in_seconds=int(LINK_CODE_TTL.total_seconds()),
        bot_username=bot,
        link=f"https://t.me/{bot}?start={code}",
    )


@router.delete("/telegram", status_code=204)
def unlink(request: Request, session: SessionPayload = Depends(require_session)) -> None:
    ema_alert_repository(request.app.state.container).unlink(session.user_id)
