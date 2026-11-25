"""Read-only proof of the installed Full Belly Fiserv fixture module."""

import json


assert env.cr.dbname.startswith("guild_"), "DISPOSABLE ODOO LAB ONLY"
company = env.ref("fb_fiserv_uat_fixture.company")
partner = env.ref("fb_fiserv_uat_fixture.customer")
cardpointe = env.ref("fb_fiserv_uat_fixture.cardpointe_provider")
merchant = env.ref("fb_fiserv_uat_fixture.cardpointe_merchant")
product = env.ref("fb_fiserv_uat_fixture.fee_product")
demo = env.ref("fb_fiserv_uat_fixture.demo_provider")
expected = {
    "success": ("FB-UAT-success", 10.0),
    "decline": ("FB-UAT-decline", 10.0),
    "markup_on": ("FB-UAT-markup_on", 100.0),
    "markup_off": ("FB-UAT-markup_off", 100.0),
    "token_portable_v3": ("FB-UAT-token-portable-v3", 10.0),
}
rows = []
for key, (ref, amount) in expected.items():
    move = env.ref(f"fb_fiserv_uat_fixture.invoice_{key}")
    assert move.company_id == company
    assert move.partner_id == partner
    assert move.ref == ref
    assert move.state == "posted"
    assert move.currency_id.name == "USD"
    assert move.amount_total == amount
    assert move.amount_residual == amount
    assert len(move.invoice_line_ids) == 1
    assert not move.invoice_line_ids.tax_ids
    assert not env["payment.transaction"].search_count([
        ("invoice_ids", "in", move.ids),
    ])
    rows.append({
        "external_id": f"fb_fiserv_uat_fixture.invoice_{key}",
        "ref": ref,
        "total": move.amount_total,
        "residual": move.amount_residual,
        "state": move.state,
    })
assert company.name == "Counter Shop Demo" and company.currency_id.name == "USD"
assert partner.email == "fiserv-uat@example.invalid"
assert cardpointe.cardpointe_merchant_config_id == merchant
assert cardpointe.markup_product_id == product
assert demo.journal_id == env.ref("fb_fiserv_uat_fixture.demo_bank_journal")
assert env["cardpointe.merchant.config"].search_count([
    ("company_id", "=", company.id),
]) == 1
print("FIXTURE_PROOF=" + json.dumps({
    "status": "PASS",
    "database": env.cr.dbname,
    "module": "fb_fiserv_uat_fixture",
    "company": company.name,
    "customer": partner.email,
    "invoice_count": len(rows),
    "invoices": rows,
    "relations": {
        "cardpointe_merchant": True,
        "markup_product": True,
        "demo_bank_journal": True,
    },
    "payment_transactions": 0,
    "external_payment_effect": False,
}, sort_keys=True))
