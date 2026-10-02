# CardPointe endpoints used by these modules

Labels: **DOCUMENTED** — Fiserv's CardPointe documentation (developer.cardpointe.com /
developer.fiserv.com), read through search results because those hosts were not reachable from the
build environment; the wording is paraphrased. **CODE** — what this module sends. Re-check the
DOCUMENTED rows against the live pages, and confirm each in UAT, before production.

## Gateway (`https://<site>[-uat].cardconnect.com/cardconnect/rest/`, HTTP Basic, `merchid` in body)

| Call | Module use | Contract |
| --- | --- | --- |
| `PUT` base URL `{"merchid"}` | credential test | DOCUMENTED: validates that the MID matches the credentials; answers "CardConnect REST Servlet". Authorizes nothing. |
| `GET inquire/<retref>/<merchid>` | first step of every return | DOCUMENTED: `setlstat` ∈ Authorized, Queued for Capture, Accepted, Rejected, Voided, Declined, Format Error, Amount Under Review, Token Decrypt, Zero Amount; `voidable` / `refundable` `Y`/`N`. |
| `GET inquireByOrderid/<orderid>/<merchid>/1` | resolve a terminal sale with an unknown outcome | DOCUMENTED: for an authorization interrupted before a response; returns the transaction (with `retref`) if one was created. `set=1` restricts to the MID. Not found: `respproc` PPS, `respcode` 29, *Txn not found*, `respstat` C. |
| `void` `{merchid, retref}` | full return of an unsettled sale | DOCUMENTED: amount optional — omitted means the full amount; partial void only while `Authorized` (not captured); `Queued for Capture` can only be voided in full; no partial void for debit. CODE sends no amount and is only used for a full return. |
| `refund` `{merchid, retref, amount}` | partial return; full return after settlement | DOCUMENTED: for settled transactions; amount optional (full when omitted); an unsettled one answers *Txn not settled* (`respstat` C) unless the MID allows unsettled refunds. CODE always sends the amount. |
| `sigcap` `{merchid, retref, signature}` | attach a terminal signature | CODE |
| `auth` | POS manual entry (Hosted iFrame token) | DOCUMENTED: `respstat` A approved / B retry / C declined; amount as a decimal (`"10.50"`) or in minor units without a decimal (`"1050"`). CODE sends `"10.50"` with `capture: "Y"`. |

CODE uses `POST` for `void`, `refund`, `sigcap` and `auth`; the documentation shows `PUT`. Confirm
the method is accepted in UAT (step in `pos_cardpointe_poc/README.md`).

## Integrated Terminal API (Bolt/Clover, `https://<site>[-uat].cardpointe.com/api`)

| Call | Contract |
| --- | --- |
| `POST /v2/connect` `{merchantId, hsn}` | DOCUMENTED: `Authorization: <API key>`; returns `X-CardConnect-SessionKey` (valid 10 minutes). |
| `POST /v4/authCard` | DOCUMENTED for v2/v3 (CODE uses v4): `amount` with two implied decimals (`"1050"` = $10.50); `orderId` alphanumeric, at most 19 characters, unique; `capture` defaults to true. A decline is HTTP 200 with a Gateway body (`respstat` B/C). Timeout: terminal 2 minutes plus 32 s at the Gateway; on its own timeout the terminal service calls `inquireByOrderid`, and voids by order id (three attempts) when that fails. |
| `POST /v2/disconnect`, `/v2/cancel`, `/v2/readSignature` | DOCUMENTED: disconnect ends the session and does **not** cancel an in-flight transaction; cancel does. |
| errors | DOCUMENTED: 400 + `errorCode` 3 generic, 4 missing parameter, 1 session key missing/invalid, 7 terminal in use; 401 bad API key or MID. CODE also maps 8 cancelled, 9 merchant mode. |
