# CardPointe Base Troubleshooting

## Typical failures

- **Auth failure**: check API username/password and MID.
- **Wrong base URL**: confirm `cardpointe_api_base` ends with `/cardconnect/rest/`.
- **Timeout**: increase connect/read timeouts or verify network access.
- **Non-JSON response**: verify endpoint and gateway availability.

## What to check in logs

Look for `[CARDPOINTE]` entries, the transaction reference (if any), and the `correlation_id` to
follow a request/response pair.

## Redaction policy reminder

Do not log or store PAN/CVV. Tokens are redacted to the last 4 digits only.
