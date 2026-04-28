from unittest.mock import MagicMock, patch

import pytest

from rir_updater.config import ROA, RouteObject
from rir_updater.exceptions import ApiError
from rir_updater.ripe.client import PROD_URL, TEST_URL, RipeClient


def ok(status_code: int = 200, json_data: dict | None = None) -> MagicMock:
    m = MagicMock(status_code=status_code, is_error=False)
    if json_data is not None:
        m.json.return_value = json_data
    return m


def err(status_code: int, body: dict | None = None) -> MagicMock:
    m = MagicMock(status_code=status_code, is_error=True)
    m.text = str(body or "")
    if body is not None:
        m.json.return_value = body
    return m


@pytest.fixture
def client():
    with patch("rir_updater.ripe.client.httpx.Client"):
        c = RipeClient(
            db_auth="dGVzdA==", rpki_key="testkey", maintainer="MAINT-AS64496"
        )
        c._http = MagicMock()
        c._rpki_http = MagicMock()
        yield c


@pytest.fixture
def prod_client():
    with patch("rir_updater.ripe.client.httpx.Client"):
        c = RipeClient(
            db_auth="dGVzdA==",
            rpki_key="testkey",
            maintainer="MAINT-AS64496",
            use_test_env=False,
        )
        c._http = MagicMock()
        c._rpki_http = MagicMock()
        yield c


IPV4_ROUTE = RouteObject(prefix="192.0.2.0/24", origin="AS64496")
IPV6_ROUTE = RouteObject(prefix="2001:db8::/32", origin="AS64496")
IPV4_ROA = ROA(prefix="192.0.2.0/24", origin="AS64496", max_length=24)

_EXISTING_ROUTE = {
    "objects": {
        "object": [
            {
                "type": "route",
                "attributes": {
                    "attribute": [
                        {"name": "route", "value": "192.0.2.0/24"},
                        {"name": "origin", "value": "AS64496"},
                        {"name": "mnt-by", "value": "MAINT-AS64496"},
                        {"name": "source", "value": "TEST"},
                    ]
                },
            }
        ]
    }
}


class TestRouteHelpers:
    def test_object_type_ipv4(self, client):
        assert client._route_object_type("192.0.2.0/24") == "route"

    def test_object_type_ipv6(self, client):
        assert client._route_object_type("2001:db8::/32") == "route6"

    def test_route_key(self, client):
        assert client._route_key(IPV4_ROUTE) == "192.0.2.0/24AS64496"

    def test_route_key_normalises_asn(self, client):
        route = RouteObject(prefix="192.0.2.0/24", origin="as64496")
        assert client._route_key(route) == "192.0.2.0/24AS64496"

    def test_route_body_structure(self, client):
        body = client._route_body(IPV4_ROUTE)
        obj = body["objects"]["object"][0]
        assert obj["type"] == "route"
        attrs = {a["name"]: a["value"] for a in obj["attributes"]["attribute"]}
        assert attrs["route"] == "192.0.2.0/24"
        assert attrs["origin"] == "AS64496"
        assert attrs["mnt-by"] == "MAINT-AS64496"
        assert attrs["source"] == "TEST"

    def test_route_body_includes_description(self, client):
        route = RouteObject(
            prefix="192.0.2.0/24", origin="AS64496", description="My prefix"
        )
        body = client._route_body(route)
        attrs = {
            a["name"]: a["value"]
            for a in body["objects"]["object"][0]["attributes"]["attribute"]
        }
        assert attrs["descr"] == "My prefix"

    def test_route_body_prod_source(self, prod_client):
        body = prod_client._route_body(IPV4_ROUTE)
        attrs = {
            a["name"]: a["value"]
            for a in body["objects"]["object"][0]["attributes"]["attribute"]
        }
        assert attrs["source"] == "RIPE"

    def test_route_url_test_env(self, client):
        url = client._route_url(IPV4_ROUTE)
        assert url == f"{TEST_URL}/test/route"

    def test_route_url_with_key(self, client):
        url = client._route_url(IPV4_ROUTE, key="192.0.2.0/24AS64496")
        assert url == f"{TEST_URL}/test/route/192.0.2.0/24AS64496"

    def test_route_url_prod_env(self, prod_client):
        url = prod_client._route_url(IPV4_ROUTE)
        assert url == f"{PROD_URL}/ripe/route"


