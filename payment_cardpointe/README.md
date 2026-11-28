# CardPointe online checkout for Odoo 19

Select the CardPointe merchant configuration on the payment provider, select the Card payment method, and set the provider to Test Mode for sandbox testing. On the portal invoice, choose Pay Now and enter the test card in the hosted CardPointe frame.

Odoo creates the payment transaction. The adapter submits its amount once, records the gateway reference, and marks the transaction done on approval or in error on decline. An unanswered authorization stays pending. A retry of that transaction looks up the original gateway order. The merchant credential and provider connection buttons check the assigned gateway without making a payment.
