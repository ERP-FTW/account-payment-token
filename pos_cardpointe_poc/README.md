# pos_cardpointe_poc — CardPointe Bolt/Clover terminal payments in the Odoo 18 POS

Card-present POS sales on CardPointe Integrated Terminal devices (Clover Flex/Mini/Pocket in Bolt
mode) through server-side calls, POS returns through the CardPointe Gateway, and an optional
Hosted iFrame manual-entry fallback. Depends on `point_of_sale` and `payment_cardpointe_base`.

Verified on Odoo 18.0 Community (`70bdd4035000c836f9f459345e45cc4d408c68d8`): install, upgrade
from `2f156b1`, module tests (cashier returns, company isolation, unknown terminal outcomes), a
POS asset build, and configuration through reviewed `hermes-odoo-data-load/v1` packages in a lab.
**No physical terminal or CardPointe UAT call has been exercised from this branch**; the UAT
acceptance below is still required.

## Sale flow

1. `/pos_cardpointe_poc/start` (cashier must be a POS user of the method's company; the method,
   terminal configuration and merchant configuration must share that company): takes the
   terminal's runtime lock, opens a session (`POST /v2/connect`, `X-CardConnect-SessionKey`), and
   issues a **terminal order id** — 19 uppercase alphanumeric characters, the Integrated Terminal
   API's documented `orderId` format — returned to the POS line before any charge.
2. `/pos_cardpointe_poc/auth`: `POST /v4/authCard` with the amount in implied cents (`"4250"` for
   42.50), `capture: true`, the terminal order id, and `includeSignature` per the signature policy.
   The session is always disconnected and the lock released afterwards.
3. Outcomes:
   - approved → the line stores `retref`, `authcode`, the approved amount and the terminal order id;
   - declined / cancelled / terminal in use / merchant mode → reported as such, nothing to resolve;
   - **unknown** (timeout, 5xx, dropped connection, exception) → the server immediately asks the
     Gateway `inquireByOrderid` for that terminal order id: an approval for the line's amount is
     recorded as the sale (never charged again); *Txn not found* or a voided authorization means
     no charge stands and the cashier may retry; an approval for a **different amount** (for
     example a tip chosen during the lost call) and anything else stay **unknown** — void or
     adjust that charge in CardPointe, then press Send to check again.
4. An **unknown** line blocks a new charge and manual entry. Pressing Send again calls
   `/pos_cardpointe_poc/inquire` (also reachable as `/poll` with the terminal order id), which
   resolves it the same way without charging.

Deployment prerequisite: the terminal may take up to 2 minutes plus 32 s at the Gateway. Keep
`request_timeout_seconds` at or above 160 and the Odoo worker `limit_time_real` (and any reverse
proxy timeout) above that, or Odoo gives up first and every slow sale becomes an unknown outcome to
resolve.

## Returns

