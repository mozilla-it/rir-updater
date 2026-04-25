class RirUpdaterError(Exception):
    pass


class ApiError(RirUpdaterError):
    pass


class CredentialError(RirUpdaterError):
    pass


class ConfigError(RirUpdaterError):
    pass
