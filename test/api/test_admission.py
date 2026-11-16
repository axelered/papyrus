import pytest

from server.api.admission import Admission
from server.errors import QueueFull


def test_slot_tracks_in_flight_and_releases():
    admission = Admission(2)

    with admission.slot():
        assert (admission.in_flight, admission.available) == (1, 1)
        with admission.slot():
            assert admission.available == 0

    assert admission.in_flight == 0


def test_rejects_when_full_without_blocking():
    admission = Admission(1)

    with admission.slot(), pytest.raises(QueueFull), admission.slot():
        pass

    assert admission.in_flight == 0


def test_releases_slot_on_error():
    admission = Admission(1)

    with pytest.raises(RuntimeError), admission.slot():
        raise RuntimeError

    assert admission.available == 1
