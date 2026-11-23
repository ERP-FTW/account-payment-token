# One-time online ACH — Odoo 18

Optional extension of `payment_cardpointe` and `payment_cardpointe_base`. It adds
**Bank account (ACH)** to native online checkout alongside **Card**. This is
source for review/testing, not evidence of installation on a merchant system.

## Configuration and scope

1. Install `payment_cardpointe_ach` (installs the small one-time online card adapter).
   The provider starts disabled. No surcharge or saved-token addon is required.
2. Configure the existing CardPointe merchant configuration for the intended
   company, Fiserv ACH processor and environment. Use the assigned UAT merchant
   credentials and HTTPS Fiserv hosted tokenizer URL; never historical production
   credentials. Select/publish the provider only in the authorized test environment.
3. Select provider payment methods: Card only, Bank account (ACH) only, or both.
4. Supply the **assigned merchant ACH capability evidence reference**, then enable
   only the approved combinations. Consumer checking/savings use `ECHK`/`ESAV`
   with `WEB`; business checking/savings use those account types with `CCD`.
   CCD additionally requires the provider agreement reference and a signed agreement
   reference on the business payer's commercial partner (`cp_ach_ccd_agreement_ref`).
   These are evidence references, not copies of agreements or bank data. An unchecked
   box or missing reference fails closed. A reference is not proof of underwriting.
5. Configure the native payment journal/outstanding-receipts account for accounting
   postprocessing. Review any legal consent text with the merchant before real use.

ACH is USD-only. Routing/account details stay in the Fiserv iframe; select the
matching account class and explicitly authorize the displayed one-time amount.
The server checks that amount against the transaction and retains consent text,
version, time, amount/currency and account class, but no bank account or gateway
payment token. WEB e-commerce submissions carry `achDescription=PURCHASE`.
The tokenizer is configured for the routing/account input mode, with expiry/CVV
inputs disabled. ProfitStars-specific semantics are not implemented here.

## Outcomes and retry safety

- **Accepted**: gateway approval matched to merchant, unique order reference and
  amount, with a nonempty gateway reference; Odoo confirms the payment. This is
  acceptance of the submission, **not confirmation of bank funding**. Native
  invoice reconciliation can show Paid while bank settlement remains outstanding.
- **Declined**: a matched gateway decline becomes an Odoo error, not a funded payment.
- **Unknown**: transport errors, malformed/mismatched replies or an approval without
  a gateway reference remain pending. **Do not start another payment.**

The controller owns a separate cursor and commits the attempt identity before
sending authorization. Retries use `inquireByOrderid` for that original identity;
not-found or failed inquiry never authorizes a new debit. Reload recovery retains
only the Odoo processing identity in session storage, never the hosted payment
 token. Concurrent submissions serialize by document/payer; an unresolved attempt
holds another transaction even if its selected method changes. Changed merchant,
endpoint, currency or amount requires reconciliation rather than resending.

Use the original checkout to inquire. Do not manually cancel/reset an unknown
transaction just to permit another charge. If browser recovery identity is lost,
merchant/operator reconciliation of the original reference is required. There is
no new scheduled reconciliation, settlement, return or bank-funding engine.
Existing base void/refund behavior is unchanged; processor-specific ACH reversal
eligibility is not newly promised by this addon.

## What has been tested

**Fixture-tested, not gateway-tested.** Disposable Odoo 18 Community tests cover:
accepted/declined/unknown outcomes; all four explicitly enabled account classes;
consent, amount, currency and capability negatives; three method configurations;
original-reference inquiry before retry; duplicate invoice/payer holds; changed
merchant/endpoint negatives; transaction-bound access; and private gateway logging.
Browser proof covers card-only, ACH-only, both, missing consent, accepted/declined/
unknown ACH, retry and native invoice portal checkout. A separate independent-cursor
probe verifies durable pre-send identity and one authorization under concurrent retry.

Run the shipped deterministic tests in a disposable Odoo 18 installation:

```sh
odoo-bin -d <disposable-db> -i payment_cardpointe_ach --test-enable \
  --test-tags /payment_cardpointe_ach --stop-after-init --without-demo all
node payment_cardpointe_ach/tests/frontend_boundary.mjs
python3 -m unittest discover -s payment_cardpointe_base/tests -v
```

Tests generate opaque placeholders only in memory; do not replace them with real
bank details, gateway tokens or credentials. The private browser boundary harness
is deliberately not shipped in the merchant addon or public source.

## Remaining UAT and exclusions

The assigned UAT merchant can review/install this source in an authorized disposable Odoo
18 environment, configure the three method combinations, and reproduce the fixture
checks. Assigned merchant capability/entry-code, agreement and safe test-data
references are still needed to verify the **real Fiserv tokenizer and gateway**.
No live gateway compatibility, underwriting, settlement or production result is
claimed. Never capture raw bank details/tokens in logs, fixtures or browser evidence.

A separate configured manual-fee integration test is required once the reviewed
Odoo 18 fee addon is available; standalone ACH is tested without it. If installed,
the displayed/consented/submitted total must agree. This addon does not add fees.
Saved accounts/autopay, POS ACH, production deployment, real-money payments and a
new settlement/return engine are outside this change.
