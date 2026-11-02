"""Body of POST /v1/message-to-customer (plan section 17.B.2)."""

from pydantic import BaseModel, ConfigDict

from app.models.outbound import JobType


class MessageToCustomer(BaseModel):
    # Unknown fields are ignored on purpose: vendors may add their own without breaking.
    model_config = ConfigDict(extra="ignore")

    number: str
    message: str  # length is checked by the service so the answer is 413 MESSAGE_TOO_LONG
    type: JobType = JobType.TRANSACTIONAL
    ttlSeconds: int | None = None
