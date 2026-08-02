from datetime import date
from decimal import Decimal

from backend.adapters.providers.mock import MockDataProvider
from backend.domain.use_cases import LoadOptionChainUseCase
from backend.domain.entities import ContractType


def test_load_option_chain_use_case_returns_deterministic_chain() -> None:
    use_case = LoadOptionChainUseCase(MockDataProvider())

    chain = use_case.execute("spy")

    assert chain.symbol == "SPY"
    assert chain.as_of.isoformat() == "2026-01-15T14:30:00+00:00"
    assert len(chain.contracts) == 6
    assert {contract.strike for contract in chain.contracts} == {
        Decimal("540"),
        Decimal("545"),
        Decimal("550"),
    }
    assert {contract.contract_type for contract in chain.contracts} == {
        ContractType.CALL,
        ContractType.PUT,
    }
    assert {contract.expiration for contract in chain.contracts} == {date(2026, 2, 20)}


def test_load_option_chain_use_case_forwards_expiration_filter() -> None:
    use_case = LoadOptionChainUseCase(MockDataProvider())

    chain = use_case.execute("qqq", expiration=date(2026, 3, 20))

    assert chain.symbol == "QQQ"
    assert {contract.expiration for contract in chain.contracts} == {date(2026, 3, 20)}
