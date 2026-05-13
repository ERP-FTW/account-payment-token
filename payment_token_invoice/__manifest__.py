{
    "name": "Invoice Token Payment Wizard",
    "summary": "Charge customer invoices with saved payment tokens from the backend.",
    "version": "16.0.2.0.0",
    "category": "Accounting/Accounting",
    "license": "LGPL-3",
    "author": "ERP-FTW - Tech Sovereigns",
    "depends": [
        "account",
        "payment",
        "payment_token_base",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/account_invoice_token_views.xml"
    ],
    "installable": True,
    "application": False
}
