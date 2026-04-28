from unittest.mock import MagicMock, patch

import pytest

from rir_updater.config import RouteObject
from rir_updater.exceptions import ApiError
from rir_updater.radb.client import BASE_URL, RadbClient

IPV4_ROUTE = RouteObject(prefix="192.0.2.0/24", origin="AS64496")
IPV6_ROUTE = RouteObject(prefix="2001:db8::/32", origin="AS64496")

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
                        {"name": "changed", "value": "admin@example.com 20240101"},
                        {"name": "source", "value": "RADB"},
                    ]
                },
            }
        ]
    }
}


def ok(status_code: int = 200, json_data=None) -> MagicMock:
    m = MagicMock(status_code=status_code, is_error=False)
    if json_data is not None:
        m.json.return_value = json_data
    return m


def err(status_code: int, message: str = "error") -> MagicMock:
    m = MagicMock(status_code=status_code, is_error=True)
    m.text = message
    m.json.return_value = {"errors": [{"message": message}]}
    return m


@pytest.fixture
def client():
    with patch("rir_updater.radb.client.httpx.Client"):
        c = RadbClient(
            maintainer="MAINT-AS64496",
            portal_username="user@example.com",
            portal_password="portalpass",
            mntner_password="testpass",
            contact_email="admin@example.com",
        )
        c._http = MagicMock()
        yield c


class TestRouteHelpers:
    def test_object_type_ipv4(self, client):
        assert client._object_type("192.0.2.0/24") == "route"

    def test_object_type_ipv6(self, client):
        assert client._object_type("2001:db8::/32") == "route6"

    def test_route_key_url_ipv4(self, client):
        url = client._route_key_url(IPV4_ROUTE)
        assert url == f"{BASE_URL}/radb/route/192.0.2.0/24/AS64496"

    def test_route_key_url_ipv6(self, client):
        url = client._route_key_url(IPV6_ROUTE)
        assert url == f"{BASE_URL}/radb/route6/2001:db8::/32/AS64496"

    def test_route_body_structure(self, client):
        body = client._route_body(IPV4_ROUTE)
        obj = body["objects"]["object"][0]
        assert obj["type"] == "route"
        attrs = {a["name"]: a["value"] for a in obj["attributes"]["attribute"]}
        assert attrs["route"] == "192.0.2.0/24"
        assert attrs["origin"] == "AS64496"
        assert attrs["mnt-by"] == "MAINT-AS64496"
        assert attrs["source"] == "RADB"
        assert "changed" in attrs

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

    def test_route_body_asn_uppercased(self, client):
        route = RouteObject(prefix="192.0.2.0/24", origin="as64496")
        body = client._route_body(route)
        attrs = {
            a["name"]: a["value"]
            for a in body["objects"]["object"][0]["attributes"]["attribute"]
        }
        assert attrs["origin"] == "AS64496"


class TestSyncRoute:
    def test_creates_when_not_exists(self, client):
        client._http.get.return_value = MagicMock(status_code=404, is_error=False)
        client._http.post.return_value = ok()

        result = client.sync_route(IPV4_ROUTE)

        assert result == "created"
        client._http.post.assert_called_once()
        call_kwargs = client._http.post.call_args
        assert call_kwargs.kwargs["params"] == {"password": "testpass"}

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
                                {
                                    "name": "changed",
                                    "value": "old@example.com 20200101",
                                },  # noqa: E501
                                {"name": "source", "value": "RADB"},
                            ]
                        },
                    }
                ]
            }
        }
        client._http.get.return_value = ok(json_data=existing)
        client._http.put.return_value = ok()

        client.sync_route(IPV4_ROUTE)

        put_body = client._http.put.call_args.kwargs["json"]
        attrs = {
            a["name"]: a["value"]
            for a in put_body["objects"]["object"][0]["attributes"]["attribute"]
        }
        assert attrs["remarks"] == "Keep this"
        assert attrs["source"] == "RADB"

    def test_api_error_raises(self, client):
        client._http.get.return_value = MagicMock(status_code=404, is_error=False)
        client._http.post.return_value = err(403, "Authorization failed")

        with pytest.raises(ApiError, match="Authorization failed"):
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
        client._http.get.return_value = ok()

        result = client.sync_route(IPV4_ROUTE)

        assert result == "dry-run-update"


class TestDeleteRoute:
    def test_deletes_when_exists(self, client):
        client._http.delete.return_value = ok()

        result = client.delete_route(IPV4_ROUTE)

        assert result == "deleted"
        client._http.delete.assert_called_once()
        call_kwargs = client._http.delete.call_args
        assert call_kwargs.kwargs["params"] == {"password": "testpass"}

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
        client._http.delete.return_value = err(403, "Authorization failed")

        with pytest.raises(ApiError, match="Authorization failed"):
            client.delete_route(IPV4_ROUTE)
