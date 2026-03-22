from odoo import SUPERUSER_ID, api

from . import models
from . import controllers


def _column_exists(cr, table_name, column_name):
    cr.execute(
        """
        SELECT 1
          FROM information_schema.columns
         WHERE table_name = %s
           AND column_name = %s
        """,
        (table_name, column_name),
    )
    return bool(cr.fetchone())


def _read_legacy_value(cr, record_id, column, default=''):
    cr.execute(f"SELECT {column} FROM pos_cardpointe_terminal_config WHERE id = %s", (record_id,))
    row = cr.fetchone()
    return (row and row[0]) or default


def post_init_hook(cr, registry):
    env = api.Environment(cr, SUPERUSER_ID, {})
    terminal_config_obj = env['pos.cardpointe.terminal.config']
    merchant_config_obj = env['cardpointe.merchant.config']

    has_legacy_mid = _column_exists(cr, 'pos_cardpointe_terminal_config', 'merchant_id')
    has_legacy_gateway_base = _column_exists(cr, 'pos_cardpointe_terminal_config', 'gateway_base_url')
    has_legacy_gateway_user = _column_exists(cr, 'pos_cardpointe_terminal_config', 'gateway_username')
    has_legacy_gateway_pass = _column_exists(cr, 'pos_cardpointe_terminal_config', 'gateway_password')

    for terminal_config in terminal_config_obj.search([]):
        if terminal_config.merchant_config_id:
            continue

        legacy_mid = terminal_config.merchant_id
        gateway_base = 'https://fts-uat.cardconnect.com/cardconnect/rest/'
        gateway_user = ''
        gateway_pass = ''

        if has_legacy_mid:
            legacy_mid = _read_legacy_value(cr, terminal_config.id, 'merchant_id', legacy_mid)
        if has_legacy_gateway_base:
            gateway_base = _read_legacy_value(cr, terminal_config.id, 'gateway_base_url', gateway_base)
        if has_legacy_gateway_user:
            gateway_user = _read_legacy_value(cr, terminal_config.id, 'gateway_username', gateway_user)
        if has_legacy_gateway_pass:
            gateway_pass = _read_legacy_value(cr, terminal_config.id, 'gateway_password', gateway_pass)

        existing = merchant_config_obj.search([
            ('company_id', '=', terminal_config.company_id.id),
            ('mid', '=', legacy_mid),
            ('gateway_base_url', '=', gateway_base),
            ('gateway_username', '=', gateway_user),
        ], limit=1)

        if not existing:
            existing = merchant_config_obj.create({
                'name': f"{terminal_config.company_id.name} / {legacy_mid or 'CardPointe'}",
                'company_id': terminal_config.company_id.id,
                'mid': legacy_mid or 'unknown',
                'gateway_base_url': gateway_base,
                'gateway_username': gateway_user or 'missing',
                'gateway_password': gateway_pass or 'missing',
            })

        terminal_config.merchant_config_id = existing.id
