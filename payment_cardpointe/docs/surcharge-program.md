# CardPointe program surcharge proof (Odoo 18)

## Boundary and lineage

Publication-only technical proof; no production/stage installation, real payment,
processor certification or business UAT. Baseline is PR22,
`7b68edabca38161293e4beaa36536364fa4f7122`, preserving its commits and authors.
Refreshed `18.0` is `0f9538f11dacee9a027ea3ba35477b981c85446b`.
PR21 `b655e693ce8cc32e994ac9e4a1976bf7798c8d5a` is documentary manual-markup
behavior, NOT the processor program and NOT an integration dependency.
The delta adds no module dependency; base owns reusable validation, online owns
quote/access/receipt, POS owns terminal/recovery/receipt. ACH production is unchanged.

## Authoritative documentation, freshly read 2026-11-23

- https://developer.fiserv.com/product/CardPointe/apis
- https://www.postman.com/cloverpos/cardpointe-integrated-payments/request/p5u0nas/surcharge-eligibility
- https://developer.fiserv.com/product/CardPointe/docs/documentation/CardPointeIntegratedTerminalDeveloperGuides.md?branch=active
- https://developer.fiserv.com/product/CardPointe/api/get/cardconnect/rest/surcharge?branch=active
- https://developer.fiserv.com/product/CardPointe/api/post/cardconnect/rest/auth?branch=active

The last URL documents **PUT or POST** auth; `/api/put/...` is not a valid
Developer Studio documentation page. Response schema was read using its actual
Response schema tab (not inferred from the request example).

## DOCUMENTED + VERIFIED_SOURCE field/operation map

