"""How a job is shown to admins: never the text of a message, never a whole phone number."""

import re

from app.models.outbound import JobType

_CODE = re.compile(r"(?<![0-9])[0-9]{4,8}(?![0-9])")


def mask_number(number: str) -> str:
    """923001234567 -> +9230*****567 (enough to tell numbers apart, not enough to call)."""
    digits = "".join(c for c in number if c.isdigit())
    if len(digits) <= 7:
        return "+" + "*" * len(digits)
    return f"+{digits[:4]}{'*' * (len(digits) - 7)}{digits[-3:]}"


def mask_body(body: str | None, job_type: str) -> str:
    """OTP -> ``OTP ****13`` (last two digits of the code); anything else -> its length only."""
    if body is None:
        return "(text removed)"
    if job_type == JobType.OTP.value:
        match = _CODE.search(body)
        if match:
            return f"OTP ****{match.group()[-2:]}"
    return f"{job_type} ({len(body)} characters)"
