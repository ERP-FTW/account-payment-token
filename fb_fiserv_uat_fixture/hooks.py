"""Exact, reviewed Full Belly Fiserv UAT setup.

This install hook is intentionally client- and scenario-specific.  It resolves
existing accounting/payment relations without raw database IDs, refuses
ambiguity, creates only the synthetic customer and five invoices when absent,
and posts newly-created invoices through Odoo's native ``action_post`` method.
It never reads, writes, or logs CardPointe credential values and never invokes a
payment or an external service.
"""

from __future__ import annotations

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare


MODULE = "fb_fiserv_uat_fixture"
COMPANY_NAME = "Counter Shop Demo"
CUSTOMER_EMAIL = "fiserv-uat@example.invalid"
CUSTOMER_NAME = "Fiserv UAT synthetic customer"
FEE_PRODUCT_NAME = "Manual checkout fee"
INVOICE_CASES = (
    ("success", "FB-UAT-success", 10.0),
    ("decline", "FB-UAT-decline", 10.0),
    ("markup_on", "FB-UAT-markup_on", 100.0),
    ("markup_off", "FB-UAT-markup_off", 100.0),
    ("token_portable_v3", "FB-UAT-token-portable-v3", 10.0),
)


def _refusal(message):
    raise UserError(f"Full Belly Fiserv UAT fixture refused: {message}")


def _one(records, description):
    if len(records) != 1:
        _refusal(f"{description} must resolve exactly once; observed {len(records)}")
    return records


def _bind(env, name, record):
    """Create one noupdate alias, or prove the existing alias is identical."""
    rows = env["ir.model.data"].sudo().search([
        ("module", "=", MODULE),
        ("name", "=", name),
    ])
    if len(rows) > 1:
        _refusal(f"external ID {MODULE}.{name} is duplicated")
    if rows:
        row = rows[0]
        if row.model != record._name or row.res_id != record.id:
            _refusal(
                f"external ID {MODULE}.{name} already targets another record"
            )
        return
    env["ir.model.data"].sudo().create({
        "module": MODULE,
        "name": name,
        "model": record._name,
        "res_id": record.id,
        "noupdate": True,
    })


def _same_amount(currency, observed, expected):
    return float_compare(
        observed,
        expected,
        precision_rounding=currency.rounding,
    ) == 0


def _company(env):
    companies = env["res.company"].with_context(active_test=False).search([
        ("name", "=", COMPANY_NAME),
        ("currency_id.name", "=", "USD"),
    ])
    return _one(companies, f"USD company named {COMPANY_NAME!r}")


def _partner(env, company):
    partners = env["res.partner"].with_context(active_test=False).search([
        ("email", "=", CUSTOMER_EMAIL),
    ])
    if len(partners) > 1:
        _refusal(
            f"customer email {CUSTOMER_EMAIL!r} is ambiguous ({len(partners)} records)"
        )
    if partners and partners.company_id not in (env["res.company"], company):
        _refusal("the existing synthetic customer belongs to another company")
    return partners


