from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.point_of_sale.tests.common import TestPoSCommon

from .cardpointe_fake import TIMEOUT, FakeCardPointe


@tagged('post_install', '-at_install', 'cardpointe')
class TestCardPointeRefunds(TestPoSCommon):
    """Returns against a CardPointe terminal sale, as a cashier (not an administrator)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.basic_config
        company = cls.company
        cls.merchant = cls.env['cardpointe.merchant.config'].create({
            'name': 'Test merchant', 'company_id': company.id, 'mid': '496160873888',
            'gateway_base_url': 'https://fts-uat.cardconnect.com/cardconnect/rest/',
            'gateway_username': 'testing', 'gateway_password': 'testing-SECRET',
        })
        cls.terminal = cls.env['pos.cardpointe.terminal.config'].create({
            'name': 'Counter terminal', 'company_id': company.id, 'merchant_config_id': cls.merchant.id,
            'auth_key': 'terminal-key-SECRET', 'device_serial': 'C032UQ03960574',
        })
        cls.cardpointe_pm = cls.env['pos.payment.method'].create({
            'name': 'CardPointe', 'journal_id': cls.company_data['default_journal_bank'].id,
            'receivable_account_id': cls.pos_receivable_bank.id,
            'use_payment_terminal': 'cardpointe_poc', 'cardpointe_config_id': cls.terminal.id,
            'company_id': company.id,
        })
        cls.config.write({'payment_method_ids': [Command.link(cls.cardpointe_pm.id)]})
        cls.product = cls.create_product('CardPointe test product', cls.categ_basic, 10.0)
        cls.cashier = cls.env['res.users'].create({
            'name': 'Cashier', 'login': 'cardpointe_cashier',
            'company_id': company.id, 'company_ids': [Command.set(company.ids)],
            'groups_id': [Command.set([cls.env.ref('base.group_user').id,
                                       cls.env.ref('point_of_sale.group_pos_user').id])],
        })

    def setUp(self):
        super().setUp()
        self.fake = FakeCardPointe()
        self.fake.patch_transport(self)

    def _sale(self, amount=100.0, retref='SALE-RETREF-1'):
        self.open_new_session()
        order_data = self.create_ui_order_data(
            [(self.product, amount / self.product.lst_price)], payments=[(self.cardpointe_pm, amount)]
        )
        order = self.env['pos.order'].browse(
            self.env['pos.order'].sync_from_ui([order_data])['pos.order'][0]['id'])
        order.payment_ids.write({'cardpointe_retref': retref, 'cardpointe_status': 'approved'})
        self.fake.inquire = {'retref': retref, 'amount': f"{amount:.2f}", 'setlstat': 'Queued for Capture',
                             'respstat': 'A', 'respcode': '00', 'voidable': 'Y', 'refundable': 'Y'}
        return order

    def _refund(self, order, amount, user=None):
        return self.env['pos.payment'].with_user(user or self.cashier).cardpointe_process_refund(
            payment_method_id=self.cardpointe_pm.id, amount=-amount,
            refunded_orderline_ids=order.lines.ids,
        )

    def test_partial_return_is_an_exact_refund_never_a_void(self):
        order = self._sale(100.0)
        result = self._refund(order, 40.0)
        self.assertFalse(self.fake.paths('rest/void'), "a partial return must never void the sale")
        refunds = self.fake.paths('rest/refund')
        self.assertEqual(len(refunds), 1)
        self.assertEqual(refunds[0]['json']['amount'], '40.00')
        self.assertEqual(result['operation'], 'refund')

    def test_partial_return_before_settlement_fails_without_moving_money(self):
        order = self._sale(100.0)
        self.fake.refund = {'respstat': 'C', 'respcode': '28', 'resptext': 'Txn not settled'}
        with self.assertRaises(UserError):
            self._refund(order, 40.0)
        self.assertFalse(self.fake.paths('rest/void'))

    def test_full_return_before_settlement_voids_the_sale(self):
        order = self._sale(100.0)
        result = self._refund(order, 100.0)
        self.assertEqual(len(self.fake.paths('rest/void')), 1)
        self.assertFalse(self.fake.paths('rest/refund'))
        self.assertEqual(result['operation'], 'void')

    def test_failed_inquiry_selects_no_operation(self):
        order = self._sale(100.0)
        self.fake.inquire = {}  # empty data: as when the inquiry could not be read
        with self.assertRaises(UserError):
            self._refund(order, 100.0)
        self.assertFalse(self.fake.paths('rest/void'))
        self.assertFalse(self.fake.paths('rest/refund'))

    def test_unanswered_refund_is_unknown_and_holds_the_sale(self):
        order = self._sale(100.0)
        self.fake.inquire['setlstat'] = 'Accepted'
        self.fake.refund_error = TIMEOUT
        result = self._refund(order, 30.0)
        self.assertEqual(result['status'], 'unknown', "never a retryable error: the refund may have gone through")
        self.assertFalse(result['ok'])
        self.assertIn('unknown', result['message'])
        self.assertTrue(order.payment_ids.cardpointe_refund_hold)
        # The line, or a new return of the same sale, cannot send the money back again.
        self.fake.refund_error = None
        with self.assertRaisesRegex(UserError, 'hold'):
            self._refund(order, 30.0)
        self.assertEqual(len(self.fake.paths('rest/refund')), 1)

    def test_only_a_manager_clears_the_hold_and_returns_resume(self):
        order = self._sale(100.0)
        self.fake.inquire['setlstat'] = 'Accepted'
        self.fake.refund_error = TIMEOUT
        self._refund(order, 30.0)
        payment = order.payment_ids
        with self.assertRaises(AccessError):
            payment.with_user(self.cashier).action_cardpointe_clear_refund_hold()
        self.assertTrue(payment.cardpointe_refund_hold)
        payment.action_cardpointe_clear_refund_hold()  # administrator, a POS manager
        self.assertFalse(payment.cardpointe_refund_hold)
        self.fake.refund_error = None
        self.assertEqual(self._refund(order, 30.0)['status'], 'approved')

    def test_return_stopping_part_way_holds_every_payment_it_touched(self):
        self.open_new_session()
        order_data = self.create_ui_order_data(
            [(self.product, 10)], payments=[(self.cardpointe_pm, 60.0), (self.cardpointe_pm, 40.0)])
        order = self.env['pos.order'].browse(
            self.env['pos.order'].sync_from_ui([order_data])['pos.order'][0]['id'])
        first, second = order.payment_ids.sorted('id')
        first.write({'cardpointe_retref': 'SALE-A', 'cardpointe_status': 'approved'})
        second.write({'cardpointe_retref': 'SALE-B', 'cardpointe_status': 'approved'})
        self.fake.inquire = {'amount': '60.00', 'setlstat': 'Accepted', 'respstat': 'A', 'respcode': '00',
                             'voidable': 'N', 'refundable': 'Y'}
        self.fake.inquire_retref_from_path = True
        self.fake.refund_answers = [
            {'respstat': 'A', 'respcode': '000', 'resptext': 'Approval', 'retref': 'REFUND-A'},
            {'respstat': 'C', 'respcode': '05', 'resptext': 'Refund declined'},
        ]
        result = self._refund(order, 100.0)
        self.assertEqual(result['status'], 'unknown', "a retry would return SALE-A a second time")
        self.assertIn('REFUND-A', result['message'])
        self.assertTrue(first.cardpointe_refund_hold)
        self.assertTrue(second.cardpointe_refund_hold)
        with self.assertRaisesRegex(UserError, 'hold'):
            self._refund(order, 100.0)
        self.assertEqual(len(self.fake.paths('rest/refund')), 2)

    def test_cashier_of_another_company_cannot_refund(self):
        order = self._sale(100.0)
        other = self.env['res.company'].create({'name': 'Other CardPointe company'})
        outsider = self.cashier.copy({
            'login': 'cardpointe_outsider', 'company_id': other.id,
            'company_ids': [Command.set(other.ids)],
        })
        with self.assertRaises(Exception) as caught:
            self._refund(order, 10.0, user=outsider)
        self.assertIsInstance(caught.exception, (UserError, AccessError))
        self.assertFalse(self.fake.paths('rest/'))

    def test_terminal_and_merchant_must_share_the_method_company(self):
        order = self._sale(100.0)
        other = self.env['res.company'].create({'name': 'Merchant elsewhere'})
        self.merchant.sudo().company_id = other
        with self.assertRaises(UserError):
            self._refund(order, 10.0)
        self.assertFalse(self.fake.paths('rest/'))
