# Reference bot

A tiny vendor bot that follows `../ai-contract.md` (version 1). It is also a starting point for a
vendor: copy this folder, replace `build_replies` in `app/handlers.py` with your own logic.

It answers "Aap ne likha: ..." plus a summary of any media. No AI, no database.

| File | What it does |
|---|---|
| `app/main.py` | `POST /ai-reply`, `GET /health`. Verifies the signature **before** reading the body |
| `app/security.py` | HMAC check: raw bytes, 300 s tolerance, constant-time compare, rotation (two signatures) |
| `app/schemas.py` | Request models (text, image, sticker, document, ptt, audio, `base64Data: null`) and `{"replies": [...]}` |
| `app/handlers.py` | The answer logic. Limits: 5 bubbles, 4000 characters each |
| `app/config.py` | `BOT_SECRET` (required), `PORT` (9000), `MAX_SKEW_SECONDS` (300) |

## Run it

The bot secret is the `whsec_...` value printed once when the vendor is created. It is read from
the environment and is never written to a file, a log or an image.

```bash
# from whatsup-plugin/ (Docker, joins the compose network as http://reference-bot:9000)
make gateway ARGS="create-vendor --name 'Ref Bot Shop' --number 923001230099 \
  --email ref@example.com --bot-url http://reference-bot:9000"      # note the Bot secret
make bot BOT_SECRET=whsec_...
make gateway ARGS="test-bot <vendor-id>"                            # PASS = contract works

# or on the host, no Docker (use --bot-url http://localhost:9000 for a vendor the api can reach)
make bot-local BOT_SECRET=whsec_...
```

Wrong secret: the bot answers an empty `401` and the gateway reports `BOT_HTTP_ERROR`.

## Test

```bash
make bot-test       # 41 tests; they sign requests with their own copy of the contract formula
make bot-lint       # ruff + mypy --strict
```

The tests cover: good and bad signature, tampered body, signing parsed JSON instead of raw bytes,
old/future timestamps, missing headers, rotation header, every media type, a null `base64Data`,
corrupt base64, a 12 MB file, malformed bodies (400), retry with the same `requestId`, and that
the secret and message text never show up in responses or logs.
