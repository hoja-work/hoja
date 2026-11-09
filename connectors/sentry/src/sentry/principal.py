"""The signed principal is one contract for every connector, so it is shared, not re-implemented."""

from google_workspace.principal import (
    SECRET_ENV,
    Principal,
    sign_principal,
    verify_principal,
)

PRINCIPAL_HEADER = "x-hoja-principal"

__all__ = [
    "PRINCIPAL_HEADER",
    "SECRET_ENV",
    "Principal",
    "sign_principal",
    "verify_principal",
]
