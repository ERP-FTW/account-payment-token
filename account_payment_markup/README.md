# Account Payment Markup — Odoo 18

LGPL-3. This port retains the original module attribution in the manifest.

## Manual inbound payments

Configure a bank/cash journal's inbound payment method line with **Disabled**, **Percentage**, or **Fixed amount**, an existing service fee product and the fee's tax-included interpretation. Percentage `3` means 3%, not 0.03. Fixed amounts are in the payment currency and apply once per actual payment, including a grouped payment settling multiple invoices. Ordinary and ACH methods do not need a payment provider or merchant MID. Default manual mode is disabled. Outbound payments are not charged by this configuration.

The fee product needs the intended income account and taxes. Native Odoo tax calculation, grouping and reconciliation are retained. Payment and journal-entry fee evidence is persisted; refunds use original evidence and enforce the original cumulative cap rather than the current fee settings.

Legacy explicitly selected provider configuration remains available separately; method-level manual configuration takes precedence, so these paths do not stack.

## Optional CardPointe program boundary

**Use CardPointe surcharge program** is available only when the provider code is `cardpointe` and its supported payment methods include CARD (`card`). ACH-only and ordinary providers cannot enable it. The switch requires verified trusted credit-instrument evidence and does not itself enroll a merchant, contact a processor, or implement a gateway. Debit, ACH, unknown instruments and missing evidence are ineligible; an eligible authoritative zero remains zero. Local POS fallback is disabled for program-enabled provider mappings to prevent an unverified second fee.

Gateway modules remain independent: this module depends only on `account` and `payment`. A real merchant program and its adapter still require separate certification. Neither merchant certification nor business-user UAT is claimed by synthetic regression tests.

## Verification

Install independently of POS on Odoo 18 and run native test tags `/account_payment_markup`. Tests include percentage/fixed/manual and synthetic ACH methods, grouped invoices, tax rounding, original-evidence refunds, cancellation and transaction evidence contracts. No processor is contacted. Install `pos_payment_markup` separately for cashier flows.
