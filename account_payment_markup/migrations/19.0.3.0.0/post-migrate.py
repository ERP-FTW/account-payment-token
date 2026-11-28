import logging
from collections import defaultdict

from odoo import api, SUPERUSER_ID
from odoo.tools.sql import table_exists

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Older versions attached their draft fee invoice only during post-processing.
    cr.execute("""
        INSERT INTO account_invoice_transaction_rel (transaction_id, invoice_id)
        SELECT id, markup_fee_move_id FROM payment_transaction
         WHERE state='done' AND NOT is_post_processed AND markup_fee_move_id IS NOT NULL
        ON CONFLICT DO NOTHING
    """)
    if not table_exists(cr, 'legacy_payment_method_fee'):
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    cr.execute("""
        SELECT f.method_id, p.company_id, f.provider_id, f.markup_percent, f.markup_product_id,
               COALESCE((to_jsonb(f)->>'use_cardpointe_program')::boolean, false)
          FROM legacy_payment_method_fee f JOIN payment_provider p ON p.id=f.provider_id
    """)
    settings = defaultdict(dict)
    for method, company, provider, rate, product, program in cr.fetchall():
        settings[method, company][provider] = None if program else (rate or 0, product)
    for (method_id, company_id), rows in settings.items():
        company = env['res.company'].browse(company_id)
        method = env['payment.method'].browse(method_id).with_company(company)
        active_providers = method.provider_ids.filtered(lambda p: p.company_id == company and p.state != 'disabled')
        options = {rows.get(pid, (0, None)) for pid in set(rows) | set(active_providers.ids)}
        choices = {(rate, product) if rate else (0, None) for rate, product in (o for o in options if o is not None)}
        if len(choices) == 1:
            rate, product = choices.pop()
            if rate and product:
                method.write({'markup_percent': rate, 'markup_product_id': product})
        elif len(choices) > 1:
            _logger.warning('Payment method %s, company %s had different provider fees. '
                            'Choose the shared manual rate on the method. Prior settings are preserved '
                            'in legacy_payment_method_fee.', method_id, company_id)
