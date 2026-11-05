import pytest
from hoja_server import identity_transfer

MASTER = "master-key-held-only-by-the-control-plane-0123456789"

def test_one_tenants_key_cannot_mint_an_identity_for_another():
    # acme holds only its derived key; it must not be able to sign for beta.
    acme_key = identity_transfer.tenant_key(MASTER, "acme.hoja.work")
    forged = identity_transfer.issue(
        key=acme_key, audience="beta.hoja.work",
        email="victim@beta.test", subject="s", email_verified=True)
    beta_key = identity_transfer.tenant_key(MASTER, "beta.hoja.work")
    with pytest.raises(identity_transfer.TransferError) as exc:
        identity_transfer.redeem(key=beta_key, token=forged, audience="beta.hoja.work")
    assert exc.value.reason == "bad_signature"

def test_derived_keys_differ_per_audience_and_are_stable():
    a = identity_transfer.tenant_key(MASTER, "acme.hoja.work")
    b = identity_transfer.tenant_key(MASTER, "beta.hoja.work")
    assert a != b
    assert a == identity_transfer.tenant_key(MASTER, "ACME.hoja.work")
    assert len(a) >= 32


def test_the_derived_key_survives_an_environment_variable():
    # The key has to round-trip through an env var and still verify, otherwise
    # the control plane signs with one byte sequence and the tenant another.
    key = identity_transfer.tenant_key(MASTER, "acme.hoja.work")
    assert isinstance(key, str) and "\n" not in key
    token = identity_transfer.issue(key=key, audience="acme.hoja.work",
                                    email="a@b.co", subject="s", email_verified=True)
    claims = identity_transfer.redeem(key=key, token=token, audience="acme.hoja.work")
    assert claims["email"] == "a@b.co"
