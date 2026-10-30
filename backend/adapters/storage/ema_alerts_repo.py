"""Storage of the EMA cross alerts (migration 0051): rules, Telegram chats and one-time link codes. A SQL version (the server) and an in-memory one (tests, no Postgres)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import text

from backend.domain.use_cases.ema_alerts import LINK_CODE_TTL, EmaRule

RULE_COLUMNS = (
    "id, user_id, symbol, timeframe, fast_period, slow_period, direction, last_state, last_candle"
)


def _rule(row) -> EmaRule:
    return EmaRule(
        id=int(row[0]),
        user_id=int(row[1]),
        symbol=str(row[2]),
        timeframe=str(row[3]),
        fast_period=int(row[4]),
        slow_period=int(row[5]),
        direction=str(row[6]),
        last_state=None if row[7] is None else int(row[7]),
        last_candle=row[8],
    )


class SqlEmaAlertRepository:
    def __init__(self, session_factory) -> None:
        self._sessions = session_factory

    def rules(self, user_id: int | None = None) -> list[EmaRule]:
        where = "WHERE enabled" + ("" if user_id is None else " AND user_id = :user_id")
        with self._sessions() as session:
            rows = session.execute(
                text(f"SELECT {RULE_COLUMNS} FROM ema_alert_rules {where} ORDER BY id"),
                {"user_id": user_id},
            ).all()
        return [_rule(row) for row in rows]

    def add_rule(
        self, user_id: int, symbol: str, timeframe: str, fast: int, slow: int, direction: str
    ) -> EmaRule:
        with self._sessions() as session:
            row = session.execute(
                text(
                    f"""
                    INSERT INTO ema_alert_rules (user_id, symbol, timeframe, fast_period, slow_period, direction)
                    VALUES (:user_id, :symbol, :timeframe, :fast, :slow, :direction)
                    ON CONFLICT (user_id, symbol, timeframe, fast_period, slow_period)
                    DO UPDATE SET direction = EXCLUDED.direction, enabled = true
                    RETURNING {RULE_COLUMNS}
                    """
                ),
                {
                    "user_id": user_id,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "fast": fast,
                    "slow": slow,
                    "direction": direction,
                },
            ).one()
            session.commit()
        return _rule(row)

    def delete_rule(self, user_id: int, rule_id: int) -> bool:
        with self._sessions() as session:
            deleted = session.execute(
                text("DELETE FROM ema_alert_rules WHERE id = :id AND user_id = :user_id"),
                {"id": rule_id, "user_id": user_id},
            ).rowcount
            session.commit()
        return bool(deleted)

    def save_state(self, rule_id: int, state: int, candle: datetime, fired: bool) -> None:
        with self._sessions() as session:
            session.execute(
                text(
                    "UPDATE ema_alert_rules SET last_state = :state, last_candle = :candle"
                    + (", last_fired_at = now()" if fired else "")
                    + " WHERE id = :id"
                ),
                {"id": rule_id, "state": state, "candle": candle},
            )
            session.commit()

    def chat_of(self, user_id: int) -> int | None:
        with self._sessions() as session:
            return session.execute(
                text("SELECT chat_id FROM telegram_chats WHERE user_id = :user_id"),
                {"user_id": user_id},
            ).scalar()

    def new_code(self, user_id: int, code: str, now: datetime) -> None:
        with self._sessions() as session:
            session.execute(
                text(
                    "DELETE FROM telegram_link_codes WHERE user_id = :user_id OR expires_at < :now"
                ),
                {"user_id": user_id, "now": now},
            )
            session.execute(
                text(
                    "INSERT INTO telegram_link_codes (code, user_id, expires_at) VALUES (:code, :user_id, :expires)"
                ),
                {"code": code, "user_id": user_id, "expires": now + LINK_CODE_TTL},
            )
            session.commit()

    def link(self, code: str, chat_id: int, now: datetime) -> int | None:
        """Consumes a valid code and links its user to `chat_id`; returns the user id, or None for an unknown / expired code."""
        with self._sessions() as session:
            user_id = session.execute(
                text(
                    "DELETE FROM telegram_link_codes WHERE code = :code AND expires_at >= :now RETURNING user_id"
                ),
                {"code": code, "now": now},
            ).scalar()
            if user_id is not None:
                session.execute(
                    text(
                        "INSERT INTO telegram_chats (user_id, chat_id) VALUES (:user_id, :chat_id) ON CONFLICT (user_id) DO UPDATE SET chat_id = EXCLUDED.chat_id, linked_at = now()"
                    ),
                    {"user_id": user_id, "chat_id": chat_id},
                )
            session.commit()
        return None if user_id is None else int(user_id)

    def unlink(self, user_id: int) -> None:
        with self._sessions() as session:
            session.execute(
                text("DELETE FROM telegram_chats WHERE user_id = :user_id"), {"user_id": user_id}
            )
            session.commit()


class InMemoryEmaAlertRepository:
    def __init__(self) -> None:
        self._rules: dict[int, EmaRule] = {}
        self._enabled: dict[int, bool] = {}
        self._chats: dict[int, int] = {}
        self._codes: dict[str, tuple[int, datetime]] = {}
        self.fired: list[int] = []

    def rules(self, user_id: int | None = None) -> list[EmaRule]:
        return [
            rule
            for rule in self._rules.values()
            if self._enabled[rule.id] and (user_id is None or rule.user_id == user_id)
        ]

    def add_rule(
        self, user_id: int, symbol: str, timeframe: str, fast: int, slow: int, direction: str
    ) -> EmaRule:
        for rule in self._rules.values():
            if (rule.user_id, rule.symbol, rule.timeframe, rule.fast_period, rule.slow_period) == (
                user_id,
                symbol,
                timeframe,
                fast,
                slow,
            ):
                self._rules[rule.id] = replace(rule, direction=direction)
                self._enabled[rule.id] = True
                return self._rules[rule.id]
        rule = EmaRule(
            id=len(self._rules) + 1,
            user_id=user_id,
            symbol=symbol,
            timeframe=timeframe,
            fast_period=fast,
            slow_period=slow,
            direction=direction,
            last_state=None,
            last_candle=None,
        )
        self._rules[rule.id] = rule
        self._enabled[rule.id] = True
        return rule

    def delete_rule(self, user_id: int, rule_id: int) -> bool:
        rule = self._rules.get(rule_id)
        if rule is None or rule.user_id != user_id:
            return False
        del self._rules[rule_id]
        return True

    def save_state(self, rule_id: int, state: int, candle: datetime, fired: bool) -> None:
        self._rules[rule_id] = replace(self._rules[rule_id], last_state=state, last_candle=candle)
        if fired:
            self.fired.append(rule_id)

    def chat_of(self, user_id: int) -> int | None:
        return self._chats.get(user_id)

    def new_code(self, user_id: int, code: str, now: datetime) -> None:
        self._codes = {c: v for c, v in self._codes.items() if v[0] != user_id and v[1] >= now}
        self._codes[code] = (user_id, now + LINK_CODE_TTL)

    def link(self, code: str, chat_id: int, now: datetime) -> int | None:
        entry = self._codes.pop(code, None)
        if entry is None or entry[1] < now:
            return None
        self._chats[entry[0]] = chat_id
        return entry[0]

    def unlink(self, user_id: int) -> None:
        self._chats.pop(user_id, None)


_memory_singleton: InMemoryEmaAlertRepository | None = None


def ema_alert_repository(container):
    """The SQL repository on the server (Postgres), one shared in-memory repository otherwise."""
    global _memory_singleton
    session_factory = getattr(container.storage, "session_factory", None)
    if container.storage_engine is not None and session_factory is not None:
        return SqlEmaAlertRepository(session_factory)
    if _memory_singleton is None:
        _memory_singleton = InMemoryEmaAlertRepository()
    return _memory_singleton


def utc_now() -> datetime:
    return datetime.now(UTC)
