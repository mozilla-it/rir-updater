from collections import defaultdict

# Maps action → diff prefix character for {code:diff} rendering in Jira.
# +  renders green (added/created), -  renders red (removed/deleted),
# space renders as context (updated — already existed, content refreshed).
_DIFF_CHAR = {
    "created": "+",
    "updated": " ",
    "deleted": "-",
    "not-found": " ",
    "dry-run-create": "+",
    "dry-run-update": " ",
    "dry-run-delete": "-",
}


class Summary:
    def __init__(self, dry_run: bool = False):
        self._dry_run = dry_run
        self._order: list[str] = []
        self._routes: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        self._roas: dict[str, dict[str, int]] = {}
        # registry -> list of (label, detail) for objects that failed to sync
        self._errors: dict[str, list[tuple[str, str]]] = defaultdict(list)

    def start_registry(self, registry: str) -> None:
        if registry not in self._order:
            self._order.append(registry)

    def record_route(
        self, registry: str, action: str, prefix: str, origin: str
    ) -> None:
        self.start_registry(registry)
        self._routes[registry].append((action, prefix, origin))

    def record_roas(self, registry: str, added: int, deleted: int) -> None:
        self.start_registry(registry)
        if registry not in self._roas:
            self._roas[registry] = {"added": 0, "deleted": 0}
        self._roas[registry]["added"] += added
        self._roas[registry]["deleted"] += deleted

    def record_error(self, registry: str, label: str, detail: str) -> None:
        """Record an object that failed to sync so the run can continue.

        `label` identifies the object (e.g. a prefix or "ROAs"); `detail` is the
        error message. Failures are rendered in a trailing block and make
        has_errors() true so the CLI can exit non-zero.
        """
        self.start_registry(registry)
        self._errors[registry].append((label, detail))

    def has_errors(self) -> bool:
        return any(self._errors.values())

    def print_jira(self) -> None:
        header = "*Registry Update Summary*"
        if self._dry_run:
            header += " _(dry-run)_"
        print(header)
        print()
        print("{code:diff}")

        first = True
        for registry in self._order:
            if not first:
                print()
            first = False

            print(f"# {registry}")

            route_entries = self._routes.get(registry, [])
            roa_counts = self._roas.get(registry)
            any_output = False

            short = registry.split()[0].lower()  # "RIPE (test)" -> "ripe"

            for action, prefix, origin in route_entries:
                diff_char = _DIFF_CHAR.get(action, " ")
                obj_type = "route6" if ":" in prefix else "route"
                print(f"{diff_char} {short} {obj_type} {prefix} {origin.upper()}")
                any_output = True

            if roa_counts:
                added = roa_counts["added"]
                deleted = roa_counts["deleted"]
                if added:
                    print(f"+ {short} ROAs: {added} added")
                    any_output = True
                if deleted:
                    print(f"- {short} ROAs: {deleted} deleted")
                    any_output = True

            for label, detail in self._errors.get(registry, []):
                print(f"! {short} {label} FAILED: {detail}")
                any_output = True

            if not any_output:
                print("  (no changes)")

        print("{code}")