A negative CardPointe line on a refund order calls `/pos_cardpointe_poc/refund` (same access
checks; the refunded order must belong to the method's company). The original sale payments of the
refunded lines are allocated the return amount, and each allocation goes through
`payment_cardpointe_base` `execute_void_or_refund` (see its README): the inquiry comes first; a
partial return is an exact refund and never a void; a full unsettled return voids; an unanswered
void/refund is reported as an unknown outcome not to be retried.

**Return hold.** When a void/refund gets no answer, or a return spread over several sale payments
stops after one of them was already returned, the route answers `unknown` (the POS keeps the line
unknown and never resends it) and stamps a hold on each sale payment involved
(field `cardpointe_refund_hold`, with the time and the references). Any further return of that
sale is refused until a POS manager has checked CardPointe reporting and pressed **Clear CardPointe
return hold** on the original payment (Point of Sale → Orders → Payments). A failure before any
money moved stays an ordinary, retryable error. If Odoo itself does not answer the POS during a
return, the POS line becomes unknown too; check reporting before returning that sale again.

## Configuration

Create the records with the reviewed configuration packages (Guild skill `odoo-cardpointe`,
`recipes/pos-terminal/`) or by hand:

- **CardPointe Terminal Config** (Point of Sale → Configuration): company, merchant configuration,
  Base URL (`https://bolt-uat.cardpointe.com/api` for UAT — change deliberately for production),
  device type, HSN; then signature mode/threshold, request timeout, terminal receipt printing as the
  client wants. The **Auth Key** is entered by an authorized operator; the record can exist
  without it, but no terminal call is sent until it is set.
- **Payment method**: Use a Payment Terminal = *CardPointe POC*, CardPointe Config, a bank journal,
  and *Enable Manual Card Entry* only if the iFrame fallback is in scope (needs the merchant's
  Tokenizer URL).
- Add the method to the POS configuration, keeping the methods it already has.

## UAT acceptance (needs the CardPointe UAT merchant, its credentials and a terminal in Bolt mode)

1. **Test Credentials** on the merchant configuration and **Test Connect** on the terminal
   configuration succeed (connection only — not payment evidence).
2. Approved sale: the `pos.payment` has `cardpointe_status = approved`, a `retref`, and
   `cardpointe_terminal_order_id`; the Gateway `inquire/<retref>/<merchid>` shows the same amount.
3. Decline (UAT decline amount from CardPointe's test card guide) is shown as a decline.
4. Unknown outcome: interrupt the network between Odoo and the terminal during authCard; the line
   becomes unknown or is resolved through `inquireByOrderid`; no second charge exists in reporting.
5. Partial return before settlement fails with *Txn not settled* and moves nothing; after
   settlement it refunds exactly the returned amount; a full return before settlement voids.
   Interrupt the Gateway during a return: the line becomes unknown, the sale payment shows the
   return hold, and a second return is refused until a manager clears it.
6. Confirm the Gateway accepts the module's `POST` for void/refund/auth (the docs show `PUT`).

Record Odoo records and CardPointe reporting side by side; mark which runs reached UAT.

## Terminal verification (manual)

### 1) Connect (verify session key)

```bash
BOLT_BASE="https://bolt-uat.cardpointe.com/api"
AUTH_KEY="<bolt_auth_key>"
MID="800000009875"
HSN="C047UG43720996"

curl -sv -X POST "$BOLT_BASE/v2/connect" \
  -H "Authorization: $AUTH_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"merchantId\":\"$MID\",\"hsn\":\"$HSN\"}"
```

Confirm `X-CardConnect-SessionKey` exists in response headers.

### 2) authCard (baseline includeSignature=false)

```bash
SESSION_KEY="<session_key_from_connect>"
ORDER_ID="POS-TEST-1001"
AMT_CENTS="100"

curl -sv -X POST "$BOLT_BASE/v4/authCard" \
  -H "Authorization: $AUTH_KEY" \
  -H "X-CardConnect-SessionKey: $SESSION_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"merchantId\":\"$MID\",\"hsn\":\"$HSN\",\"amount\":\"$AMT_CENTS\",\"capture\":true,\"orderId\":\"$ORDER_ID\",\"includeSignature\":false}"
```

### 3) readSignature (post capture path)

```bash
curl -sv -X POST "$BOLT_BASE/v2/readSignature" \
  -H "Authorization: $AUTH_KEY" \
  -H "X-CardConnect-SessionKey: $SESSION_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"merchantId\":\"$MID\",\"hsn\":\"$HSN\"}"
```

### 4) sigcap (attach signature to original retref)

```bash
GW_BASE="https://fts-uat.cardconnect.com/cardconnect/rest"
GW_USER="testing"
GW_PASS="testing123"
RETREF="<retref_from_authCard>"
SIG_B64="<signature_blob_from_readSignature>"

curl -sv -u "$GW_USER:$GW_PASS" \
  -H "Content-Type: application/json" \
  -d "{\"merchid\":\"$MID\",\"retref\":\"$RETREF\",\"signature\":\"$SIG_B64\"}" \
  "$GW_BASE/sigcap"
```

## Test Connect button

Terminal config form includes admin-only **Test Connect** button.

- It calls terminal `connect` using current record settings.
- It reports success/failure in a notification.
- Secret headers/tokens remain redacted in server logs.

## What is persisted on payment

- `cardpointe_retref`
- `cardpointe_authcode`
- `cardpointe_respcode`
- `cardpointe_resptext`
- `cardpointe_token` (if returned)
- `cardpointe_status`
- `cardpointe_signature_required`
- `cardpointe_signature_captured`
- `cardpointe_signature_method`

## Troubleshooting

- **401 Unauthorized**: wrong/missing `auth_key`.
- **errorCode 9 / merchant mode**: terminal is in Merchant Mode, switch to CardPointe Integrated/Bolt app.
- **errorCode 8 / cancelled**: payment cancelled on terminal.
- **timeout**: terminal or network did not finish within timeout; verify terminal app mode, connectivity, and retry.
- **errorCode 7 / already in use on connect**: treat as stale terminal session state; retry once, then restart CardPointe app on terminal if it persists.
- **Signature not attached**: validate gateway credentials on terminal config's merchant config and confirm `sigcap` returns approval.
