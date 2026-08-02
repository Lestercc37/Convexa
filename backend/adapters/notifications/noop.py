from backend.domain.entities import FlowEvent, GammaAggregate


class NoopNotificationService:
    def notify(self, event: FlowEvent | GammaAggregate) -> None:
        return None
