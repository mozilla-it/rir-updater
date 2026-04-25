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


def get_ripe_db_auth() -> str:
    """Return base64(username:password) for the RIPE DB REST API Basic auth header."""
    import base64

    username = read_op("op://Code/Mozilla - RIPE NNC/username")
    password = read_op("op://Code/Mozilla - RIPE NNC/credential")
    return base64.b64encode(f"{username}:{password}".encode()).decode()


def get_ripe_rpki_key() -> str:
    """Return the API key for the RIPE RPKI Management API."""
    return read_op("op://Code/Mozilla - RIPE NNC/RPKI API Key")
