import pytest

from rir_updater.exceptions import ApiError, CredentialError
from rir_updater.main import _try
from rir_updater.summary import Summary


class TestTry:
    def test_returns_result_on_success(self):
        s = Summary()
        result = _try(s, "RADb", "2001:db8::/32", lambda: "created")
        assert result == "created"
        assert s.has_errors() is False

    def test_records_apierror_and_returns_none(self):
        s = Summary()

        def boom():
            raise ApiError("create failed (400): conflict")

        result = _try(s, "RADb", "2001:db8::/32", boom)
        assert result is None
        assert s.has_errors() is True

    def test_isolates_failure_so_next_call_still_runs(self):
        s = Summary()

        def boom():
            raise ApiError("down")

        assert _try(s, "RADb", "a", boom) is None
        # A subsequent object still syncs — the failure did not abort the batch.
        assert _try(s, "RADb", "b", lambda: "created") == "created"
        assert s.has_errors() is True

    def test_non_apierror_propagates(self):
        s = Summary()

        def boom():
            raise CredentialError("op not signed in")

        # Only ApiError is isolated; other errors must still abort the run.
        with pytest.raises(CredentialError):
            _try(s, "RADb", "a", boom)
