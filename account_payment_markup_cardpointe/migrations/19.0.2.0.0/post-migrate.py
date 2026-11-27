from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    """Only the old merchant flag actually enabled quoting at the gateway."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    cr.execute("""
        SELECT p.id, m.surcharge_program, p.markup_use_cardpointe_program
          FROM payment_provider p
          LEFT JOIN cardpointe_merchant_config m ON m.id = p.cardpointe_merchant_config_id
         WHERE p.code = 'cardpointe' AND (m.surcharge_program OR p.markup_use_cardpointe_program)
    """)
    for provider_id, program_active, legacy_checkbox in cr.fetchall():
        provider = env['payment.provider'].browse(provider_id)
        card = provider.with_context(active_test=False).payment_method_ids.filtered(lambda m: m.code == 'card')
        if not card:
            continue
        fee = card._markup_fee(provider)
        if program_active:
            if fee:
                fee.use_cardpointe_program = True
            else:
                env['payment.method.fee'].with_company(provider.company_id).create({
                    'provider_id': provider.id, 'method_id': card.id, 'use_cardpointe_program': True})
        elif legacy_checkbox and fee:
            fee.markup_percent = 0
