# CardPointe online payments — Odoo 19

Ported from the Odoo 18 addon; uses Odoo 19's native invoice portal and PaymentForm Interaction. Install `payment_cardpointe` (depends on `payment_cardpointe_base` and `account_payment`). Existing token addons remain unchanged.

## Hosted form configuration

Enable online invoice payments in Accounting, configure a CardPointe merchant configuration with its HTTPS hosted tokenizer URL, assign it to the CardPointe provider and publish the provider with the card method enabled. For a rendering-only disposable lab, use test state, an explicitly non-routable synthetic MID and no Gateway credentials. A hosted iframe must load the actual processor document; an empty iframe is not a successful test. Do not enter card data or submit a payment during rendering-only checks.

Gateway credentials are separately required for payment processing. Rendering the form proves neither authorization nor merchant readiness. Existing ACH and markup extensions are separate modules; this port retains their hooks without installing them. Existing surcharge behavior and its required shared base helpers are preserved, not redesigned.

## Version adaptation

- Frontend: patch `PaymentForm.prototype` from `@payment/interactions/payment_form`, use native `super`, retain token listener cleanup.
- HTTP: Odoo 19 `jsonrpc` route type.
- ORM: `models.Constraint` uniqueness and keyword-only state messages.
- JS regression suite: HOOT `web.assets_unit_tests` and `.test.js` discovery.

Run native tests with `--test-tags /payment_cardpointe`. They use synthetic transport only. The HOOT suite covers quote confirmation and inquiry recovery. No production or client database is touched by these tests.
