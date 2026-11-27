# CardPointe online checkout for Odoo 19

Select the CardPointe merchant configuration on the payment provider, select the Card payment method, and set the provider to Test Mode for sandbox testing. On the portal invoice, choose Pay Now and enter the test card in the hosted CardPointe frame.

The adapter sends a one-time payment and confirms the transaction after the gateway returns a matching approval. After a timeout it inquires using the original order reference. The merchant credential and provider connection buttons check the assigned gateway without making a payment.