def _accounting_relations(env, company, partner, product):
    move_model = env["account.move"].with_company(company).with_context(
        allowed_company_ids=[company.id],
        default_move_type="out_invoice",
    )
    journal_id = move_model.default_get(["journal_id"]).get("journal_id")
    journal = env["account.journal"].browse(journal_id).exists()
    if journal:
        if journal.company_id != company or journal.type != "sale" or not journal.active:
            _refusal("Odoo's default customer-invoice journal is not an active same-company sales journal")
    else:
        journals = env["account.journal"].search([
            ("company_id", "=", company.id),
            ("type", "=", "sale"),
            ("active", "=", True),
        ])
        journal = _one(journals, "active same-company sales journal")

    product = product.with_company(company)
    if product.type != "service" or not product.active:
        _refusal("the CardPointe markup product must be one active service product")
    if product.company_id and product.company_id != company:
        _refusal("the CardPointe markup product belongs to another company")
    if product.taxes_id:
        _refusal("the CardPointe markup product must have no customer taxes")
    income = product.property_account_income_id or product.categ_id.property_account_income_categ_id
    if not income or income.account_type not in ("income", "income_other"):
        _refusal("the CardPointe markup product has no legitimate income account")
    if company not in income.company_ids:
        _refusal("the CardPointe markup income account is not available to the target company")

    if partner:
        partner_for_company = partner.with_company(company)
        receivable = partner_for_company.property_account_receivable_id
        payable = partner_for_company.property_account_payable_id
        if not receivable or receivable.account_type != "asset_receivable":
            _refusal("the existing synthetic customer has no receivable account")
        if not payable or payable.account_type != "liability_payable":
            _refusal("the existing synthetic customer has no payable account")
    else:
        receivable = payable = env["account.account"]
    return move_model, journal, income, receivable, payable


def _providers(env, company):
    cardpointe = env.ref("payment_cardpointe.payment_provider_cardpointe")
    if cardpointe.code != "cardpointe":
        _refusal("the CardPointe provider external ID resolves to another provider")
    if cardpointe.company_id and cardpointe.company_id != company:
        _refusal("the CardPointe provider belongs to another company")

    merchants = env["cardpointe.merchant.config"].search([
        ("company_id", "=", company.id),
    ])
    merchant = _one(merchants, "same-company CardPointe merchant configuration")
    if (
        cardpointe.cardpointe_merchant_config_id
        and cardpointe.cardpointe_merchant_config_id != merchant
    ):
        _refusal("the CardPointe provider is linked to another merchant configuration")

    product = cardpointe.markup_product_id
    if not product:
        products = env["product.product"].with_context(active_test=False).search([
            ("name", "=", FEE_PRODUCT_NAME),
            ("type", "=", "service"),
            ("company_id", "in", [False, company.id]),
        ])
        product = _one(products, f"service product named {FEE_PRODUCT_NAME!r}")

    demo = env.ref("payment.payment_provider_demo")
    if demo.code != "demo":
        _refusal("the Demo provider external ID resolves to another provider")
    if demo.company_id and demo.company_id != company:
        _refusal("the Demo provider belongs to another company")
    bank = demo.journal_id
    if bank:
        if bank.company_id != company or bank.type != "bank":
            _refusal("the Demo provider journal is not a same-company bank journal")
    else:
        banks = env["account.journal"].search([
            ("company_id", "=", company.id),
            ("type", "=", "bank"),
            ("active", "=", True),
        ])
        bank = _one(banks, "active same-company bank journal for Demo")
    return cardpointe, merchant, product, demo, bank


def _existing_invoices(env, company, partner, income, product):
    """Validate every existing named invoice before the first fixture mutation."""
    found = {}
    for key, ref, amount in INVOICE_CASES:
        moves = env["account.move"].search([
            ("company_id", "=", company.id),
            ("ref", "=", ref),
        ])
        if len(moves) > 1:
            _refusal(f"invoice ref {ref!r} is ambiguous ({len(moves)} records)")
        if not moves:
            found[key] = env["account.move"]
            continue
        move = moves[0]
        if not partner or move.partner_id != partner:
            _refusal(f"existing invoice {ref!r} does not belong to the synthetic customer")
        if move.move_type != "out_invoice" or move.currency_id.name != "USD":
            _refusal(f"existing invoice {ref!r} is not a USD customer invoice")
        if move.state != "posted":
            _refusal(f"existing invoice {ref!r} is not posted; it will not be rewritten")
        if not _same_amount(move.currency_id, move.amount_total, amount):
            _refusal(f"existing invoice {ref!r} has another total")
        if not _same_amount(move.currency_id, move.amount_residual, amount):
            _refusal(
                f"existing invoice {ref!r} is paid, partially paid, or otherwise changed; "
                "preserve it and choose a newly reviewed ref"
            )
        lines = move.invoice_line_ids
        if len(lines) != 1:
            _refusal(f"existing invoice {ref!r} must have exactly one invoice line")
        line = lines[0]
        if line.tax_ids or line.account_id != income:
            _refusal(f"existing invoice {ref!r} is taxed or uses another income account")
        if line.product_id and line.product_id != product:
            _refusal(f"existing invoice {ref!r} uses another product")
        transactions = env["payment.transaction"].search_count([
            ("invoice_ids", "in", move.ids),
        ])
        if transactions:
            _refusal(
                f"existing invoice {ref!r} already has payment transaction history; "
                "preserve it and choose a newly reviewed ref"
            )
        found[key] = move
    return found


