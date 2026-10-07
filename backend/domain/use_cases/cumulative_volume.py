from __future__ import annotations

from dataclasses import replace

from backend.domain.entities import OptionChain
from backend.domain.ports import IStorage


def merge_cumulative_volume(chain: OptionChain, storage: IStorage) -> OptionChain:
    """A contract's `volume` field comes from `IDataProvider.get_option_chain()`,
    which reads it straight off the provider's own live trade-stream state
    (ThetaStreamHub._cumulative_volume) -- always correct for a process that
    actually owns a live stream, but always 0 for one that doesn't (the new
    scheduler-only process, split out from backend/worker.py to stop the
    scheduler's own REST/JSON/object-construction work contending with
    ThetaStreamHub's event loop for the GIL -- confirmed live, 2026-09-22,
    that this contention was the real cause of a WebSocket reconnect storm
    ThetaData support attributed to us being a "slow consumer").

    WhaleAlertsEngine.process(chain) -- called right after this in
    execute() -- depends on real volume for its own detection; silently
    persisting/processing an all-zero-volume chain from the scheduler-only
    process would quietly degrade that, not just report a wrong number
    somewhere. Only touches contracts whose own volume is 0 -- a process
    WITH a live stream already has the real, current value and must never
    have it overwritten by a periodic, necessarily-lagged Postgres read of
    ANOTHER process's own snapshot (see core/stream_state_export.py's
    StreamStateExporter, the writer this reads).

    Shared by RefreshUnderlyingSnapshotUseCase (the scheduler) and the
    GET /chain/{symbol} live fallback (read_models.get_option_chain): the API
    process never starts the trade stream either, so its provider reports 0
    for every contract too -- the source of the zero-volume option chain
    snapshots found 2026-10-07."""
    zero_volume_occ_symbols = [
        contract.occ_symbol for contract in chain.contracts if contract.volume == 0
    ]
    if not zero_volume_occ_symbols:
        return chain
    real_volumes = storage.get_cumulative_volumes(zero_volume_occ_symbols)
    if not real_volumes:
        return chain
    return replace(
        chain,
        contracts=tuple(
            replace(contract, volume=real_volumes[contract.occ_symbol])
            if contract.occ_symbol in real_volumes
            else contract
            for contract in chain.contracts
        ),
    )
