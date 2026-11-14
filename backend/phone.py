"""Phone numbers, stored in E.164 form ("+15105733065") so they match the handle Photon reports for an iMessage sender."""
import re


class PhoneError(ValueError):
    pass


def normalize_phone(value: str) -> str:
    """"(510) 573-3065", "510.573.3065", "1 510 573 3065" and "+15105733065" all become "+15105733065".

    10 digits without a country code are read as a US number. Raises PhoneError when it cannot be a phone number.
    """
    raw = (value or "").strip()
    digits = re.sub(r"\D", "", raw)
    if not raw.startswith("+"):
        if len(digits) == 10:
            digits = "1" + digits
        elif not (len(digits) == 11 and digits.startswith("1")):
            raise PhoneError("Enter a US number with area code, or start with + and the country code")
    if not 8 <= len(digits) <= 15:
        raise PhoneError("That doesn't look like a phone number")
    return "+" + digits
