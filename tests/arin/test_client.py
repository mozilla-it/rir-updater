import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

import pytest

from rir_updater.arin.client import CORE_NS, OTE_BASE, PROD_BASE, ArinClient
from rir_updater.config import ROA, RouteObject
from rir_updater.exceptions import ApiError

IPV4_ROUTE = RouteObject(prefix="192.0.2.0/24", origin="AS64496")
IPV6_ROUTE = RouteObject(prefix="2001:db8::/32", origin="AS64496")


def ok(status_code: int = 200, text: str = "<route/>") -> MagicMock:
    return MagicMock(status_code=status_code, is_error=False, text=text)


def err(status_code: int, message: str = "error") -> MagicMock:
    xml = f"<errorPayload><message>{message}</message></errorPayload>"
    return MagicMock(status_code=status_code, is_error=True, text=xml)


@pytest.fixture
def client():
    with patch("rir_updater.arin.client.httpx.Client"):
        c = ArinClient(
            org_handle="TESTORG-1",
            api_key="test-api-key",
            use_test_env=True,
        )
        c._http = MagicMock()
        yield c


@pytest.fixture
def prod_client():
    with patch("rir_updater.arin.client.httpx.Client"):
        c = ArinClient(
            org_handle="TESTORG-1",
            api_key="test-api-key",
            use_test_env=False,
        )
        c._http = MagicMock()
        yield c


class TestUrls:
    def test_uses_ote_base_by_default(self, client):
        assert client._base == OTE_BASE

    def test_uses_prod_base_when_specified(self, prod_client):
        assert prod_client._base == PROD_BASE

    def test_route_url_ipv4(self, client):
        url = client._route_url(IPV4_ROUTE)
        assert url == f"{OTE_BASE}/irr/route/192.0.2.0/24/AS64496"

    def test_route_url_ipv6(self, client):
        url = client._route_url(IPV6_ROUTE)
        assert url == f"{OTE_BASE}/irr/route/2001:db8::/32/AS64496"


class TestRouteBody:
    def _parse_attrs(self, route: RouteObject, client: ArinClient) -> dict:
        xml_str = client._route_body(route)
        root = ET.fromstring(xml_str)
        return {el.tag.split("}")[-1]: el.text for el in root}

    def test_required_fields(self, client):
        attrs = self._parse_attrs(IPV4_ROUTE, client)
        assert attrs["orgHandle"] == "TESTORG-1"
        assert attrs["originAS"] == "AS64496"
        assert attrs["prefix"] == "192.0.2.0/24"
        assert attrs["source"] == "ARIN"

    def test_description_included_when_present(self, client):
        route = RouteObject(prefix="192.0.2.0/24", origin="AS64496", description="Test")
        attrs = self._parse_attrs(route, client)
        assert attrs["description"] == "Test"

    def test_description_omitted_when_absent(self, client):
        attrs = self._parse_attrs(IPV4_ROUTE, client)
        assert "description" not in attrs

    def test_uses_core_namespace(self, client):
        xml_str = client._route_body(IPV4_ROUTE)
        root = ET.fromstring(xml_str)
        assert root.tag == f"{{{CORE_NS}}}route"

    def test_asn_uppercased(self, client):
        route = RouteObject(prefix="192.0.2.0/24", origin="as64496")
        attrs = self._parse_attrs(route, client)
        assert attrs["originAS"] == "AS64496"