class TestSyncRoute:
    def test_creates_when_not_exists(self, client):
        client._http.get.return_value = MagicMock(status_code=404, is_error=False)
        client._http.post.return_value = ok()

        result = client.sync_route(IPV4_ROUTE)

        assert result == "created"
        client._http.post.assert_called_once()

    def test_updates_when_exists(self, client):
        client._http.get.return_value = ok(json_data=_EXISTING_ROUTE)
        client._http.put.return_value = ok()

        result = client.sync_route(IPV4_ROUTE)

        assert result == "updated"
        client._http.put.assert_called_once()

    def test_preserves_unmanaged_attributes_on_update(self, client):
        existing = {
            "objects": {
                "object": [
                    {
                        "type": "route",
                        "attributes": {
                            "attribute": [
                                {"name": "route", "value": "192.0.2.0/24"},
                                {"name": "remarks", "value": "Keep this"},
                                {"name": "origin", "value": "AS64496"},
                                {"name": "mnt-by", "value": "MAINT-AS64496"},
                                {"name": "source", "value": "TEST"},
                            ]
                        },
                    }
                ]
            }
        }
        route = RouteObject(
            prefix="192.0.2.0/24", origin="AS64496", description="New desc"
        )
        client._http.get.return_value = ok(json_data=existing)
        client._http.put.return_value = ok()

        client.sync_route(route)

        put_body = client._http.put.call_args.kwargs["json"]
        attrs = {
            a["name"]: a["value"]
            for a in put_body["objects"]["object"][0]["attributes"]["attribute"]
        }
        assert attrs["remarks"] == "Keep this"
        assert attrs["descr"] == "New desc"

    def test_api_error_raises(self, client):
        client._http.get.return_value = MagicMock(status_code=404, is_error=False)
        client._http.post.return_value = err(
            400,
            {"errormessages": {"errormessage": [{"text": "Syntax error in prefix"}]}},
        )

        with pytest.raises(ApiError, match="Syntax error in prefix"):
            client.sync_route(IPV4_ROUTE)

    def test_dry_run_does_not_call_api(self, client):
        client._dry_run = True
        client._http.get.return_value = MagicMock(status_code=404, is_error=False)

        result = client.sync_route(IPV4_ROUTE)

        assert result == "dry-run-create"
        client._http.post.assert_not_called()
        client._http.put.assert_not_called()

    def test_dry_run_returns_update_when_exists(self, client):
        client._dry_run = True
        client._http.get.return_value = MagicMock(status_code=200, is_error=False)

        result = client.sync_route(IPV4_ROUTE)

        assert result == "dry-run-update"


class TestDeleteRoute:
    def test_deletes_when_exists(self, client):
        client._http.delete.return_value = ok()

        result = client.delete_route(IPV4_ROUTE)

        assert result == "deleted"
        client._http.delete.assert_called_once()

    def test_not_found(self, client):
        client._http.delete.return_value = MagicMock(status_code=404, is_error=False)

        result = client.delete_route(IPV4_ROUTE)

        assert result == "not-found"

    def test_dry_run(self, client):
        client._dry_run = True

        result = client.delete_route(IPV4_ROUTE)

        assert result == "dry-run-delete"
        client._http.delete.assert_not_called()

    def test_api_error_raises(self, client):
        client._http.delete.return_value = err(
            403,
            {"errormessages": {"errormessage": [{"text": "Forbidden"}]}},
        )

        with pytest.raises(ApiError, match="Forbidden"):
            client.delete_route(IPV4_ROUTE)


