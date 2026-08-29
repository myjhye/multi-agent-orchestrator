"""Tests for orchestrator.email_validation."""

import pytest

from orchestrator.email_validation import (
    EmailValidationError,
    is_valid_email,
    validate_email,
)


VALID = [
    "user@example.com",
    "USER@EXAMPLE.COM",
    "first.last@example.com",
    "user+tag@example.com",
    "user_name@example.com",
    "user-name@example.com",
    "a@b.co",                                   # shortest plausible address
    "!#$%&'*+-/=?^_`{|}~@example.com",          # every legal atext symbol
    "user@sub.domain.example.com",              # deep subdomain nesting
    "user@example.museum",                      # long TLD
    "user@123.example.com",                     # all-numeric label
    "user@ex-ample.com",                        # internal hyphen
    "1234567890@example.com",
    "x" * 64 + "@example.com",                  # local part at the 64 limit
    "user@" + "a" * 63 + ".com",                # label at the 63 limit
]

INVALID = [
    ("", "empty string"),
    ("userexample.com", "no @ at all"),
    ("@example.com", "empty local part"),
    ("user@", "empty domain"),
    ("@", "nothing on either side"),
    ("user@@example.com", "doubled @"),
    ("us@er@example.com", "two separate @"),
    (".user@example.com", "local starts with dot"),
    ("user.@example.com", "local ends with dot"),
    ("us..er@example.com", "consecutive dots in local"),
    ("user name@example.com", "space in local part"),
    ("user@exam ple.com", "space in domain"),
    (" user@example.com", "leading whitespace"),
    ("user@example.com ", "trailing whitespace"),
    ("user\n@example.com", "embedded newline / header injection"),
    ("user@exa\tmple.com", "embedded tab"),
    ("user@example", "domain has no dot"),
    ("user@example.", "domain ends with dot"),
    ("user@.example.com", "domain starts with dot"),
    ("user@example..com", "consecutive dots in domain"),
    ("user@-example.com", "label starts with hyphen"),
    ("user@example-.com", "label ends with hyphen"),
    ("user@example.c", "single-character TLD"),
    ("user@example.c0m", "digit in TLD"),
    ("user@example.com!", "illegal character in domain"),
    ("user(comment)@example.com", "RFC comments unsupported"),
    ('"user name"@example.com', "quoted local part unsupported"),
    ("user@[192.168.0.1]", "IP literal unsupported"),
    ("josé@example.com", "non-ASCII local part"),
    ("user@exämple.com", "non-ASCII domain / IDN"),
]


@pytest.mark.parametrize("address", VALID)
def test_accepts_valid_addresses(address):
    assert is_valid_email(address) is True


@pytest.mark.parametrize("address,reason", INVALID, ids=[r for _, r in INVALID])
def test_rejects_invalid_addresses(address, reason):
    assert is_valid_email(address) is False


class TestLengthLimits:
    def test_local_part_at_limit_is_accepted(self):
        assert is_valid_email("x" * 64 + "@example.com")

    def test_local_part_over_limit_is_rejected(self):
        assert not is_valid_email("x" * 65 + "@example.com")

    def test_label_at_limit_is_accepted(self):
        assert is_valid_email("user@" + "a" * 63 + ".com")

    def test_label_over_limit_is_rejected(self):
        assert not is_valid_email("user@" + "a" * 64 + ".com")

    @staticmethod
    def _address_of_length(total):
        """Build a structurally valid address of exactly *total* characters."""
        # "x"*64 + "@" + "a"*62 + "." + "a"*62 + "." + filler + ".com"
        filler = total - (64 + 1 + 62 + 1 + 62 + 1 + 4)
        assert 1 <= filler <= 63, "filler label out of range for this length"
        address = "x" * 64 + "@" + "a" * 62 + "." + "a" * 62 + "." + "a" * filler + ".com"
        assert len(address) == total
        return address

    def test_address_at_total_limit_is_accepted(self):
        assert is_valid_email(self._address_of_length(254))

    def test_address_over_total_limit_is_rejected(self):
        assert not is_valid_email(self._address_of_length(255))


class TestNonStringInput:
    @pytest.mark.parametrize("value", [None, 42, 3.5, b"user@example.com",
                                       ["user@example.com"], object()])
    def test_is_valid_email_returns_false_and_does_not_raise(self, value):
        assert is_valid_email(value) is False

    def test_validate_email_raises_with_type_name(self):
        with pytest.raises(EmailValidationError, match="got NoneType"):
            validate_email(None)


class TestNormalization:
    def test_domain_is_lowercased(self):
        assert validate_email("user@EXAMPLE.COM") == "user@example.com"

    def test_local_part_case_is_preserved(self):
        # RFC 5321 2.4: only the receiving host may interpret local-part case.
        assert validate_email("User.Name@Example.COM") == "User.Name@example.com"

    def test_valid_address_is_returned_unchanged_when_already_normal(self):
        assert validate_email("user@example.com") == "user@example.com"


class TestErrorMessages:
    """The reason matters -- callers surface it to end users."""

    @pytest.mark.parametrize(
        "address,fragment",
        [
            ("", "empty"),
            ("userexample.com", "missing '@'"),
            ("@example.com", "local part is empty"),
            ("user@", "domain is empty"),
            ("user@example", "at least one dot"),
            ("user@example.c", "top-level domain"),
            ("us..er@example.com", "consecutive dots"),
            ("user\n@example.com", "control character"),
            ("x" * 65 + "@example.com", "exceeds 64"),
        ],
    )
    def test_message_explains_the_failure(self, address, fragment):
        with pytest.raises(EmailValidationError, match=fragment):
            validate_email(address)

    def test_error_is_a_valueerror(self):
        # Callers commonly catch ValueError; keep that contract.
        assert issubclass(EmailValidationError, ValueError)


def test_is_valid_email_agrees_with_validate_email():
    """The two entry points must never disagree."""
    for address in VALID + [a for a, _ in INVALID]:
        try:
            validate_email(address)
        except EmailValidationError:
            expected = False
        else:
            expected = True
        assert is_valid_email(address) is expected, address
