# Tipping + program regression (Odoo 18)

All responses are synthetic. These tests do not contact a payment gateway.

1. Install `pos_cardpointe_poc_tipping` and run native tags
   `/pos_cardpointe_poc_tipping:TestTippingProgram,/pos_cardpointe_poc`.
   The explicit wrapper cases rerun the base program cases under the tipping
   addon's installed HTTP override, including tips disabled, zero/nonzero fee,
   malformed fee, decline, timeout, inquiry recovery and foreign identity.
2. In a disposable `guild_*` lab with a provisioned ordinary `lab_sales_*`
   cashier, invoke `tests.lab_tipping_fixture.prepare(env)` in Odoo shell.
   Save the emitted `ROUTE_RESULT` JSON as `route.json`. This invokes the
   actual start/auth controller methods with the same cashier environment;
   only the HTTP processor transport is replaced. The native tests above
   independently cover HTTP dispatch. Tips are disabled and the order has
   **no payment** at this point.
3. Run:

       node pos_cardpointe_poc_tipping/tests/consumer_regression.mjs \
           <addon-root> <exact-odoo18-source-root> route.json

   This executes the unchanged base and tipping JS response consumers,
   Odoo's native payment setters, patch utility, rounding and receipt
   exporter on that result. Only the framework storage/services are test
   doubles; this is not represented as a full POS browser test. Tender is
   initialized to zero, then **derived** as 100 with fee 3 and total 103.
   Removing `program_receipt` is a negative control and derives tender103,
   reproducing the reported defect rather than pre-seeding a passing value.
4. Pass the JSON output to `persist_consumer(env, output)` in the same
   fixture module. The actual consumer output supplies the payment amount
   and receipt; commit/invalidate/read asserts persisted100/3/103/change0.
5. As the ordinary cashier open **Synthetic program POS**, click Payment,
   then Validate. Assert the visible processor total103, surcharge3 and
   `LAB-POS-RECEIPT`. Read the finalized order and payment in a fresh Odoo
   shell: order paid100, payment100, return0, receipt3/103. This last step
   proves actual native POS finalization did not convert the fee to change.

The disposable fixture is explicit, never imported at module installation,
contains only non-routable synthetic identifiers, and must not run on a
merchant database. No merchant/device UAT is implied.