class TestSyncROAs:
    def test_adds_new_roas(self, client):
        client._rpki_http.get.return_value = ok(json_data=[])
        client._rpki_http.post.return_value = ok()

        counts = client.sync_roas([IPV4_ROA])

        assert counts["added"] == 1
        assert counts["deleted"] == 0
        client._rpki_http.post.assert_called_once()

    def test_deletes_stale_roa_for_managed_prefix(self, client):
        # Stale max_length for a managed prefix; old entry should be deleted.
        client._rpki_http.get.return_value = ok(
            json_data=[
                {"prefix": "192.0.2.0/24", "asn": "AS64496", "maximalLength": 16}
            ]
        )
        client._rpki_http.post.return_value = ok()

        counts = client.sync_roas([IPV4_ROA])  # IPV4_ROA wants max_length=24

        assert counts["deleted"] == 1
        assert counts["added"] == 1

    def test_does_not_delete_unmanaged_prefix_roas(self, client):
        # ROAs for prefixes not in the config are left untouched.
        client._rpki_http.get.return_value = ok(
            json_data=[{"prefix": "10.0.0.0/8", "asn": "AS64496", "maximalLength": 8}]
        )
        client._rpki_http.post.return_value = ok()

        counts = client.sync_roas([])

        assert counts["deleted"] == 0
        client._rpki_http.post.assert_not_called()

    def test_no_changes_skips_publish(self, client):
        client._rpki_http.get.return_value = ok(
            json_data=[
                {"prefix": "192.0.2.0/24", "asn": "AS64496", "maximalLength": 24}
            ]
        )

        counts = client.sync_roas([IPV4_ROA])

        assert counts == {"added": 0, "deleted": 0}
        client._rpki_http.post.assert_not_called()

    def test_api_error_raises(self, client):
        client._rpki_http.get.return_value = err(401)

        with pytest.raises(ApiError, match="fetch current ROAs"):
            client.sync_roas([IPV4_ROA])

    def test_dry_run_does_not_call_api(self, client):
        client._dry_run = True

        counts = client.sync_roas([IPV4_ROA])

        client._rpki_http.get.assert_not_called()
        client._rpki_http.post.assert_not_called()
        assert counts["added"] == 1


SSO_EMAILS = ["admin@example.com"]


class TestSetupTestEnv:
    def test_dry_run_does_not_call_api(self, client, capsys):
        client._dry_run = True

        client.setup_test_env([IPV4_ROUTE], SSO_EMAILS)

        client._http.get.assert_not_called()
        client._http.post.assert_not_called()
        out = capsys.readouterr().out
        assert "mntner" in out
        assert "aut-num" in out

    def test_syncs_mntner_and_autnums(self, client):
        mock_mntner = {
            "objects": {
                "object": [
                    {
                        "attributes": {
                            "attribute": [
                                {"name": "admin-c", "value": "AB1-RIPE"},
                            ]
                        }
                    }
                ]
            }
        }
        mock_autnum = {"objects": {"object": [{"attributes": {"attribute": []}}]}}

        # mntner exists (200), all other objects don't yet (404)
        def mock_get(url):
            if "/mntner/" in url:
                return MagicMock(status_code=200, is_error=False)
            return MagicMock(status_code=404, is_error=False)

        client._http.get.side_effect = mock_get
        client._http.post.return_value = ok()
        client._http.put.return_value = ok()

        def fake_fetch(obj_type, key):
            if obj_type == "mntner":
                return mock_mntner
            if obj_type == "aut-num":
                return mock_autnum
            return None

        with patch(
            "rir_updater.ripe.client.RipeClient._fetch_prod_object",
            side_effect=fake_fetch,
        ):
            client.setup_test_env([IPV4_ROUTE], SSO_EMAILS)

        # mntner PUT + aut-num POST = at least 2 API calls
        assert client._http.put.call_count + client._http.post.call_count >= 2

    def test_mntner_body_contains_sso_auth(self, client):
        refs = {"admin-c": ["AB1-RIPE"]}
        body = client._mntner_body(refs, ["a@example.com", "b@example.com"])
        attrs = {
            a["name"]: a["value"]
            for a in body["objects"]["object"][0]["attributes"]["attribute"]
            if a["name"] != "auth"
        }
        auth_vals = [
            a["value"]
            for a in body["objects"]["object"][0]["attributes"]["attribute"]
            if a["name"] == "auth"
        ]
        assert attrs["mntner"] == "MAINT-AS64496"
        assert "SSO a@example.com" in auth_vals
        assert "SSO b@example.com" in auth_vals
