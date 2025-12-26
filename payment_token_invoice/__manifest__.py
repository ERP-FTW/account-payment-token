{
    "name": "Invoice Token Payment Wizard",
    "summary": "Charge customer invoices with saved payment tokens from the backend.",
    "version": "18.0.1.0.0",
    "category": "Accounting/Accounting",
    "license": "LGPL-3",
    "author": "Your Company",
    "depends": [
        "account",
        "payment"
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/account_invoice_token_views.xml"
    ],
    "installable": True,
    "application": False
}
