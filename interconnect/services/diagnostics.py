"""In-memory delivery diagnostics for runtime observability."""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from ..interfaces import DispatchResult
from ..models import MessageEnvelope


@dataclass(frozen=True)
class DeliveryRecord:
    """Compact record of one attempted delivery."""

    timestamp: datetime
    message_id: str
    direction: str
    route_id: str
    source_type: str
    source_alias: str
    target_type: str
    target_alias: str
    ok: bool
    message: str


@dataclass(frozen=True)
class DeliveryStats:
    """Aggregated counters for recent delivery records."""

    history_limit: int
    retained: int
    total: int
    succeeded: int
    failed: int
    by_target_type: dict[str, int]


class DeliveryDiagnostics:
    """Tracks recent delivery attempts without persisting message content."""

    def __init__(self, history_limit: int = 100) -> None:
        self._history_limit = max(0, history_limit)
        self._records: deque[DeliveryRecord] = deque(maxlen=self._history_limit)
        self._total = 0
        self._succeeded = 0
        self._failed = 0
        self._by_target_type: Counter[str] = Counter()

    @property
    def history_limit(self) -> int:
        """Returns the maximum number of retained recent records."""

        return self._history_limit

    def observe(self, envelope: MessageEnvelope, result: DispatchResult) -> None:
        """Records one delivery result."""

        self._total += 1
        if result.ok:
            self._succeeded += 1
        else:
            self._failed += 1

        target_type = result.target.type or envelope.target.type or "unknown"
        self._by_target_type[target_type] += 1
        if self._history_limit == 0:
            return

        self._records.append(
            DeliveryRecord(
                timestamp=datetime.now(timezone.utc),
                message_id=envelope.message_id,
                direction=envelope.direction,
                route_id=envelope.route_id,
                source_type=envelope.source.type,
                source_alias=envelope.source.alias,
                target_type=target_type,
                target_alias=result.target.alias or envelope.target.alias,
                ok=result.ok,
                message=result.message,
            )
        )

    def recent(
        self,
        limit: int = 10,
        *,
        only_failed: bool = False,
    ) -> tuple[DeliveryRecord, ...]:
        """Returns newest delivery records first."""

        normalized_limit = max(0, min(limit, self._history_limit or limit))
        if normalized_limit == 0:
            return ()

        records: Iterable[DeliveryRecord] = reversed(self._records)
        if only_failed:
            records = (record for record in records if not record.ok)
        return tuple(list(records)[:normalized_limit])

    def stats(self) -> DeliveryStats:
        """Returns aggregate counters since runtime startup."""

        return DeliveryStats(
            history_limit=self._history_limit,
            retained=len(self._records),
            total=self._total,
            succeeded=self._succeeded,
            failed=self._failed,
            by_target_type=dict(self._by_target_type),
        )
