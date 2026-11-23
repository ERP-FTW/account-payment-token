# Account Payment Markup

`account_payment_markup` owns provider-scoped configuration and the shared allocated-base calculation used by `pos_payment_markup`; the POS addon depends on this module and does not duplicate arithmetic.

Configure `payment.provider.markup_enabled`, `markup_percent` (ordinary percentage units), and an existing service `markup_product_id`. The product/company and income account are validated, and configured product taxes are applied explicitly to the fee document. No production product, account, tax, or legal rate is hard-coded.

An explicit trusted processor markup amount, including zero, replaces local calculation. The original base/rate/amount/source/reference and cumulative refund amount are persisted; retries reuse the original decision. Posted-invoice processing links the configured fee invoice into the normal account-payment reconciliation flow and cancels an unposted fee document on cancellation.

Activation with processor evidence is fail-closed: a processor adapter must supply `server_verified`, verified current-instrument credit evidence, and explicit `surcharge_present=False`. Generic labels, unknown/debit/prepaid evidence, and missing surcharge state cannot enable a processor-derived fee. Manual registration uses the explicitly selected configured provider. Provider configuration remains subject to Odoo's existing access rules; this add-on does not grant payment-provider access to invoicing users.

## Tests

Install and run `/account_payment_markup` on vanilla Odoo 16 independently of POS. The native suite includes the arithmetic and source contracts as well as posted fee accounting and evidence-linked cumulative refunds. No processor is contacted. `pos_payment_markup` owns its POS-specific contracts and can be installed afterwards.