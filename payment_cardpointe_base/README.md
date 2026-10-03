# CardPointe Base (payment_cardpointe_base) — Odoo 18

Shared CardPointe Gateway configuration and client for the POS terminal modules: the merchant
configuration record, the Gateway REST client (`inquire`, `inquireByOrderid`, `void`, `refund`,
`sigcap`, `auth`, credential check), the return-routing service, redacted logging, and the
`cardpointe` provider code.

It provides **no online checkout and no saved-card charging** on 18.0 (see the last section).

Verified on Odoo 18.0 Community (`70bdd4035000c836f9f459345e45cc4d408c68d8`) with mocked
CardPointe responses. No request has reached CardPointe from this branch: the endpoint semantics
below come from Fiserv's CardPointe documentation (`docs/ENDPOINTS.md` says which ones) and are
confirmed in UAT by the steps in `pos_cardpointe_poc/README.md`.

## Merchant configuration (authoritative for POS)

Top-level menu *CardPointe Merchant Configs* (system administrators only):

| Field | Meaning | Set by |
| --- | --- | --- |
| Name, Company | one record per merchant id per company | configuration package |
| Merchant ID (MID) | CardPointe merchant id | configuration package |
| Gateway Base URL | `https://<site>-uat.cardconnect.com/cardconnect/rest/` (UAT) or `https://<site>.cardconnect.com/cardconnect/rest/` (production) — the environment selection | configuration package |
| Gateway Username / Password | Gateway API credentials (HTTP Basic). Optional on the record, required before any call; the password is system-only | authorized operator, from the credential reference |
| Tokenizer URL | Hosted iFrame Tokenizer URL, only for POS manual entry | configuration package, when manual entry is in scope |
| Timeouts, debug logging | connection/read timeouts; debug adds redacted payloads | client tuning |

**Test Credentials** (header button) sends CardPointe's documented credential check — a `PUT` to the
base URL with `{"merchid": <MID>}`, expected answer *CardConnect REST Servlet* — and authorizes
nothing. The provider form's **Test CardPointe Connection** does the same with the provider's
copy of the values.

The `payment.provider` record (code `cardpointe`) keeps a one-way copy of a linked merchant
configuration (`cardpointe_merchant_config_id`). The merchant configuration is the record the POS
modules read; edit that one.

## Returns: void or refund

`services/refunds.py` `execute_void_or_refund()` decides every POS return:

1. `inquire` the original `retref`. A failed, empty, malformed or mismatched inquiry, an explicit
   decline, or an invalid amount **stops with no void or refund**.
2. A **partial** return is always a `refund` of exactly that amount — never a void, which would
   reverse the whole sale. Before settlement CardPointe answers *Txn not settled*; the return then
   fails and must be repeated after settlement (or the MID enabled for unsettled refunds).
3. A **full** return voids the sale while it is unsettled (`setlstat` not in the settled set and
   `voidable` not `N`), and refunds it once settled (`Accepted`, settled/captured/batched values).
   `refundable` = `N` stops the return.
4. The only fallbacks: a full void refused because the sale settled meanwhile becomes the full
   refund; a full refund refused as *not settled* becomes the full void — each only when the
   inquiry permits it.
5. A void/refund request that got **no HTTP answer** is reported with `unknown: true`: the POS tells
   the cashier not to retry and to check CardPointe reporting, because it may have been applied.

## Logs

Gateway requests log method, path, masked headers and redacted payloads; responses are sanitized
(signatures, receipts and EMV data removed). Card numbers and CVV are never stored or logged.

## Saved-card (token) charging: not implemented on 18.0

`payment_token_partner_form` and `payment_token_invoice` are provider-agnostic. On 18.0 no module
implements CardPointe tokenization or `_send_payment_request` for code `cardpointe`, so a CardPointe
token cannot be created through the partner form nor charged from an invoice: Odoo 18's base
`_send_payment_request` only checks the provider and logs. This is an `enhancement`, not
configuration. The reusable source is the 16.0 `payment_cardpointe` module of this repository
(with `payment_token_base` / `payment_token_autocharge`), which has the Gateway profile calls and a
`_send_payment_request` override; a port to 18 needs `payment.method` records, the Odoo 18
`payment_form` JS API, `(env)` hooks, and its `_send_payment_request` fixed to call `super()`
(16.0 assigns `super()` without calling it).

## Tests

- `tests/test_gateway_refund_logic.py`, `tests/test_services_smoke.py`: standalone
  (`python -m pytest payment_cardpointe_base/tests/test_gateway_refund_logic.py`).
- POS-level behaviour is tested in `pos_cardpointe_poc/tests/`.
