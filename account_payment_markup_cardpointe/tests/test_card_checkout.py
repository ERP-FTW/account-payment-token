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
        cls.provider = cls.env['payment.provider'].create({
            'name': 'Synthetic CardPointe checkout', 'code': 'cardpointe',
            'inline_form_view_id': cls.env.ref('payment_cardpointe.inline_form').id,
        })
        product = cls.fee.markup_product_id
        cfg = cls.env['cardpointe.merchant.config'].create({
            'name': 'Synthetic card checkout', 'mid': 'LAB-NONROUTABLE',
        })
        cls.provider.write({
            'state': 'test',
            'cardpointe_merchant_config_id': cfg.id,
            'payment_method_ids': [(6, 0, cls.env.ref('payment.payment_method_card').ids)],
        })

        cls.fee = cls.env['payment.method.fee'].create({
            'method_id': cls.method.id, 'provider_id': cls.provider.id, 'markup_percent': 3,
            'markup_product_id': product.id,
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

    def test_program_card_excludes_configured_manual_fee(self):
        self.fee.use_cardpointe_program = True
        self.assertEqual(self.fee.markup_percent, 0)
        tx = self._transaction()
        values = tx._get_processing_values()
        self.assertEqual(values['amount'], 100)
        self.assertEqual(values['markup_amount'], 0)
        self.assertFalse(tx.markup_fee_move_id)

    def test_card_checkout_amount_with_manual_fee(self):
        tx = self._transaction()
        self.assertFalse(self.fee.use_cardpointe_program)
        self.assertEqual(tx._get_processing_values()['amount'], 103)
        self.assertEqual(tx.markup_base_amount, 100)
        self.assertEqual(self.fee._markup_details(100, self.currency, self.partner)['total'], 103)
        fee = tx.markup_fee_move_id
        self.assertEqual(fee.amount_total, 3)
        self.assertEqual(tx._get_processing_values()['amount'], 103)
        self.assertEqual(tx.markup_fee_move_id, fee)

        with patch.object(type(self.provider), '_cp_online_request', return_value={'ok': True, 'data': {
                'respstat': 'A', 'retref': 'CARD-APPROVED', 'amount': '103.00'}}) as gateway:
            self.assertEqual(tx._cp_process('SyntheticToken123')['state'], 'done')
            tx._cp_process()
        self.assertEqual(gateway.call_count, 1)
        self.assertEqual(gateway.call_args.args[2]['amount'], '103.00')
        self.assertEqual(tx.provider_reference, 'CARD-APPROVED')
