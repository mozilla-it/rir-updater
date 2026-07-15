import io
from contextlib import redirect_stdout

from rir_updater.summary import Summary


def render(summary: Summary) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        summary.print_jira()
    return buf.getvalue()


class TestErrors:
    def test_no_errors_by_default(self):
        s = Summary()
        assert s.has_errors() is False

    def test_record_error_sets_has_errors(self):
        s = Summary()
        s.record_error("RADb", "2001:db8::/32", "boom")
        assert s.has_errors() is True

    def test_error_rendered_in_jira_block(self):
        s = Summary(dry_run=False)
        s.record_route("RADb", "created", "2001:db8::/32", "AS64496")
        s.record_error("RADb", "2001:db8:1::/48", "create failed (400): nope")
        out = render(s)
        # Successful object still shown alongside the failure.
        assert "+ radb route6 2001:db8::/32 AS64496" in out
        assert "! radb 2001:db8:1::/48 FAILED: create failed (400): nope" in out

    def test_error_only_registry_appears(self):
        s = Summary()
        s.record_error("RADb", "ROAs", "network error")
        out = render(s)
        assert "# RADb" in out
        assert "! radb ROAs FAILED: network error" in out
