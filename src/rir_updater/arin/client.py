import ipaddress
import xml.etree.ElementTree as ET
from xml.etree.ElementTree import Element, SubElement

import httpx

from rir_updater.config import ROA, RouteObject
from rir_updater.exceptions import ApiError

PROD_BASE = "https://reg.arin.net/rest"
OTE_BASE = "https://reg.ote.arin.net/rest"

# XML namespace for IRR route objects
CORE_NS = "http://www.arin.net/regrws/core/v1"
# XML namespace for RPKI ROA objects
RPKI_NS = "http://www.arin.net/regrws/rpki/v1"

ET.register_namespace("", CORE_NS)


def _find_text(element: ET.Element, local_name: str) -> str | None:
    """Find a child element by local name, ignoring namespace prefixes."""
    for child in element:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag == local_name:
            return child.text
    return None


def _raise_for_status(resp: httpx.Response, context: str) -> None:
    if resp.is_error:
        try:
            root = ET.fromstring(resp.text)
            detail = _find_text(root, "message") or resp.text[:200]
        except ET.ParseError:
            detail = resp.text[:200] or "(empty body)"
        raise ApiError(f"{context} failed ({resp.status_code}): {detail}")


class ArinClient:
    """Client for the ARIN IRR REST API and RPKI Management API.

    All requests are authenticated with an API key passed as a `?apikey=`
    query parameter. Request and response bodies use XML.
    """

    def __init__(
        self,
        org_handle: str,
        api_key: str,
        dry_run: bool = False,
        use_test_env: bool = True,
    ):
        self._org_handle = org_handle
        self._api_key = api_key
        self._dry_run = dry_run
        self._base = OTE_BASE if use_test_env else PROD_BASE
        self._http = httpx.Client(
            headers={
                "Accept": "application/xml",
                "Content-Type": "application/xml",
            },
            timeout=30,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _params(self) -> dict:
        return {"apikey": self._api_key}

    # --- Route objects ---

    def _route_url(self, route: RouteObject) -> str:
        # ARIN uses /irr/route/ for both IPv4 and IPv6; the prefix distinguishes them.
        # Key URL segments: network/prefix_len/asn (same pattern as RADb).
        network, prefix_len = route.prefix.split("/")
        asn = route.origin.upper()
        return f"{self._base}/irr/route/{network}/{prefix_len}/{asn}"

    def _route_body(self, route: RouteObject) -> str:
        root = Element(f"{{{CORE_NS}}}route")
        SubElement(root, f"{{{CORE_NS}}}orgHandle").text = self._org_handle
        SubElement(root, f"{{{CORE_NS}}}originAS").text = route.origin.upper()
        SubElement(root, f"{{{CORE_NS}}}prefix").text = route.prefix
        # description is required by schema; content uses <line number="N"> children
        desc_el = SubElement(root, f"{{{CORE_NS}}}description")
        for i, text in enumerate((route.description or "").splitlines()):
            line_el = SubElement(desc_el, f"{{{CORE_NS}}}line")
            line_el.set("number", str(i))
            line_el.text = text
        SubElement(root, f"{{{CORE_NS}}}source").text = "ARIN"
        return ET.tostring(root, encoding="unicode")

    def list_routes(self) -> list[RouteObject]:
        """Return all route objects for the org from the current environment."""
        resp = self._http.get(
            f"{self._base}/org/{self._org_handle}/routes", params=self._params()
        )
        if resp.status_code == 404:
            return []
        _raise_for_status(resp, "list ARIN routes")
        routes = []
        root = ET.fromstring(resp.text)
        for ref in root.iter():
            if ref.tag.split("}")[-1] != "routeRef":
                continue
            prefix = _find_text(ref, "prefix")
            asn = _find_text(ref, "originAS")
            if prefix and asn:
                routes.append(RouteObject(prefix=prefix, origin=asn))
        return routes

    def list_roas(self) -> list[ROA]:
        """Return all ROAs for the org from the current environment."""
        current = self._get_current_roas()
        result = []
        for (prefix, asn, max_len), _ in current.items():
            prefix_len = int(prefix.split("/")[1])
            result.append(
                ROA(
                    prefix=prefix,
                    origin=asn,
                    max_length=max_len if max_len != prefix_len else None,
                )
            )
        return result

    def _route_exists(self, route: RouteObject) -> bool:
        resp = self._http.get(self._route_url(route), params=self._params())
        return resp.status_code == 200

    def delete_route(self, route: RouteObject) -> str:
        """Delete a route object. Returns 'deleted', 'not-found', or 'dry-run'."""
        obj_type = "route6" if ":" in route.prefix else "route"
        asn = route.origin.upper()
        if self._dry_run:
            print(f"[dry-run] would delete arin {obj_type} {route.prefix} {asn}")
            return "dry-run"
        url = self._route_url(route)
        resp = self._http.delete(url, params=self._params())
        if resp.status_code == 404:
            return "not-found"
        _raise_for_status(resp, f"delete arin route {route.prefix} {asn}")
        return "deleted"

    def sync_route(self, route: RouteObject) -> str:
        """Sync a route object. Returns 'created', 'updated', or 'dry-run'."""
        exists = self._route_exists(route)
        obj_type = "route6" if ":" in route.prefix else "route"
        asn = route.origin.upper()

        if self._dry_run:
            action = "update" if exists else "create"
            print(f"[dry-run] would {action} arin {obj_type} {route.prefix} {asn}")
            return "dry-run"

        body = self._route_body(route)
        url = self._route_url(route)
        if exists:
            resp = self._http.put(url, params=self._params(), content=body)
            _raise_for_status(resp, f"update arin route {route.prefix} {asn}")
            return "updated"
        else:
            # Unlike RIPE, ARIN POST uses the key URL (not the collection URL).
            resp = self._http.post(url, params=self._params(), content=body)
            _raise_for_status(resp, f"create arin route {route.prefix} {asn}")
            return "created"

    # --- ROAs ---

    def _roa_key(self, roa: ROA) -> tuple[str, str, int]:
        prefix_len = int(roa.prefix.split("/")[1])
        max_length = roa.max_length if roa.max_length is not None else prefix_len
        return (roa.prefix, roa.origin.upper(), max_length)

    def _get_current_roas(self) -> dict[tuple[str, str, int], str]:
        """Return a map of (prefix, asn, max_length) -> roaHandle for current ROAs."""
        resp = self._http.get(
            f"{self._base}/roa/{self._org_handle}", params=self._params()
        )
        if resp.status_code == 404:
            return {}
        _raise_for_status(resp, "fetch current ARIN ROAs")
        result = {}
        root = ET.fromstring(resp.text)
        for roa_el in root.iter():
            if roa_el.tag.split("}")[-1] != "roaSpec":
                continue
            handle = _find_text(roa_el, "roaHandle")
            asn_text = _find_text(roa_el, "asNumber")
            if not handle or not asn_text:
                continue
            asn = f"AS{asn_text}"
            for res_el in roa_el.iter():
                if res_el.tag.split("}")[-1] != "resources":
                    continue
                start = _find_text(res_el, "startAddress")
                cidr = _find_text(res_el, "cidrLength")
                max_len_text = _find_text(res_el, "maxLength")
                if not start or not cidr:
                    continue
                # ARIN returns IPv4 octets with leading zeros (e.g. "063.245.208.000");
                # strip them before parsing — Python rejects leading zeros in IPv4.
                if ":" not in start:
                    start = ".".join(str(int(o)) for o in start.split("."))
                # Normalize to canonical CIDR (handles IPv6 expansion, strict=False
                # in case startAddress is a host address rather than network address).
                prefix = str(ipaddress.ip_network(f"{start}/{cidr}", strict=False))
                max_len = int(max_len_text) if max_len_text else int(cidr)
                result[(prefix, asn, max_len)] = handle
        return result

    def _roa_transaction_body(
        self,
        to_add: set[tuple[str, str, int]],
        to_delete_handles: list[str],
    ) -> str:
        ET.register_namespace("", RPKI_NS)
        root = Element(f"{{{RPKI_NS}}}rpkiTransaction")
        for handle in to_delete_handles:
            del_el = SubElement(root, f"{{{RPKI_NS}}}roaSpecDelete")
            handle_el = SubElement(del_el, f"{{{RPKI_NS}}}roaHandle")
            handle_el.set("autoLink", "true")
            handle_el.text = handle
        for prefix, asn, max_len in to_add:
            add_el = SubElement(root, f"{{{RPKI_NS}}}roaSpecAdd")
            spec_el = SubElement(add_el, f"{{{RPKI_NS}}}roaSpec")
            SubElement(spec_el, f"{{{RPKI_NS}}}autoLink").text = "true"
            # ROA name: only a-z A-Z 0-9 _ - space allowed; sanitize prefix chars.
            raw_name = f"{prefix} {asn}"
            name = "".join(c if c.isalnum() or c in "_ -" else "-" for c in raw_name)
            while "--" in name:
                name = name.replace("--", "-")
            SubElement(spec_el, f"{{{RPKI_NS}}}name").text = name.strip("-")
            # ARIN <asNumber> takes the numeric value only, without the "AS" prefix.
            SubElement(spec_el, f"{{{RPKI_NS}}}asNumber").text = asn.removeprefix("AS")
            network, prefix_len = prefix.split("/")
            resources_el = SubElement(spec_el, f"{{{RPKI_NS}}}resources")
            res_el = SubElement(resources_el, f"{{{RPKI_NS}}}roaSpecResource")
            SubElement(res_el, f"{{{RPKI_NS}}}startAddress").text = network
            SubElement(res_el, f"{{{RPKI_NS}}}cidrLength").text = prefix_len
            if max_len != int(prefix_len):
                SubElement(res_el, f"{{{RPKI_NS}}}maxLength").text = str(max_len)
        return ET.tostring(root, encoding="unicode")

    def sync_roas(self, roas: list[ROA]) -> dict[str, int]:
        """Diff desired ROAs against current state and publish changes.

        Only ROAs whose prefix appears in the config are managed. ROAs for
        other prefixes in the account are left untouched.
        """
        # Entries with delete=True are scoped (managed) but excluded from desired,
        # so their existing ROAs get removed by the diff.
        desired = {self._roa_key(r) for r in roas if not r.delete}
        managed_prefixes = {r.prefix for r in roas}

        current = self._get_current_roas()
        current_managed = {k: v for k, v in current.items() if k[0] in managed_prefixes}
        to_add = desired - set(current_managed.keys())
        to_delete_keys = set(current_managed.keys()) - desired
        to_delete_handles = [current_managed[k] for k in to_delete_keys]

        if self._dry_run:
            for prefix, asn, max_len in to_add:
                print(f"[dry-run] would add arin ROA {prefix} {asn} max={max_len}")
            for prefix, asn, max_len in to_delete_keys:
                print(f"[dry-run] would delete arin ROA {prefix} {asn} max={max_len}")
            return {"added": len(to_add), "deleted": len(to_delete_keys)}

        if not to_add and not to_delete_handles:
            return {"added": 0, "deleted": 0}

        body = self._roa_transaction_body(to_add, to_delete_handles)
        resp = self._http.post(
            f"{self._base}/rpki/{self._org_handle}",
            params=self._params(),
            content=body,
        )
        _raise_for_status(resp, "publish ARIN ROAs")
        return {"added": len(to_add), "deleted": len(to_delete_handles)}
