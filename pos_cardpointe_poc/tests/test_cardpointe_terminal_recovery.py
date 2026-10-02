import re

from odoo import Command
from odoo.tests import tagged

from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon

from .cardpointe_fake import TIMEOUT, FakeCardPointe


@tagged('post_install', '-at_install', 'cardpointe')
class TestCardPointeTerminalRecovery(TestPointOfSaleHttpCommon):
    """Terminal sales whose authCard got no answer, through the routes the POS calls."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.main_pos_config.company_id
        cls.merchant = cls.env['cardpointe.merchant.config'].create({
            'name': 'Test merchant', 'company_id': company.id, 'mid': '496160873888',
            'gateway_base_url': 'https://fts-uat.cardconnect.com/cardconnect/rest/',
            'gateway_username': 'testing', 'gateway_password': 'testing-SECRET',
        })
        cls.terminal = cls.env['pos.cardpointe.terminal.config'].create({
            'name': 'Counter terminal', 'company_id': company.id, 'merchant_config_id': cls.merchant.id,
            'auth_key': 'terminal-key-SECRET', 'device_serial': 'C032UQ03960574',
            'signature_mode': 'never',
        })
        cls.cardpointe_pm = cls.env['pos.payment.method'].create({
            'name': 'CardPointe', 'journal_id': cls.bank_journal.id,
            'use_payment_terminal': 'cardpointe_poc', 'cardpointe_config_id': cls.terminal.id,
            'company_id': company.id,
        })
        cls.main_pos_config.write({'payment_method_ids': [Command.link(cls.cardpointe_pm.id)]})

    def setUp(self):
        super().setUp()
        self.fake = FakeCardPointe()
        self.fake.patch_transport(self)
        self.authenticate('pos_user', 'pos_user')

    def _start(self, amount=42.5):
        return self.make_jsonrpc_request('/pos_cardpointe_poc/start', {
            'pos_config_id': self.main_pos_config.id, 'payment_method_id': self.cardpointe_pm.id,
            'amount': amount, 'currency': 'USD', 'order_uid': '7f0c1d9e-1111-4c2a-9a5e-0123456789ab',
            'payment_line_uuid': 'line-1',
        })

    def _pay(self, amount=42.5):
        started = self._start(amount)
        self.assertEqual(started['status'], 'ready', started)
        result = self.make_jsonrpc_request('/pos_cardpointe_poc/auth', {
            'request_id': started['request_id'], 'amount': amount,
        })
        return started, result

    def test_terminal_order_id_follows_the_documented_format(self):
        started, result = self._pay()
        self.assertEqual(result['status'], 'approved')
        order_id = self.fake.paths('v4/authCard')[0]['json']['orderId']
        self.assertRegex(order_id, re.compile(r'^[A-Za-z0-9]{1,19}$'))
        self.assertEqual(order_id, started.get('terminal_order_id'))

    def test_timeout_resolved_as_approved_without_charging_again(self):
        self.fake.auth_card_error = TIMEOUT
        self.fake.inquire_by_orderid = {'respstat': 'A', 'respcode': '00', 'resptext': 'Approval',
                                        'retref': 'RECOVERED-1', 'authcode': 'Z9', 'amount': '42.50',
                                        'setlstat': 'Queued for Capture'}
        started, result = self._pay()
        self.assertEqual(result['status'], 'approved', result)
        self.assertEqual(result['retref'], 'RECOVERED-1')
        self.assertTrue(result['recovered'])
        self.assertEqual(len(self.fake.paths('v4/authCard')), 1, "never a second charge")
        inquiry = self.fake.paths('inquireByOrderid')[0]['path']
        self.assertIn(started['terminal_order_id'], inquiry)

    def test_timeout_with_no_transaction_lets_the_cashier_retry(self):
        self.fake.auth_card_error = TIMEOUT
        self.fake.inquire_by_orderid = None  # "Txn not found"
        _started, result = self._pay()
        self.assertEqual(result['status'], 'error')
        self.assertIn('no charge stands', result['message'])

    def test_timeout_with_unreachable_gateway_stays_unknown(self):
        self.fake.auth_card_error = TIMEOUT
        self.fake.inquire_by_orderid = TIMEOUT
        started, result = self._pay()
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['terminal_order_id'], started['terminal_order_id'])
        # Later, once the Gateway answers, the line resolves without a new charge.
        self.fake.inquire_by_orderid = {'respstat': 'A', 'respcode': '00', 'retref': 'LATE-1',
                                        'authcode': 'L1', 'amount': '42.50'}
        resolved = self.make_jsonrpc_request('/pos_cardpointe_poc/inquire', {
            'payment_method_id': self.cardpointe_pm.id,
            'terminal_order_id': started['terminal_order_id'], 'amount': 42.5,
        })
        self.assertEqual(resolved['status'], 'approved')
        self.assertEqual(resolved['retref'], 'LATE-1')
        self.assertEqual(len(self.fake.paths('v4/authCard')), 1)

    def test_decline_is_not_an_unknown_outcome(self):
        self.fake.auth_card = {'respstat': 'C', 'respcode': '05', 'resptext': 'Do not honor'}
        _started, result = self._pay()
        self.assertEqual(result['status'], 'declined')
        self.assertFalse(self.fake.paths('inquireByOrderid'))

    def test_terminal_amount_uses_implied_cents(self):
        self._pay(42.5)
        self.assertEqual(self.fake.paths('v4/authCard')[0]['json']['amount'], '4250')

    def test_credential_check_uses_the_documented_request(self):
        result = self.merchant.action_test_credentials()
        self.assertEqual(result['params']['type'], 'success')
        call = self.fake.paths('cardconnect/rest/')[-1]
        self.assertEqual(call['method'], 'PUT')
        self.assertEqual(call['json'], {'merchid': '496160873888'})


@tagged('post_install', '-at_install', 'cardpointe')
class TestCardPointeCredentialsAfterCreation(TestPointOfSaleHttpCommon):
    """A configuration package creates the records; an operator adds the secrets later."""

    def test_records_can_be_created_without_secrets_and_refuse_to_call_out(self):
        fake = FakeCardPointe()
        fake.patch_transport(self)
        company = self.main_pos_config.company_id
        merchant = self.env['cardpointe.merchant.config'].create({
            'name': 'Packaged merchant', 'company_id': company.id, 'mid': '800000009875',
            'gateway_base_url': 'https://fts-uat.cardconnect.com/cardconnect/rest/',
        })
        terminal = self.env['pos.cardpointe.terminal.config'].create({
            'name': 'Packaged terminal', 'company_id': company.id, 'merchant_config_id': merchant.id,
            'device_serial': 'C047UG43720996',
        })
        from odoo.addons.payment_cardpointe_base.services.gateway import CardPointeGatewayClient
        from odoo.addons.pos_cardpointe_poc.services.cardpointe_terminal import CardPointeTerminalClient
        self.assertEqual(CardPointeGatewayClient(merchant).inquire('R', 'M')['error_code'], 'credentials_missing')
        self.assertFalse(CardPointeTerminalClient(terminal).connect()['ok'])
        self.assertFalse(fake.calls, "nothing may be sent without credentials")
        merchant.write({'gateway_username': 'testing', 'gateway_password': 'testing-SECRET'})
        terminal.write({'auth_key': 'terminal-key-SECRET'})
        self.assertTrue(CardPointeTerminalClient(terminal).connect()['ok'])
