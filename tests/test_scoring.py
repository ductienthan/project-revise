from preflight.core.checklist import priority_for
from preflight.db.models import Priority


def test_priority_bands():
    assert [priority_for(s) for s in (1, 2, 2.5, 3.9, 4, 10)] == [
        Priority.LOW, Priority.LOW, Priority.MEDIUM, Priority.MEDIUM, Priority.HIGH, Priority.HIGH,
    ]
