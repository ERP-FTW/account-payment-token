# CardPointe one-time ACH for Odoo 19

Install this addon and select **Bank account (ACH)** on the CardPointe provider. Enable the account types assigned to the merchant. The hosted form uses the provider’s tokenizer URL in bank-account mode. Checking and savings are supported. Business CCD submissions also require the configured merchant and payer agreement references.

The payer authorizes the displayed one-time USD total. A matched approval confirms submission, a matched decline leaves the payment in error, and an unknown result is inquired using the original order reference. Bank funding is confirmed separately.
