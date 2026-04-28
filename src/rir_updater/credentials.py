import subprocess

from rir_updater.exceptions import CredentialError


def read_op(reference: str) -> str:
    """Fetch a secret from 1Password using the op CLI."""
    try:
        result = subprocess.run(
            ["op", "read", reference],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except FileNotFoundError:
        raise CredentialError(
            "1Password CLI ('op') not found — install it from https://1password.com/downloads/command-line/"
        ) from None
    except subprocess.CalledProcessError as e:
        raise CredentialError(
            f"Failed to read secret from 1Password ({reference!r}): {e.stderr.strip()}"
        ) from e


def get_ripe_db_auth(username_ref: str, password_ref: str) -> str:
    """Return base64(username:password) for the RIPE DB REST API Basic auth header."""
    import base64

    username = read_op(username_ref)
    password = read_op(password_ref)
    return base64.b64encode(f"{username}:{password}".encode()).decode()


def get_ripe_rpki_key(key_ref: str) -> str:
    """Return the API key for the RIPE RPKI Management API."""
    return read_op(key_ref)


def get_radb_portal_auth(username_ref: str, password_ref: str) -> tuple[str, str]:
    """Return (username, password) for the RADb portal HTTP Basic auth."""
    return read_op(username_ref), read_op(password_ref)


def get_radb_mntner_password(password_ref: str) -> str:
    """Return the RADb mntner password used for object-level authorization."""
    return read_op(password_ref)


def get_arin_api_key(key_ref: str) -> str:
    """Return the ARIN API key used for all IRR and RPKI requests."""
    return read_op(key_ref)
