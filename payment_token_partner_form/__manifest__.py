# -*- coding: utf-8 -*-
{
    "name": "Internal Tokenization from Contact",
    "version": "16.0.1.0.0",
    "category": "Accounting/Payment",
    "summary": "Let internal users tokenize and save a customer payment method from Contacts.",
    "depends": ["contacts", "payment", "portal", "website"],
    "data": [
        "security/ir.model.access.csv",
        "views/tokenize_partner_payment_method_view.xml",
        "views/res_partner_view.xml",
        "views/payment_token_templates.xml",
    ],
    "license": "LGPL-3",
    "installable": True,
    "application": False,
}
