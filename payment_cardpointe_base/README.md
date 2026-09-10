# CardPointe Base (payment_cardpointe_base)

This module provides shared CardPointe configuration and API client helpers for Odoo 16 payment
providers. It includes provider fields, redacted logging, error mapping, diagnostics, and the
shared POS refund-routing service, but no checkout UI.

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

CardPointe logs are tagged with `[CARDPOINTE]`.

## Refund routing

Every refund first performs an inquiry. The inquiry must contain a non-empty transaction with the
same reference that was requested; malformed, mismatched, failed, or explicitly declined inquiries
stop without a financial mutation. A full unsettled return uses an amountless void only when the inquiry reports
a known positive original amount exactly equal to the requested amount and does not prohibit
voiding. Partial returns always call `refund` for their exact amount. Settled/full returns use
`refund` when eligible; a `respcode=28` partial-refund response is returned as a failure and is
never converted into a full void. A full void may fall back to the exact full refund only when the void response indicates settlement and the inquiry still permits both operations. Conversely, a full refund may fall back to an amountless void only after an authoritative non-approved refund response, exact positive full amount match, and permitted void eligibility; approved/transport-failed, partial, prohibited, or negatively-settled responses never authorize a second mutation.
