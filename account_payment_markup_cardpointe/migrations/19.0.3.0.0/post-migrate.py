import logging

from odoo import api, SUPERUSER_ID
from odoo.tools.sql import column_exists

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    if column_exists(cr, 'legacy_payment_method_fee', 'use_cardpointe_program'):
        cr.execute("""
            SELECT f.method_id, p.company_id, array_agg(p.id)
              FROM legacy_payment_method_fee f JOIN payment_provider p ON p.id=f.provider_id
             WHERE f.use_cardpointe_program GROUP BY f.method_id, p.company_id
        """)
    elif column_exists(cr, 'cardpointe_merchant_config', 'surcharge_program'):
        cr.execute("""
            SELECT r.payment_method_id, p.company_id, array_agg(p.id)
              FROM payment_provider p JOIN cardpointe_merchant_config m ON m.id=p.cardpointe_merchant_config_id
              JOIN payment_method_payment_provider_rel r ON r.payment_provider_id=p.id
              JOIN payment_method pm ON pm.id=r.payment_method_id
             WHERE m.surcharge_program AND p.code='cardpointe' AND pm.code='card'
             GROUP BY r.payment_method_id, p.company_id
        """)
    else:
        return
    for method_id, company_id, enrolled in cr.fetchall():
        method = env['payment.method'].browse(method_id).with_company(company_id)
        providers = method.provider_ids.filtered(
            lambda p: p.code == 'cardpointe' and p.company_id.id == company_id and p.state != 'disabled')
        if set(providers.ids).issubset(enrolled):
            method.use_cardpointe_program = True
        else:
            _logger.warning('Mixed CardPointe program settings for method %s in company %s. '
                            'Review the method checkbox for the enrolled merchant.', method_id, company_id)