def _create_partner(env, company):
    partner = env["res.partner"].with_company(company).create({
        "name": CUSTOMER_NAME,
        "email": CUSTOMER_EMAIL,
        "company_type": "company",
        "company_id": company.id,
    })
    partner_for_company = partner.with_company(company)
    receivable = partner_for_company.property_account_receivable_id
    payable = partner_for_company.property_account_payable_id
    if not receivable or receivable.account_type != "asset_receivable":
        _refusal("the created synthetic customer did not receive a receivable account")
    if not payable or payable.account_type != "liability_payable":
        _refusal("the created synthetic customer did not receive a payable account")
    return partner, receivable, payable


def _create_invoice(move_model, company, partner, journal, product, key, ref, amount):
    move = move_model.create({
        "move_type": "out_invoice",
        "company_id": company.id,
        "partner_id": partner.id,
        "currency_id": company.currency_id.id,
        "journal_id": journal.id,
        "invoice_date": fields.Date.context_today(move_model),
        "ref": ref,
        "invoice_line_ids": [Command.create({
            "name": f"Fiserv UAT {key}",
            "product_id": product.id,
            "quantity": 1.0,
            "price_unit": amount,
            "tax_ids": [Command.clear()],
        })],
    })
    move.action_post()
    if move.state != "posted":
        _refusal(f"new invoice {ref!r} did not reach posted state")
    if not _same_amount(move.currency_id, move.amount_total, amount):
        _refusal(f"new invoice {ref!r} has an unexpected total after posting")
    if not _same_amount(move.currency_id, move.amount_residual, amount):
        _refusal(f"new invoice {ref!r} is not fully due after posting")
    return move


def post_init_hook(env):
    company = _company(env)
    partner = _partner(env, company)
    cardpointe, merchant, product, demo, bank = _providers(env, company)
    move_model, journal, income, receivable, payable = _accounting_relations(
        env, company, partner, product
    )
    invoices = _existing_invoices(env, company, partner, income, product)

    # Every lookup and existing-record check above passed.  Only now may the
    # bounded setup attach missing relations and create missing synthetic data.
    if not cardpointe.cardpointe_merchant_config_id:
        cardpointe.cardpointe_merchant_config_id = merchant
    if not cardpointe.markup_product_id:
        cardpointe.markup_product_id = product
    if not demo.journal_id:
        demo.journal_id = bank

    if not partner:
        partner, receivable, payable = _create_partner(env, company)

    for key, ref, amount in INVOICE_CASES:
        move = invoices[key]
        if not move:
            move = _create_invoice(
                move_model, company, partner, journal, product, key, ref, amount
            )
        invoices[key] = move

    aliases = {
        "company": company,
        "customer": partner,
        "cardpointe_provider": cardpointe,
        "cardpointe_merchant": merchant,
        "fee_product": product,
        "demo_provider": demo,
        "sales_journal": journal,
        "demo_bank_journal": bank,
        "income_account": income,
        "receivable_account": receivable,
        "payable_account": payable,
    }
    aliases.update({f"invoice_{key}": invoices[key] for key, _ref, _amount in INVOICE_CASES})
    for name, record in aliases.items():
        _bind(env, name, record)