class TestSyncRoute:
    def test_creates_when_not_exists(self, client):
        client._http.get.return_value = MagicMock(status_code=404, is_error=False)
        client._http.post.return_value = ok()

        result = client.sync_route(IPV4_ROUTE)

        assert result == "created"
        client._http.post.assert_called_once()
        call_kwargs = client._http.post.call_args
        assert call_kwargs.kwargs["params"] == {"apikey": "test-api-key"}

    def test_updates_when_exists(self, client):
        client._http.get.return_value = ok()
        client._http.put.return_value = ok()

        result = client.sync_route(IPV4_ROUTE)

        assert result == "updated"
        client._http.put.assert_called_once()

    def test_post_uses_key_url(self, client):
        client._http.get.return_value = MagicMock(status_code=404, is_error=False)
        client._http.post.return_value = ok()

        client.sync_route(IPV4_ROUTE)

        url = client._http.post.call_args.args[0]
        assert url == f"{OTE_BASE}/irr/route/192.0.2.0/24/AS64496"

    def test_api_error_raises(self, client):
        client._http.get.return_value = MagicMock(status_code=404, is_error=False)
        client._http.post.return_value = err(403, "Unauthorized")

        with pytest.raises(ApiError, match="Unauthorized"):
            client.sync_route(IPV4_ROUTE)

    def test_dry_run_does_not_call_post_or_put(self, client):
        client._dry_run = True
        client._http.get.return_value = MagicMock(status_code=404, is_error=False)

        result = client.sync_route(IPV4_ROUTE)

        assert result == "dry-run"
        client._http.post.assert_not_called()
        client._http.put.assert_not_called()

    def test_dry_run_shows_update_when_exists(self, client, capsys):
        client._dry_run = True
        client._http.get.return_value = ok()

        client.sync_route(IPV4_ROUTE)

        assert "update" in capsys.readouterr().out


class TestSyncRoas:
    def _make_roa_xml(self, prefix: str, asn: str, max_len: int, handle: str) -> str:
        network, cidr = prefix.split("/")
        return f"""<roaSpecList xmlns="{CORE_NS}">
  <roaSpec>
    <roaHandle>{handle}</roaHandle>
    <asNumber>{asn.removeprefix("AS")}</asNumber>
    <resources>
      <startAddress>{network}</startAddress>
      <cidrLength>{cidr}</cidrLength>
      <maxLength>{max_len}</maxLength>
    </resources>
  </roaSpec>
</roaSpecList>"""

    def test_adds_missing_roas(self, client):
        client._http.get.return_value = ok(text="<roaSpecList/>")
        client._http.post.return_value = ok()
        roas = [ROA(prefix="192.0.2.0/24", origin="AS64496", max_length=24)]

        result = client.sync_roas(roas)

        assert result["added"] == 1
        assert result["deleted"] == 0

    def test_deletes_unwanted_roas(self, client):
        xml = self._make_roa_xml("192.0.2.0/24", "AS64496", 24, "handle-abc")
        client._http.get.return_value = ok(text=xml)
        client._http.post.return_value = ok()

        result = client.sync_roas([])

        assert result["deleted"] == 0  # prefix not in managed set — untouched

    def test_scoped_to_managed_prefixes(self, client):
        # A ROA for an unmanaged prefix must not be deleted.
        xml = self._make_roa_xml("10.0.0.0/8", "AS64496", 8, "handle-xyz")
        client._http.get.return_value = ok(text=xml)
        client._http.post.return_value = ok()
        roas = [ROA(prefix="192.0.2.0/24", origin="AS64496", max_length=24)]

        result = client.sync_roas(roas)

        assert result["added"] == 1
        assert result["deleted"] == 0

    def test_no_op_when_already_in_sync(self, client):
        xml = self._make_roa_xml("192.0.2.0/24", "AS64496", 24, "handle-abc")
        client._http.get.return_value = ok(text=xml)
        roas = [ROA(prefix="192.0.2.0/24", origin="AS64496", max_length=24)]

        result = client.sync_roas(roas)

        assert result == {"added": 0, "deleted": 0}
        client._http.post.assert_not_called()

    def test_dry_run_does_not_call_post(self, client):
        client._dry_run = True
        roas = [ROA(prefix="192.0.2.0/24", origin="AS64496", max_length=24)]

        result = client.sync_roas(roas)

        assert result["added"] == 1
        client._http.get.assert_not_called()
        client._http.post.assert_not_called()
