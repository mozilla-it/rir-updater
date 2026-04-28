from datetime import date

import httpx

from rir_updater.config import RouteObject
from rir_updater.exceptions import ApiError

BASE_URL = "https://api.radb.net/api"


def _raise_for_status(resp: httpx.Response, context: str) -> None:
    if resp.is_error:
        try:
            detail = resp.json().get("errors", [{}])[0].get("message", resp.text)
        except Exception:
            content_type = resp.headers.get("content-type", "unknown")
            detail = resp.text or f"(empty body, content-type: {content_type})"
        raise ApiError(f"{context} failed ({resp.status_code}): {detail}")


class RadbClient:
    def __init__(
        self,
        maintainer: str,
        mntner_password: str,
        contact_email: str,
        dry_run: bool = False,
    ):
        self._maintainer = maintainer
        self._mntner_password = mntner_password
        self._contact_email = contact_email
        self._dry_run = dry_run
        self._http = httpx.Client(
            headers={"Accept": "application/json"},
            timeout=30,
        )

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _object_type(self, prefix: str) -> str:
        return "route6" if ":" in prefix else "route"

    def _route_key_url(self, route: RouteObject) -> str:
        obj_type = self._object_type(route.prefix)
        network, prefix_len = route.prefix.split("/")
        asn = route.origin.upper()
        return f"{BASE_URL}/radb/{obj_type}/{network}/{prefix_len}/{asn}"

    def _route_base_url(self, route: RouteObject) -> str:
        obj_type = self._object_type(route.prefix)
        return f"{BASE_URL}/radb/{obj_type}"

    def _route_body(self, route: RouteObject) -> dict:
        obj_type = self._object_type(route.prefix)
        changed = f"{self._contact_email} {date.today().strftime('%Y%m%d')}"
        attrs = [
            {"name": obj_type, "value": route.prefix},
            {"name": "origin", "value": route.origin.upper()},
            {"name": "mnt-by", "value": self._maintainer},
            {"name": "changed", "value": changed},
            {"name": "source", "value": "RADB"},
        ]
        if route.description:
            attrs.insert(1, {"name": "descr", "value": route.description})
        return {
            "objects": {
                "object": [{"type": obj_type, "attributes": {"attribute": attrs}}]
            }
        }

    def _route_exists(self, route: RouteObject) -> bool:
        resp = self._http.get(self._route_key_url(route))
        return resp.status_code == 200

    def sync_route(self, route: RouteObject) -> str:
        """Sync a route object. Returns 'created', 'updated', or 'dry-run'."""
        exists = self._route_exists(route)
        asn = route.origin.upper()
        obj_type = self._object_type(route.prefix)

        if self._dry_run:
            action = "update" if exists else "create"
            print(f"[dry-run] would {action} radb {obj_type} {route.prefix} {asn}")
            return "dry-run"

        body = self._route_body(route)
        params = {"password": self._mntner_password}
        if exists:
            resp = self._http.put(self._route_key_url(route), json=body, params=params)
            _raise_for_status(resp, f"update radb route {route.prefix} {asn}")
            return "updated"
        else:
            resp = self._http.post(
                self._route_base_url(route), json=body, params=params
            )
            _raise_for_status(resp, f"create radb route {route.prefix} {asn}")
            return "created"
