import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

import pytest

from rir_updater.arin.client import CORE_NS, OTE_BASE, PROD_BASE, ArinClient
from rir_updater.config import ROA, RouteObject
from rir_updater.exceptions import ApiError

IPV4_ROUTE = RouteObject(prefix="192.0.2.0/24", origin="AS64496")
IPV6_ROUTE = RouteObject(prefix="2001:db8::/32", origin="AS64496")

_EXISTING_ROUTE_XML = (
    f'<route xmlns="{CORE_NS}">'
    f"<orgHandle>TESTORG-1</orgHandle>"
    f"<originAS>AS64496</originAS>"
    f"<prefix>192.0.2.0/24</prefix>"
    f"<description/>"
    f"<comment><line number=\"0\">Keep this</line></comment>"
    f"<source>ARIN</source>"
    f"</route>"
)


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
    def _parse_root(self, route: RouteObject, client: ArinClient) -> ET.Element:
        return ET.fromstring(client._route_body(route))

    def _find(self, root: ET.Element, local_name: str) -> ET.Element | None:
        return root.find(f"{{{CORE_NS}}}{local_name}")

    def test_required_fields(self, client):
        root = self._parse_root(IPV4_ROUTE, client)
        assert self._find(root, "orgHandle").text == "TESTORG-1"
        assert self._find(root, "originAS").text == "AS64496"
        assert self._find(root, "prefix").text == "192.0.2.0/24"
        assert self._find(root, "source").text == "ARIN"

    def test_description_included_when_present(self, client):
        route = RouteObject(prefix="192.0.2.0/24", origin="AS64496", description="Test")
        root = self._parse_root(route, client)
        desc_el = self._find(root, "description")
        assert desc_el is not None
        lines = desc_el.findall(f"{{{CORE_NS}}}line")
        assert len(lines) == 1
        assert lines[0].text == "Test"
        assert lines[0].get("number") == "0"

    def test_description_empty_when_absent(self, client):
        root = self._parse_root(IPV4_ROUTE, client)
        desc_el = self._find(root, "description")
        assert desc_el is not None
        assert len(desc_el) == 0

    def test_uses_core_namespace(self, client):
        root = self._parse_root(IPV4_ROUTE, client)
        assert root.tag == f"{{{CORE_NS}}}route"

    def test_asn_uppercased(self, client):
        route = RouteObject(prefix="192.0.2.0/24", origin="as64496")
        root = self._parse_root(route, client)
        assert self._find(root, "originAS").text == "AS64496"


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
        client._http.get.return_value = ok(text=_EXISTING_ROUTE_XML)
        client._http.put.return_value = ok()

        result = client.sync_route(IPV4_ROUTE)

        assert result == "updated"
        client._http.put.assert_called_once()

    def test_preserves_unmanaged_attributes_on_update(self, client):
        client._http.get.return_value = ok(text=_EXISTING_ROUTE_XML)
        client._http.put.return_value = ok()

        client.sync_route(IPV4_ROUTE)

        put_xml = client._http.put.call_args.kwargs["content"]
        root = ET.fromstring(put_xml)
        comment_el = root.find(f"{{{CORE_NS}}}comment")
        assert comment_el is not None
        line_el = comment_el.find(f"{{{CORE_NS}}}line")
        assert line_el is not None
        assert line_el.text == "Keep this"

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

        assert result == "dry-run-create"
        client._http.post.assert_not_called()
        client._http.put.assert_not_called()

    def test_dry_run_returns_update_when_exists(self, client):
        client._dry_run = True
        client._http.get.return_value = ok()

        result = client.sync_route(IPV4_ROUTE)

        assert result == "dry-run-update"


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
        client._http.get.return_value.status_code = 404
        roas = [ROA(prefix="192.0.2.0/24", origin="AS64496", max_length=24)]

        result = client.sync_roas(roas)

        assert result["added"] == 1
        client._http.post.assert_not_called()


class TestDeleteRoute:
    def test_deletes_when_exists(self, client):
        client._http.delete.return_value = ok()

        result = client.delete_route(IPV4_ROUTE)

        assert result == "deleted"
        client._http.delete.assert_called_once()
        call_kwargs = client._http.delete.call_args
        assert call_kwargs.kwargs["params"] == {"apikey": "test-api-key"}

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
        client._http.delete.return_value = err(403, "Unauthorized")

        with pytest.raises(ApiError, match="Unauthorized"):
            client.delete_route(IPV4_ROUTE)
