"""Disposable-lab route fixture; processor transport never leaves this process.

Invoke with odoo-lab shell, then feed the emitted ROUTE_RESULT to the JS
consumer regression and persist that consumer's output via persist_consumer().
"""
import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from odoo.addons.pos_cardpointe_poc.controllers import main as base_controller
from odoo.addons.pos_cardpointe_poc_tipping.controllers import main as tipping_controller
from odoo.addons.pos_cardpointe_poc.tests.cardpointe_fake import FakeCardPointe


def prepare(env):
    assert env.cr.dbname.startswith('guild_'), 'Disposable lab only'
    env.company.write({'country_id': env.ref('base.us').id, 'currency_id': env.ref('base.USD').id})
    merchant = env['cardpointe.merchant.config'].create({
        'name': 'Synthetic tipping program', 'mid': 'LAB-NONROUTABLE',
        'surcharge_program': True, 'gateway_base_url': 'https://example.invalid/cardconnect/rest/',
    })
    terminal = env['pos.cardpointe.terminal.config'].create({
        'name': 'Synthetic tipping terminal', 'merchant_config_id': merchant.id,
        'device_serial': 'LAB-NONROUTABLE', 'auth_key': 'fixture-only',
        'signature_mode': 'never', 'enable_tips': False,
    })
    bank = env['account.journal'].search([('type', '=', 'bank')], limit=1)
    pm = env['pos.payment.method'].create({
        'name': 'Synthetic program card', 'journal_id': bank.id,
        'use_payment_terminal': 'cardpointe_poc', 'cardpointe_config_id': terminal.id,
    })
    config = env['pos.config'].create({
        'name': 'Synthetic program POS', 'payment_method_ids': [(6, 0, pm.ids)],
        'cash_control': False, 'iface_print_auto': False,
    })
    cashier = env['res.users'].search([('login', '=like', 'lab_sales_%')], limit=1)
    assert cashier and not cashier.has_group('base.group_system')
    cashier.partner_id.email = 'cashier@example.invalid'
    config.with_user(cashier).open_ui()
    config.current_session_id.with_user(cashier).set_opening_control(0, 'Synthetic fixture')
    product = env['product.product'].create({
        'name': 'Synthetic program item', 'list_price': 100, 'available_in_pos': True,
        'type': 'consu', 'taxes_id': [(5, 0, 0)],
    })
    order = env['pos.order'].create({
        'session_id': config.current_session_id.id, 'uuid': str(uuid4()),
        'pos_reference': 'SYNTHETIC-TIPPING-RECEIPT',
        'floating_order_name': 'Synthetic approved receipt',
        'amount_tax': 0, 'amount_total': 100, 'amount_paid': 0, 'amount_return': 0,
        'lines': [(0, 0, {'uuid': str(uuid4()), 'product_id': product.id,
                         'full_product_name': product.display_name, 'qty': 1, 'price_unit': 100,
                         'price_subtotal': 100, 'price_subtotal_incl': 100, 'tax_ids': [(5, 0, 0)]})],
    })
    fake = FakeCardPointe()
    fake.auth_card.update(amount='103.00', fee_amount='3.00', fee_type='SURCHRG',
                          merchid=merchant.mid, retref='LAB-POS-RECEIPT')
    request = SimpleNamespace(env=env(user=cashier))
    with patch.object(base_controller, 'request', request), patch.object(tipping_controller, 'request', request), patch(
        'odoo.addons.pos_cardpointe_poc.services.cardpointe_terminal.request_json', side_effect=fake
    ):
        controller = tipping_controller.PosCardPointeTippingController()
        started = controller.start(config.id, pm.id, 100, 'USD', order.uuid, str(uuid4()))
        assert started['status'] == 'ready', started
        fake.auth_card['orderid'] = started['terminal_order_id']
        result = controller.auth(started['request_id'], amount=100)
    assert result['status'] == 'approved' and result['cardpointe_tip_prompted'] is False, result
    assert len(fake.paths('v4/authCard')) == 1
    assert not fake.paths('v3/tip')
    env.cr.commit()
    print('ROUTE_RESULT=' + json.dumps(result))
    return result


def persist_consumer(env, consumer):
    assert env.cr.dbname.startswith('guild_'), 'Disposable lab only'
    order = env['pos.order'].search([('pos_reference', '=', 'SYNTHETIC-TIPPING-RECEIPT')])
    assert len(order) == 1 and not order.payment_ids
    pm = order.config_id.payment_method_ids
    values = dict(consumer['payment'])
    values.update(pos_order_id=order.id, payment_method_id=pm.id, uuid=str(uuid4()))
    env['pos.payment'].create(values)
    order.write({'amount_paid': consumer['tender'], 'amount_return': consumer['change']})
    env.flush_all()
    env.cr.commit()
    env.invalidate_all()
    order = env['pos.order'].browse(order.id)
    assert order.amount_paid == order.payment_ids.amount == 100
    assert order.amount_return == 0
    assert order.payment_ids.cardpointe_program_receipt['fee'] == '3.00'
    assert order.payment_ids.cardpointe_program_receipt['total'] == '103.00'
    print('CONSUMER_PERSISTENCE=PASS')
