import pytest
from miniapp_test_support import TOKEN, signed_data


def auth(raw, **kwargs):
    from bridge.miniapp_auth import authenticate

    return authenticate(raw, TOKEN, frozenset({"12345"}), now=10000, **kwargs)


def test_validated_user_is_the_private_chat_scope():
    who = auth(signed_data(now=10000, chat_instance="untrusted-scope"))
    assert who.user_id == "12345"
    assert who.chat_id == "12345"
    assert who.name == "Test <user>"


@pytest.mark.parametrize("raw", ["", "a=b", "user=%ZZ", "a=" + "x" * 9000])
def test_rejects_malformed_or_oversized_data(raw):
    with pytest.raises(ValueError):
        auth(raw)


@pytest.mark.parametrize("change", ["tamper", "duplicate", "hash_duplicate", "wrong_key"])
def test_rejects_forged_and_ambiguous_data(change):
    raw = signed_data(now=10000)
    if change == "tamper":
        raw = raw.replace("12345", "12346")
    elif change == "duplicate":
        raw += "&auth_date=10000"
    elif change == "hash_duplicate":
        raw += "&hash=" + "0" * 64
    else:
        raw = raw[:-10] + "0" * 10
    with pytest.raises(ValueError):
        auth(raw)


@pytest.mark.parametrize("age", [3601, -31, 100000])
def test_rejects_expired_or_future_data(age):
    with pytest.raises(ValueError):
        auth(signed_data(now=10000 - age))


@pytest.mark.parametrize("user_id", [54321, True, "12345", -1, 0, 2**53, None])
def test_rejects_unallowed_or_invalid_user_ids(user_id):
    with pytest.raises(ValueError):
        auth(signed_data(now=10000, user_id=user_id))


def test_includes_optional_signature_in_hmac_check():
    assert auth(signed_data(now=10000, signature="optional-signature")).user_id == "12345"
