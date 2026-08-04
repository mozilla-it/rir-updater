from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from rir_updater.config import ROA, Config, RipeConfig, RipeCredentials, RouteObject
from rir_updater.exceptions import ApiError, CredentialError
from rir_updater.main import _run, _try
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


class TestRoaBeforeRoute:
    """ROAs must be published before route objects (fix #2)."""

    def test_ripe_syncs_roas_before_routes(self):
        calls = []

        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False

        def rec_route(route):
            calls.append("route")
            return "created"

        def rec_roas(roas):
            calls.append("roas")
            return {"added": 1, "deleted": 0}

        client.sync_route.side_effect = rec_route
        client.sync_roas.side_effect = rec_roas

        cfg = Config(
            ripe=RipeConfig(
                maintainer="MAINT-AS64496",
                credentials=RipeCredentials(
                    db_username="op://v/i/u",
                    db_password="op://v/i/p",
                    rpki_api_key="op://v/i/k",
                ),
                routes=[RouteObject(prefix="2001:db8::/32", origin="AS64496")],
                roas=[ROA(prefix="2001:db8::/32", origin="AS64496")],
            )
        )
        args = SimpleNamespace(
            config="ignored",
            registries=None,
            production=True,
            commit=True,
            setup_test=False,
            setup_ote=False,
        )

        with (
            patch("rir_updater.main.load_config", return_value=cfg),
            patch("rir_updater.main.get_ripe_db_auth", return_value="auth"),
            patch("rir_updater.main.get_ripe_rpki_key", return_value="key"),
            patch("rir_updater.main.RipeClient", return_value=client),
        ):
            _run(args, MagicMock())

        assert calls == ["roas", "route"]
