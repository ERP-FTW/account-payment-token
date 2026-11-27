# Manual payment fees for Odoo 19

For online checkout, open a **Payment Method → Payment fees**, select its provider and enter the manual percentage. The fee is zero until configured. The fee product supplies the income account and taxes. The same calculation supplies the amount shown at checkout and the amount sent for payment.

For **Register Payment**, set the fee on the journal’s inbound payment method line. Choose disabled, percentage or fixed amount. Selecting a disabled line leaves the payment at its original amount.

Existing posted entries retain their amounts. Refunds use the original payment’s fee. On upgrade, existing provider rates are copied to that provider’s enabled methods.