| Operation | Documented inputs/ownership | Actual caller and persisted evidence |
|---|---|---|
| Online GET `/cardconnect/rest/surcharge` | Query `merchid`, `token`, `postal`, `amount`; original eligible subtotal in major units. It does not enable/disable the merchant program. | `payment.transaction._cp_prepare_quote` -> provider `_cp_online_request` -> base `_cardpointe_request` -> `request_json(params=...)`, NOT a GET JSON body. Token is transient; keyed digest binds token/postal/subtotal/currency/method/provider/MID/site/config and 5-minute expiry. |
| Online quote response | `token`: CREDIT/DEBIT/UNKNOWN; `postal`: RESTRICTED/NOT_RESTRICTED/UNKNOWN. `fee_amount` is returned for enrolled CREDIT + NOT_RESTRICTED with amount. DEBIT/restricted explicitly ineligible may omit fee. | `services/surcharge.quote_fee` requires known eligibility; missing eligible fee is NOT zero. Explicit zero stays zero. Contradictory eligibility/nonzero, malformed fee, messages or timeout block auth. `cp_quote` contains only subtotal, fee, display total, currency, eligibility labels. |
| Online PUT `/cardconnect/rest/auth` | `merchid`, `account` token, `amount` ORIGINAL subtotal, `orderid`, currency, capture, postal/ecomind. Gateway computes fee; never submit a locally inflated amount or fee override. | `_cp_process` first discloses quote, then validates its identifier/binding. Existing owned-cursor durable pre-send checkpoint and original identity are preserved. 100.00 stays 100.00 in auth. |
| Online authorization response | `respstat=A`, merchant/order identity, `retref`, final `amount`, `fee_amount`, `fee_type` SURCHRG/SURCHRG_WAIVED, optional fee metadata. | `_cp_apply_response` + `agreement` require subtotal+fee=final and quote fee=final fee. No rate calculator. `cp_auth_evidence` is a sanitized field allowlist (including mismatch); `cp_receipt` is written only for agreement; mismatch stays pending with sticky `cp_fee_hold`. |
| GET `/inquireByOrderid/{orderid}/{merchid}` | Original unique order/MID for a sent-but-unknown authorization. | Online `_cp_reconcile`; terminal gateway client uses documented `/1` selector. Timeout, missing/not-found online response never resends auth. Terminal program not-found remains unknown. Explicit identity changes do not silently match. |
| GET `/inquire/{retref}/{merchid}` | Retrieval-reference lookup, including existing return safety. | Existing gateway return pipeline unchanged; regression-tested. Not a replacement for order inquiry when no retref was returned. |
| Terminal POST `/v2/connect`, `/v2/disconnect` | merchantId, hsn, server-held API key; session header established and released. | Existing `CardPointeTerminalClient` lifecycle retained. No online quote endpoint is guessed onto a terminal. |
| Terminal POST `/v4/authCard` | merchantId, hsn, ORIGINAL amount in implied cents, unique orderId, capture; confirmAmount=true for configured program. Terminal service itself calls gateway inquireMerchant/surcharge and displays subtotal, fee, total AFTER reading the card. | `auth_card_with_session`: 100.00 -> `amount=10000`. Own v4 response validated; response `amount` converted from documented gateway units, fee_amount dollars/cents. Matching merchant/order/reference required. No gateway token retained for program output. |
| Terminal v4 response | fee_format=percent, fee_value rate basis-points representation, fee_type SURCHRG; waived uses empty format, zero value, SURCHRG_WAIVED and fee_amount=0.00. fee_authcode duplicates authcode; fee_retref/fee_merchid empty. | Shared arithmetic/consistency validation only, never local rate calculation. Sanitized `program_receipt` -> controller -> POS line `cardpointe_program_receipt` -> ORM -> reload -> printed receipt. Unusable approval is unknown, never a made-up zero. |
| Terminal POST `/v3/printReceipt` | merchantId, hsn, orderId; Clover receipt and response include surcharge. | Existing `pos_cardpointe_receipts` service uses this endpoint with its own terminal session. Documentary/source mapping; physical printer NOT tested. Odoo receipt extension separately displays subtotal/surcharge/processor total and disclaimer. |
| `/v4/readCard`, `/v4/authManual`, `/v4/readManual` | Alternative documented v4 tokenization/manual-terminal flows; same service-owned disclosure. | DOCUMENTARY ONLY: not invoked by this scoped card-present path. Existing POS gateway iframe manual entry is refused when this program is configured because it has no reviewed program disclosure path. |
| GET `/inquireMerchant/{merchid}` | Merchant enrollment/rate discovery. | Terminal service calls it internally per documentation. Not an Odoo online enrollment writer or a checkbox override. Actual assigned MID capability remains merchant UAT. |
| `/v2/cancel`, `/v2/readSignature`, gateway `sigcap` | Cancel/session/signature policy, not fee calculation. | Existing source paths retained. Unknown outcomes remain referenced; cancel is not evidence a captured charge disappeared. |

The amount credited against the Odoo sale remains its original subtotal. The
processor surcharge is separately labelled receipt evidence; it is not additional
cash tender/change or a manual markup product. This proof does not implement new
settlement accounting, tip adjustment or refund fee allocation. Existing returns
continue using the original reference; no negative authorization was introduced.

## Configuration and privacy

The merchant checkbox is **card-only disclosure/reconciliation configuration**,
not enrollment or a gateway waiver switch. An enrolled merchant must configure it;
unexpected final fee when it is off is held, not silently accepted. Combining it
with PR21's `markup_enabled` (or legacy `fees_active`) refuses before charging.
No manual-module dependency is added. The optional-field guard is fixture-tested
with the optional flag present; a PR21 Odoo18 installation is not claimed.

Never persist quote input token/postal/bank data. Quote binding uses Odoo's keyed
access-token utility; only the digest is stored. GET transport exceptions are
sanitized because requests exceptions can include sensitive query strings.
New receipt fields have explicit schemas; program POS responses omit gateway token.
Read-back through the receipt controller uses the existing transaction-bound access
check, no-store and no-referrer. Fixture data is synthetic and transport patched.

## Executable merchant matrix and evidence classification

All rows below are DOCUMENTED/SOURCE MAPPED. `fixture` is executable Odoo/browser
fixture proof, **not** a call to Fiserv. Exact executed results belong in the shared
build evidence record bound to the candidate SHA.

