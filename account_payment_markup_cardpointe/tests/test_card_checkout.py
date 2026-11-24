"""Actual card adapter contract, independent of the optional ACH module."""
from types import SimpleNamespace
from unittest.mock import patch

from odoo.tests import tagged
from odoo.addons.account_payment_markup.tests.test_invoice_checkout import TestInvoiceCheckout


@tagged('post_install', '-at_install')
class TestCardCheckout(TestInvoiceCheckout):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref('payment_cardpointe.payment_provider_cardpointe')
        product = cls.env['product.product'].search([('name', '=', 'Manual checkout fee')], limit=1)
        cfg = cls.env['cardpointe.merchant.config'].create({
            'name': 'Synthetic card checkout', 'mid': 'LAB-NONROUTABLE',
            'surcharge_program': False,
        })
        cls.provider.write({
            'state': 'test', 'markup_enabled': True, 'markup_percent': 3,
            'markup_product_id': product.id, 'markup_use_cardpointe_program': False,
            'cardpointe_merchant_config_id': cfg.id,
            'payment_method_ids': [(6, 0, cls.env.ref('payment.payment_method_card').ids)],
        })

    def setUp(self):
        super().setUp()
        for ctx in (
            patch('odoo.addons.payment.utils.request', new=SimpleNamespace(env=self.env)),
            patch('requests.sessions.Session.request', side_effect=AssertionError('Network forbidden')),
        ):
            ctx.start()
            self.addCleanup(ctx.stop)

    def test_card_enabled_single_fee(self):
        super().test_enabled_checkout_has_one_fee_on_repeated_processing()

    def test_card_disabled_no_fee(self):
        super().test_disabled_checkout_remains_principal_without_fee()

    def test_card_checkout_amount_and_document_ceiling(self):
        tx = self._transaction()
        self.assertFalse(self.provider.cp_surcharge_program)
        self.assertEqual(tx._get_processing_values()['amount'], 103)
        self.assertEqual(tx._cp_document_amount(), 100)
        self.assertEqual(self.provider._cp_checkout_amount(100, self.currency, self.partner, 'card'), 103)
        tx._cp_check_documents()
        fee = tx.markup_fee_move_id
        self.assertEqual(fee.amount_total, 3)
        self.assertEqual(tx._get_processing_values()['amount'], 103)
        self.assertEqual(tx.markup_fee_move_id, fee)
