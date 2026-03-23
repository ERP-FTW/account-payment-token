# CardPointe Base (payment_cardpointe_base)

This module provides shared CardPointe configuration and API client helpers for Odoo 16 payment
providers. It includes provider fields, redacted logging, error mapping, and diagnostics, but no
checkout UI.

## Configuration

Enable the CardPointe provider and fill in:

- **API Base URL** (`cardpointe_api_base`): example `https://.../cardconnect/rest/`
- **API Username / Password**
- **Merchant ID (MID)**
- **Hosted iFrame Tokenizer URL**
- **Test Connection Endpoint** (relative path from Gateway API docs)
- Optional: timeouts and debug logging

## Debug logging

Enable **CardPointe Debug Logging** to include sanitized request/response logs. Tokens are
redacted (only last 4 digits shown), and sensitive fields (PAN/CVV/passwords/authorization) are
never logged.

## Test CardPointe Connection

Use **Test CardPointe Connection** on the provider form to validate credentials and connectivity.
Set **Test Connection Endpoint** on the provider (or define `ENDPOINT_TEST_CONNECTION`) as
described in `docs/ENDPOINTS.md`.

## Logs

CardPointe logs are tagged with `[CARDPOINTE]`. Example search:

```bash
rg "\[CARDPOINTE\]" -n odoo.log
```

## Developer Mode

Example `curl` call to the ecommerce process route (requires a valid transaction reference and
access token):

```bash
curl -X POST https://your-odoo.test/payment/cardpointe/process \
  -H "Content-Type: application/json" \
  -d '{"reference": "SO123-1", "access_token": "...", "token": "tok_..."}'
```

Tail logs for correlation IDs:

```bash
tail -f odoo.log | rg "\[CARDPOINTE\]"
```

## Refund fallback behavior

If a gateway refund response returns `respcode=28` (`Txn not settled`), the base refund service will automatically retry as a void for the same `retref`.
