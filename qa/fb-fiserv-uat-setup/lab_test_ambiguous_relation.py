"""Runtime proof that ambiguous merchant resolution refuses before mutation.

Run only in a disposable ``odoo-lab`` database after
``lab_prepare_prerequisites.py`` and before installing the fixture addon.  The
script creates one temporary non-routable duplicate relation, proves the hook
refuses while every ORM create/write entry point is guarded, removes the
ambiguity, and leaves the lab ready for the normal install proof.
"""

import json
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.models import BaseModel

from odoo.addons.fb_fiserv_uat_fixture.hooks import post_init_hook


assert env.cr.dbname.startswith("guild_"), "DISPOSABLE ODOO LAB ONLY"
company = env.company
merchant_model = env["cardpointe.merchant.config"]
provider = env.ref("payment_cardpointe.payment_provider_cardpointe")
demo = env.ref("payment.payment_provider_demo")
invoice_refs = [
    "FB-UAT-success",
    "FB-UAT-decline",
    "FB-UAT-markup_on",
    "FB-UAT-markup_off",
    "FB-UAT-token-portable-v3",
]

original_merchants = merchant_model.search([
    ("company_id", "=", company.id),
])
assert len(original_merchants) == 1, "expected one prepared proof merchant"
duplicate = merchant_model.create({
    "name": "Fiserv ambiguous proof merchant",
    "company_id": company.id,
    "mid": "PROOF-AMBIGUOUS-NONROUTABLE",
    "gateway_base_url": "https://example.invalid/cardconnect/rest/",
    "tokenizer_url": "https://example.invalid/tokenizer",
    "surcharge_program": False,
})
env.cr.commit()


def snapshot():
    return {
        "merchant_count": merchant_model.search_count([
            ("company_id", "=", company.id),
        ]),
        "provider_merchant_id": provider.cardpointe_merchant_config_id.id or False,
        "provider_markup_product_id": provider.markup_product_id.id or False,
        "demo_journal_id": demo.journal_id.id or False,
        "customer_count": env["res.partner"].search_count([
            ("email", "=", "fiserv-uat@example.invalid"),
        ]),
        "invoice_count": env["account.move"].search_count([
            ("company_id", "=", company.id),
            ("ref", "in", invoice_refs),
        ]),
        "fixture_alias_count": env["ir.model.data"].sudo().search_count([
            ("module", "=", "fb_fiserv_uat_fixture"),
        ]),
        "payment_transaction_count": env["payment.transaction"].search_count([]),
    }


before = snapshot()
refusal = None
with patch.object(
    BaseModel,
    "create",
    side_effect=AssertionError("fixture attempted ORM create before ambiguity refusal"),
) as create_guard, patch.object(
    BaseModel,
    "write",
    side_effect=AssertionError("fixture attempted ORM write before ambiguity refusal"),
) as write_guard:
    try:
        post_init_hook(env)
    except UserError as exc:
        refusal = str(exc)
    else:
        raise AssertionError("ambiguous merchant relation was not refused")
    assert create_guard.call_count == 0, "fixture attempted create before refusal"
    assert write_guard.call_count == 0, "fixture attempted write before refusal"

assert "same-company CardPointe merchant configuration must resolve exactly once" in refusal
assert "observed 2" in refusal
assert snapshot() == before, "fixture state changed before ambiguity refusal"

duplicate.unlink()
env.cr.commit()
assert merchant_model.search_count([("company_id", "=", company.id)]) == 1
assert snapshot()["invoice_count"] == 0

print("AMBIGUITY_PROOF=" + json.dumps({
    "status": "PASS",
    "database": env.cr.dbname,
    "relation": "same-company CardPointe merchant configuration",
    "observed_records": 2,
    "refusal": refusal,
    "orm_create_calls_before_refusal": 0,
    "orm_write_calls_before_refusal": 0,
    "fixture_state_unchanged": True,
    "temporary_duplicate_removed": True,
    "external_payment_effect": False,
}, sort_keys=True))
