# CardPointe one-time ACH for Odoo 19

Select **Bank account (ACH)** on the CardPointe provider and use a merchant account enabled for eCheck. The hosted form uses the configured tokenizer URL in bank-account mode. At checkout the payer chooses consumer or business checking or savings and authorizes the displayed one-time USD total.

Odoo records the gateway response on the payment transaction. If the result is unknown, a repeat request looks up the original gateway order. Gateway approval confirms submission. Bank funding is a separate step.