| Case | Fixture implementation | Gateway/physical |
|---|---|---|
| Online 100 + quote3 -> auth100 -> final103 -> receipt reload | `TestSurcharge.test_first_acceptance_quote_auth_receipt_readback`; public receipt browser read/reload | NOT_PERFORMED |
| Explicit zero, waived, debit, restricted | Same parameterized ORM test; browser QUnit zero-disclosure case | NOT_PERFORMED |
| Malformed/missing/contradictory quote and timeout | `test_invalid_quotes_zero_auth` (zero auth) | NOT_PERFORMED |
| Expiry / changed token, postal, amount | `test_changed_and_expired_quote_blocks_auth` | NOT_PERFORMED |
| Ambiguous auth and repeated submit | `test_ambiguous_auth_inquiry_never_recharges`; QUnit holds original transaction identity, clears raw token | NOT_PERFORMED |
| Quote3 vs final4/104 mismatch | `test_mismatch_remains_held_after_inquiry`; persisted sanitized final response and sticky hold | NOT_PERFORMED |
| Enrolled ACH exclusion / nonprogram / method switching | `TestOnlineACH` enrolled-exclusion and prior PR22 regressions | NOT_PERFORMED |
| Manual/program conflict | Optional-field guard fixture and source mapping to PR21 | NOT_PERFORMED |
| POS eligible + waived0 + ORM receipt reload | `TestTerminalProgram.test_terminal_nonzero_zero_receipt_persistence`; cashier receipt UI fixture | NOT_PERFORMED |
| POS unusable / ambiguous / original inquiry identity / not-found | Two negative `TestTerminalProgram` HTTP fixtures; existing unknown-state JS path | NOT_PERFORMED |
| Terminal own display and physical v3 printed receipt | DOCUMENTARY/SOURCE ONLY; Odoo receipt UI tested separately | NOT_PERFORMED |

Browser online quote tests use Odoo's real payment-form override with the supported
`rpc._rpc` test seam and synthetic DOM; backend transport/ORM is proven separately.
The receipt browser uses the real controller/template over a persisted fixture.
POS browser reaches the pre-authorized synthetic order payment UI, validates it,
reads the processor total/fee/reference on the receipt, and re-reads the paid order
and receipt from ORM. The held-line browser repeats Retry and observes the
missing-reference reconciliation warning without any authorization request.
Earlier scenario failures (missing native UUID fixture identities, collapsed order
tabs and unselected payment line) remain historical failures, not final PASS.
Authorization transport itself is separately fixture-tested.
None of these is a full external hosted-tokenizer or physical-terminal UAT.

## Reproduction (independent reviewer uses OWN phase lab)

Use the already recovered Community18 image/source (70bdd4035000c836f9f459345e45cc4d408c68d8),
not a new runtime recovery. Expose base, online, ACH, POS and receipts from this worktree.

- `odoo-lab test --task <own-phase-lab> --module payment_cardpointe_ach --test-tags /payment_cardpointe,/payment_cardpointe_ach`
- `odoo-lab test --task <own-phase-lab> --module pos_cardpointe_receipts --test-tags /pos_cardpointe_poc,/pos_cardpointe_receipts`
- Install/upgrade those modules in the main browser lab; establish a normal POS user
  with `qa-user-credential --group point_of_sale.group_pos_user --company base.main_company`.
- Run `tests/lab_program_fixture.py` explicitly via fenced Odoo shell (not auto-imported).
  It creates only synthetic fixture records and rejects non-guild DB names.
- Replay the shared evidence browser specs with the phase's own credential references.
  QUnit route `/web/tests/legacy?filter=CardPointeProgram` runs two tests.

## External residual (not a publication blocker)

Owner **client-fb**: supply assigned TEST MID, confirmed surcharge-program
capability, synthetic test-data and gateway/terminal credential REFERENCES, plus
supported terminal device/HSN reference. No such references were supplied. Wake a
separately authorized merchant UAT when they exist; validate all rows at the gateway,
terminal disclosure, physical receipt, enrollment-off/waivers and settlement.
Business UAT/live certification remain NOT_PERFORMED. Exact-head independent review
and reviewed publication/read-back are the same card's subsequent phases, not
self-approved by the producer.
