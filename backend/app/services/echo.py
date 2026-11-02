"""``echo`` inbound mode: say back what arrived, so the data path can be checked without a bot."""

import math

from app.schemas.ai_reply import MAX_BUBBLE_CHARS, AiReplyRequest


def make_echo_replies(payload: AiReplyRequest) -> list[str]:
    texts = [m.text.strip() for m in payload.incoming_messages if m.text.strip()]
    typed = " / ".join(texts) if texts else "(no text)"

    counts: dict[str, int] = {}
    total_bytes = 0
    for message in payload.incoming_messages:
        if message.media is not None:
            counts[message.media.media_type] = counts.get(message.media.media_type, 0) + 1
            total_bytes += message.media.file_size
    parts = [f"{n} {kind}" for kind, n in counts.items()]
    if total_bytes:
        parts.append(f"{math.ceil(total_bytes / 1024)} KB")
    summary = f" ({', '.join(parts)})" if parts else ""

    return [f"Aap ne likha: {typed}{summary}"[:MAX_BUBBLE_CHARS]]
