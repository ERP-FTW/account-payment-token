from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    """Retain the existing rate on the methods previously enabled by each provider."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    cr.execute("""
        SELECT id, markup_percent, markup_product_id, markup_tax_included
          FROM payment_provider
         WHERE markup_enabled AND markup_percent > 0
    """)
    for provider_id, rate, product_id, tax_included in cr.fetchall():
        provider = env['payment.provider'].browse(provider_id)
        fees = env['payment.method.fee'].with_company(provider.company_id)
        for method in provider.with_context(active_test=False).payment_method_ids:
            if not method._markup_fee(provider):
                fees.create({'provider_id': provider.id, 'method_id': method.id,
                             'markup_percent': rate, 'markup_product_id': product_id,
                             'markup_tax_included': tax_included})
