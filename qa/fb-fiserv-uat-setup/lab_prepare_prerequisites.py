"""Disposable proof-lab prerequisites for t_7179e09b only.

This is not part of the candidate module and must never run against a client
database.  It creates non-routable accounting/provider prerequisites but leaves
all five invoices absent so the reviewed fixture module must create and post
those records itself.
"""

import json

from odoo import Command


assert env.cr.dbname == "guild_t_7179e09b", "PROOF LAB ONLY"
company = env.company
company.write({
    "name": "Counter Shop Demo",
    "currency_id": env.ref("base.USD").id,
    "country_id": env.ref("base.us").id,
})


def record(name, model, values):
    xmlid = f"fb_fiserv_uat_fixture_proof.{name}"
    found = env.ref(xmlid, raise_if_not_found=False)
    if found:
        assert found._name == model
        return found
    created = env[model].create(values)
    env["ir.model.data"].create({
        "module": "fb_fiserv_uat_fixture_proof",
        "name": name,
        "model": model,
        "res_id": created.id,
        "noupdate": True,
    })
    return created


receivable = record("receivable", "account.account", {
    "name": "Fiserv proof receivable",
    "code": "FP1100",
    "account_type": "asset_receivable",
    "reconcile": True,
    "company_ids": [Command.set(company.ids)],
})
payable = record("payable", "account.account", {
    "name": "Fiserv proof payable",
    "code": "FP2100",
    "account_type": "liability_payable",
    "reconcile": True,
    "company_ids": [Command.set(company.ids)],
})
income = record("income", "account.account", {
    "name": "Fiserv proof income",
    "code": "FP4000",
    "account_type": "income",
    "company_ids": [Command.set(company.ids)],
})
sale_journals = env["account.journal"].search([
    ("company_id", "=", company.id),
    ("type", "=", "sale"),
    ("active", "=", True),
])
if len(sale_journals) == 1:
    sales = sale_journals
    if not env.ref("fb_fiserv_uat_fixture_proof.sales_journal", raise_if_not_found=False):
        env["ir.model.data"].create({
            "module": "fb_fiserv_uat_fixture_proof",
            "name": "sales_journal",
            "model": "account.journal",
            "res_id": sales.id,
            "noupdate": True,
        })
elif not sale_journals:
    sales = record("sales_journal", "account.journal", {
        "name": "Fiserv proof sales",
        "code": "FPSL",
        "type": "sale",
        "company_id": company.id,
        "default_account_id": income.id,
    })
else:
    raise AssertionError(f"proof requires at most one existing sales journal; observed {len(sale_journals)}")
bank = record("bank_journal", "account.journal", {
    "name": "Fiserv proof bank",
    "code": "FPBK",
    "type": "bank",
    "company_id": company.id,
})
customer = record("customer", "res.partner", {
    "name": "Fiserv UAT synthetic customer",
    "email": "fiserv-uat@example.invalid",
    "company_id": company.id,
    "property_account_receivable_id": receivable.id,
    "property_account_payable_id": payable.id,
})
product = record("fee_product", "product.product", {
    "name": "Manual checkout fee",
    "type": "service",
    "property_account_income_id": income.id,
    "taxes_id": [Command.clear()],
})
merchant = record("merchant", "cardpointe.merchant.config", {
    "name": "Fiserv proof merchant",
    "company_id": company.id,
    "mid": "PROOF-NONROUTABLE",
    "gateway_base_url": "https://example.invalid/cardconnect/rest/",
    "tokenizer_url": "https://example.invalid/tokenizer",
    "surcharge_program": False,
})
cardpointe = env.ref("payment_cardpointe.payment_provider_cardpointe")
cardpointe.write({
    "company_id": company.id,
    "cardpointe_merchant_config_id": False,
    "markup_product_id": False,
    "allow_tokenization": False,
})
demo = env.ref("payment.payment_provider_demo")
demo.write({
    "company_id": company.id,
    "journal_id": False,
    "state": "test",
    "is_published": True,
    "allow_tokenization": True,
})
assert not env["account.move"].search_count([
    ("ref", "in", [
        "FB-UAT-success",
        "FB-UAT-decline",
        "FB-UAT-markup_on",
        "FB-UAT-markup_off",
        "FB-UAT-token-portable-v3",
    ]),
])
env.cr.commit()
print("PROOF_PREREQUISITES=" + json.dumps({
    "database": env.cr.dbname,
    "company": company.name,
    "customer": customer.email,
    "merchant_count": env["cardpointe.merchant.config"].search_count([
        ("company_id", "=", company.id),
    ]),
    "product": product.name,
    "sales_journal": sales.code,
    "bank_journal": bank.code,
    "invoices_before": 0,
    "external_payment_effect": False,
}, sort_keys=True))
