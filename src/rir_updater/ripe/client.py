import sys

import httpx

from rir_updater.config import ROA, RouteObject
from rir_updater.exceptions import ApiError, RirUpdaterError

PROD_URL = "https://rest.db.ripe.net"
TEST_URL = "https://rest-test.db.ripe.net"

RPKI_PROD_URL = "https://my.ripe.net/api/rpki"
RPKI_TEST_URL = "https://localcert.ripe.net/api/rpki"


def _extract_ripe_errors(resp: httpx.Response) -> str:
    """Extract human-readable messages from a RIPE error response."""
    try:
        data = resp.json()
        messages = data.get("errormessages", {}).get("errormessage", [])
        if messages:
            return "; ".join(m.get("text", "") for m in messages)
        # Fall back to full JSON if no errormessages structure found
        return str(data)
    except Exception:
        pass
    content_type = resp.headers.get("content-type", "unknown")
    return resp.text or f"(empty body, content-type: {content_type})"


def _raise_for_status(resp: httpx.Response, context: str) -> None:
    if resp.is_error:
        detail = _extract_ripe_errors(resp)
        raise ApiError(f"{context} failed ({resp.status_code}): {detail}")


class RipeClient:
    def __init__(
        self,
        db_auth: str,
        rpki_key: str,
        maintainer: str,
        dry_run: bool = False,
        use_test_env: bool = True,
    ):
        self._maintainer = maintainer
        self._dry_run = dry_run
        self._base_url = TEST_URL if use_test_env else PROD_URL
        self._rpki_url = RPKI_TEST_URL if use_test_env else RPKI_PROD_URL
        self._source = "TEST" if use_test_env else "RIPE"
        self._http = httpx.Client(
            headers={
                "Authorization": f"Basic {db_auth}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        self._rpki_http = httpx.Client(
            headers={
                "ncc-api-authorization": rpki_key,
                "Accept": "application/json",
            },
            timeout=30,
        )

    def close(self):
        self._http.close()
        self._rpki_http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # --- Test environment setup ---

    def _fetch_prod_object(self, obj_type: str, key: str) -> dict | None:
        """Fetch an object from production with auth to get unfiltered output."""
        url = f"{PROD_URL}/ripe/{obj_type}/{key}"
        try:
            # Use authenticated client headers but target production URL directly.
            # unfiltered=true is required to get upd-to and other auth-visible fields.
            resp = httpx.get(
                url,
                headers={
                    "Authorization": self._http.headers["Authorization"],
                    "Accept": "application/json",
                },
                params={"unfiltered": "true"},
                timeout=30,
                follow_redirects=True,
            )
        except httpx.RequestError as e:
            raise ApiError(f"Network error fetching {obj_type} {key!r}: {e}") from e
        if resp.status_code == 404:
            return None
        _raise_for_status(resp, f"fetch {obj_type} {key!r} from production")
        return resp.json()

    def _clean_body(self, body: dict) -> dict:
        """Strip response-only metadata and rewrite source for submission."""
        for obj in body.get("objects", {}).get("object", []):
            cleaned = []
            for attr in obj.get("attributes", {}).get("attribute", []):
                entry = {"name": attr["name"], "value": attr["value"]}
                if attr["name"] == "source":
                    entry["value"] = self._source
                cleaned.append(entry)
            obj["attributes"]["attribute"] = cleaned
            # Remove response-only top-level keys
            for key in ("link", "primary-key"):
                obj.pop(key, None)
        return body

    def _object_exists_in_test(self, obj_type: str, key: str) -> bool:
        url = f"{self._base_url}/{self._source.lower()}/{obj_type}/{key}"
        return self._http.get(url).status_code == 200

    def _replicate_object(self, obj_type: str, key: str) -> str:
        """Copy an object from production into the test database."""
        body = self._fetch_prod_object(obj_type, key)
        if body is None:
            return "not-found"
        body = self._clean_body(body)
        exists = self._object_exists_in_test(obj_type, key)
        url = f"{self._base_url}/{self._source.lower()}/{obj_type}"
        if exists:
            resp = self._http.put(f"{url}/{key}", json=body)
            _raise_for_status(resp, f"update {obj_type} {key!r} in test")
            return "updated"
        else:
            resp = self._http.post(url, json=body)
            _raise_for_status(resp, f"create {obj_type} {key!r} in test")
            return "created"

    _MNTNER_COPY_ATTRS = ("admin-c", "tech-c", "upd-to", "mnt-notify")

    def _get_prod_mntner_references(self) -> dict[str, list[str]]:
        """Return copyable attrs from the production mntner."""
        body = self._fetch_prod_object("mntner", self._maintainer)
        if body is None:
            return {}
        refs: dict[str, list[str]] = {}
        for obj in body.get("objects", {}).get("object", []):
            for attr in obj.get("attributes", {}).get("attribute", []):
                name = attr.get("name")
                if name in self._MNTNER_COPY_ATTRS:
                    refs.setdefault(name, [])
                    val = attr.get("value", "")
                    if val not in refs[name]:
                        refs[name].append(val)
        return refs

    def _mntner_body(self, refs: dict[str, list[str]], sso_emails: list[str]) -> dict:
        attrs = [{"name": "mntner", "value": self._maintainer}]
        for key in self._MNTNER_COPY_ATTRS:
            for val in refs.get(key, []):
                attrs.append({"name": key, "value": val})
        for email in sso_emails:
            attrs.append({"name": "auth", "value": f"SSO {email}"})
        attrs.append({"name": "mnt-by", "value": self._maintainer})
        attrs.append({"name": "source", "value": self._source})
        return {
            "objects": {
                "object": [{"type": "mntner", "attributes": {"attribute": attrs}}]
            }
        }

    def _put_mntner(self, refs: dict[str, list[str]], sso_emails: list[str]) -> str:
        url = f"{self._base_url}/{self._source.lower()}/mntner"
        body = self._mntner_body(refs, sso_emails)
        exists = self._object_exists_in_test("mntner", self._maintainer)
        if exists:
            resp = self._http.put(f"{url}/{self._maintainer}", json=body)
            _raise_for_status(resp, f"update mntner {self._maintainer!r} in test")
            return "updated"
        else:
            resp = self._http.post(url, json=body)
            _raise_for_status(resp, f"create mntner {self._maintainer!r} in test")
            return "created"

    def _sync_mntner(self, sso_emails: list[str]) -> str:
        """Update the mntner and replicate referenced person/role objects.

        Assumes the mntner already exists (caller checks with _object_exists_in_test).
        Replicates admin-c/tech-c persons first, then updates the mntner with full refs.
        """
        refs = self._get_prod_mntner_references()

        # Replicate person/role objects — works because the mntner already exists,
        # so RIPE can verify that our API key is authorised to create objects mnt-by it.
        for obj_type in ("admin-c", "tech-c"):
            for key in refs.get(obj_type, []):
                r = self._replicate_object("person", key)
                if r == "not-found":
                    r = self._replicate_object("role", key)
                if r != "not-found":
                    print(f"{r}: person/role {key}")

        return self._put_mntner(refs, sso_emails)

    _BOOTSTRAP_INSTRUCTIONS = """\
The RIPE test database API does not allow creating a new maintainer via the API
because of the circular person↔mntner dependency (each requires the other to exist).

One-time manual bootstrap required:
  1. Log in to https://apps-test.db.ripe.net with your RIPE NCC Access account.
  2. Go to: Database → Create Object → mntner
     (or navigate to the "Maintainer" form for source TEST)
  3. Fill in the mntner with these values:
       mntner:   {maintainer}
       admin-c:  AA1-TEST   (a pre-seeded placeholder; update later)
       upd-to:   {upd_to}
       auth:     SSO {auth_email}
       mnt-by:   {maintainer}
       source:   TEST
  4. Submit using your SSO session.
  5. Re-run this command — setup will complete automatically."""

    def setup_test_env(self, routes: list[RouteObject], sso_emails: list[str]) -> None:
        """Set up the test database with all objects needed to run against it.

        - Replicates referenced person/role objects from production.
        - Updates the mntner with current SSO auth entries.
        - Replicates aut-num objects where available (warns for ARIN/APNIC ASNs).

        The mntner must already exist in the test database. If it does not, prints
        one-time manual bootstrap instructions and raises RirUpdaterError.
        """
        asns = {r.origin.upper() for r in routes}

        if self._dry_run:
            print(f"[dry-run] would sync mntner {self._maintainer}")
            for asn in sorted(asns):
                print(f"[dry-run] would replicate aut-num {asn} from production")
            return

        if not self._object_exists_in_test("mntner", self._maintainer):
            email = sso_emails[0] if sso_emails else "your@email.com"
            print(
                self._BOOTSTRAP_INSTRUCTIONS.format(
                    maintainer=self._maintainer,
                    upd_to=email,
                    auth_email=email,
                ),
                file=sys.stderr,
            )
            raise RirUpdaterError(
                f"mntner '{self._maintainer}' not found in test DB — "
                "see bootstrap instructions above"
            )

        result = self._sync_mntner(sso_emails)
        print(f"{result}: mntner {self._maintainer}")

        for asn in sorted(asns):
            result = self._replicate_object("aut-num", asn)
            if result == "not-found":
                print(
                    f"warning: aut-num {asn} not found in RIPE production "
                    f"(may be an ARIN/APNIC ASN) — create it manually in test"
                )
            else:
                print(f"{result}: aut-num {asn}")

    # --- Route objects ---

    def _route_object_type(self, prefix: str) -> str:
        return "route6" if ":" in prefix else "route"

    def _route_key(self, route: RouteObject) -> str:
        # RIPE primary key: prefix+ASN with no separator, e.g. "192.0.2.0/24AS64496"
        return f"{route.prefix}{route.origin.upper()}"

    def _route_url(self, route: RouteObject, key: str | None = None) -> str:
        obj_type = self._route_object_type(route.prefix)
        base = f"{self._base_url}/{self._source.lower()}/{obj_type}"
        if key:
            return f"{base}/{key}"
        return base

    def _route_body(self, route: RouteObject) -> dict:
        obj_type = self._route_object_type(route.prefix)
        attrs = [
            {"name": obj_type, "value": route.prefix},
            {"name": "origin", "value": route.origin.upper()},
            {"name": "mnt-by", "value": self._maintainer},
            {"name": "source", "value": self._source},
        ]
        if route.description:
            attrs.insert(1, {"name": "descr", "value": route.description})
        return {
            "objects": {
                "object": [
                    {
                        "type": obj_type,
                        "attributes": {"attribute": attrs},
                    }
                ]
            }
        }

    def _route_exists(self, route: RouteObject) -> bool:
        key = self._route_key(route)
        resp = self._http.get(self._route_url(route, key))
        return resp.status_code == 200

    def sync_route(self, route: RouteObject) -> str:
        """Sync a route object. Returns 'created', 'updated', or 'dry-run'."""
        key = self._route_key(route)
        exists = self._route_exists(route)

        if self._dry_run:
            action = "update" if exists else "create"
            obj_type = self._route_object_type(route.prefix)
            print(f"[dry-run] would {action} {obj_type} {key}")
            return "dry-run"

        body = self._route_body(route)
        if exists:
            resp = self._http.put(self._route_url(route, key), json=body)
            _raise_for_status(resp, f"update route {key!r}")
            return "updated"
        else:
            resp = self._http.post(self._route_url(route), json=body)
            _raise_for_status(resp, f"create route {key!r}")
            return "created"

    # --- ROAs ---

    def _roa_key(self, roa: ROA) -> tuple[str, str, int]:
        prefix_len = int(roa.prefix.split("/")[1])
        max_length = roa.max_length if roa.max_length is not None else prefix_len
        return (roa.prefix, roa.origin.upper(), max_length)

    def _get_current_roas(self) -> set[tuple[str, str, int]]:
        try:
            resp = self._rpki_http.get(f"{self._rpki_url}/roas")
        except httpx.RequestError as e:
            raise ApiError(f"Network error fetching current ROAs: {e}") from e
        _raise_for_status(resp, "fetch current ROAs")
        return {(r["prefix"], r["asn"], r["maximalLength"]) for r in resp.json()}

    def sync_roas(self, roas: list[ROA]) -> dict[str, int]:
        """Diff desired ROAs against current state and publish changes.

        Only ROAs whose prefix appears in the config are managed. ROAs for
        other prefixes in the account are left untouched.
        """
        desired = {self._roa_key(r) for r in roas}
        managed_prefixes = {r.prefix for r in roas}
        current = set() if self._dry_run else self._get_current_roas()
        current_managed = {r for r in current if r[0] in managed_prefixes}
        to_add = desired - current_managed
        to_delete = current_managed - desired

        if self._dry_run:
            for prefix, asn, max_len in desired:
                print(f"[dry-run] would sync ROA {prefix} {asn} max={max_len}")
            return {"added": len(desired), "deleted": 0}

        if not to_add and not to_delete:
            return {"added": 0, "deleted": 0}

        payload = {
            "added": [
                {"asn": asn, "prefix": prefix, "maximalLength": max_len}
                for prefix, asn, max_len in to_add
            ],
            "deleted": [
                {"asn": asn, "prefix": prefix, "maximalLength": max_len}
                for prefix, asn, max_len in to_delete
            ],
        }
        try:
            resp = self._rpki_http.post(f"{self._rpki_url}/roas/publish", json=payload)
        except httpx.RequestError as e:
            raise ApiError(f"Network error publishing ROAs: {e}") from e
        _raise_for_status(resp, "publish ROAs")
        return {"added": len(to_add), "deleted": len(to_delete)}
