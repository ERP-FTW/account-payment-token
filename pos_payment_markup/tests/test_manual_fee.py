from odoo.exceptions import UserError
from .test_pos_session import TestPosSessionMarkupLoader


class TestPosManualFee(TestPosSessionMarkupLoader):
    def test_program_provider_projection_matches_backend(self):
        if 'cp_surcharge_program' not in self.env['payment.provider']._fields:
            self.skipTest('Optional online CardPointe provider is not installed')
        provider = self.env.ref('payment_cardpointe.payment_provider_cardpointe')
        config = self.env['cardpointe.merchant.config'].create({
            'name': 'Synthetic projection program', 'mid': 'LAB-NONROUTABLE',
            'surcharge_program': True,
        })
        provider.write({'company_id': self.company.id,
                        'cardpointe_merchant_config_id': config.id,
                        'markup_enabled': True, 'markup_percent': 3,
                        'markup_product_id': self.markup_product.id,
                        'markup_use_cardpointe_program': False})
        self.method.write({'markup_mode': 'disabled', 'markup_provider_id': provider.id,
                           'markup_card_type': 'credit'})
        data = {'pos.config': {'data': [{'id': self.pos_config.id}]}}
        def loaded():
            rows = self.env['pos.payment.method'].with_user(self.pos_user)._load_pos_data(data)['data']
            return next(row for row in rows if row['id'] == self.method.id)
        self.assertFalse(loaded()['markup_enabled'])
        self.assertEqual(self.method._markup_preview(100)['amount'], 0)
        # Dependency invalidation must follow the actual merchant configuration.
        config.surcharge_program = False
        self.assertTrue(loaded()['markup_enabled'])
        self.assertEqual(self.method._markup_preview(100)['amount'], 3)
        config.surcharge_program = True
        self.assertFalse(loaded()['markup_enabled'])
        # Ordinary explicitly configured manual tenders remain independent.
        self.method.write({'markup_mode': 'percentage', 'manual_markup_percent': 3,
                           'manual_markup_product_id': self.markup_product.id})
        self.assertTrue(loaded()['markup_enabled'])
        self.assertEqual(self.method._markup_preview(100)['amount'], 3)

    def test_terminal_program_suppresses_manual_and_provider_fees(self):
        if 'cardpointe_config_id' not in self.method._fields:
            self.skipTest('Optional POS CardPointe terminal not installed')
        merchant = self.env['cardpointe.merchant.config'].create({
            'name': 'Synthetic terminal program', 'mid': 'LAB-NONROUTABLE',
            'surcharge_program': True})
        terminal = self.env['pos.cardpointe.terminal.config'].create({
            'name': 'Synthetic terminal', 'merchant_config_id': merchant.id})
        self.method.write({'use_payment_terminal': 'cardpointe_poc',
                           'cardpointe_config_id': terminal.id,
                           'manual_markup_product_id': self.markup_product.id,
                           'manual_markup_percent': 3, 'markup_fixed_amount': 2})
        data = {'pos.config': {'data': [{'id': self.pos_config.id}]}}
        for mode in ('disabled', 'percentage', 'fixed'):
            with self.subTest(mode=mode):
                self.method.markup_mode = mode
                rows = self.env['pos.payment.method'].with_user(self.pos_user)._load_pos_data(data)['data']
                row = next(row for row in rows if row['id'] == self.method.id)
                self.assertFalse(row['markup_enabled'])
                self.assertFalse(row['markup_product_id'])
                preview = self.method.with_user(self.pos_user)._markup_preview(100)
                self.assertFalse(preview['eligible'])
                self.assertEqual(preview['amount'], 0)
        merchant.surcharge_program = False
        self.assertTrue(self.method.with_user(self.pos_user).markup_enabled)
        self.assertEqual(self.method.with_user(self.pos_user)._markup_preview(100)['amount'], 2)

    def test_payment_loader_preserves_fee_and_native_fields(self):
        self.markup_product.taxes_id = [(5, 0, 0)]
        self.method.write({'markup_provider_id': False, 'markup_mode': 'percentage',
                           'manual_markup_percent': 3,
                           'manual_markup_product_id': self.markup_product.id})
        session = self.env['pos.session'].create({'config_id': self.pos_config.id})
        order = self.env['pos.order'].create({
            'session_id': session.id, 'amount_tax': 0, 'amount_total': 61.8,
            'amount_paid': 61.8, 'amount_return': 0,
        })
        evidence = {'markup_base_amount': 60, 'markup_rate_applied': 3,
                    'markup_amount': 1.8, 'markup_total_amount': 61.8,
                    'markup_amount_source': 'local',
                    'markup_payment_reference': 'loader-tender'}
        payment = self.env['pos.payment'].create(dict(evidence,
            pos_order_id=order.id, payment_method_id=self.method.id,
            amount=61.8, uuid='loader-tender'))
        data = {'pos.config': {'data': [{'id': self.pos_config.id}]},
                'pos.order': {'data': [{'id': order.id}]}}
        loaded = self.env['pos.payment'].with_user(self.pos_user)._load_pos_data(data)
        row, = loaded['data']
        for name, value in evidence.items():
            self.assertEqual(row[name], value)
        self.assertEqual(row['amount'], 61.8)
        self.assertEqual(row['uuid'], payment.uuid)
        self.assertEqual(row['payment_method_id'], self.method.id)
        self.assertEqual(row['pos_order_id'], order.id)
        if 'cardpointe_status' in payment._fields:
            self.assertIn('cardpointe_status', row)
        if loaded['fields']:
            self.assertEqual(len(loaded['fields']), len(set(loaded['fields'])))

    def test_manual_modes_tax_rounding_and_split(self):
        self.markup_product.taxes_id = [(5, 0, 0)]
        method = self.method
        method.write({'markup_provider_id': False, 'markup_mode': 'percentage',
                      'manual_markup_percent': 3, 'manual_markup_product_id': self.markup_product.id})
        self.assertEqual(method.with_user(self.pos_user)._markup_preview(60)['total'], 61.8)
        self.assertEqual(method._markup_preview(0.1)['amount'], 0)
        method.write({'markup_mode': 'fixed', 'markup_fixed_amount': 2})
        self.assertEqual(method._markup_preview(100)['total'], 102)
        self.assertEqual(method._markup_preview(0)['amount'], 0)
        method.markup_mode = 'disabled'
        self.assertEqual(method._markup_preview(100)['amount'], 0)
        tax = self.env['account.tax'].create({'name': 'Synthetic fee tax', 'amount': 10, 'company_id': self.company.id})
        self.markup_product.taxes_id = tax
        method.write({'markup_mode': 'fixed', 'markup_fixed_amount': 2})
        preview = method._markup_preview(100)
        self.assertEqual((preview['net_amount'], preview['tax_amount'], preview['gross_amount']), (2, .2, 2.2))
        method.write({'markup_fixed_amount': 2.2, 'manual_markup_tax_included': True})
        preview = method._markup_preview(100)
        self.assertEqual((preview['net_amount'], preview['tax_amount'], preview['gross_amount']), (2, .2, 2.2))

    def test_original_fee_refund_cap_and_immutability(self):
        self.markup_product.taxes_id = [(5, 0, 0)]
        self.method.write({'markup_provider_id': False, 'markup_mode': 'fixed',
                           'markup_fixed_amount': 2, 'manual_markup_product_id': self.markup_product.id})
        session = self.env['pos.session'].create({'config_id': self.pos_config.id})
        original = self.env['pos.order'].create({
            'session_id': session.id, 'amount_tax': 0, 'amount_total': 102,
            'amount_paid': 102, 'amount_return': 0,
            'lines': [(0, 0, {'product_id': self.markup_product.id, 'qty': 1,
                             'price_unit': 2, 'price_subtotal': 2, 'price_subtotal_incl': 2,
                             'markup_generated': True, 'markup_payment_reference': 'original-tender'}),
                      (0, 0, {'product_id': self.markup_product.id, 'qty': 1, 'price_unit': 100,
                              'price_subtotal': 100, 'price_subtotal_incl': 100})],
            'payment_ids': [(0, 0, {'payment_method_id': self.method.id, 'amount': 102,
                                   'uuid': 'original-tender', 'markup_base_amount': 100,
                                   'markup_amount': 2, 'markup_total_amount': 102})],
        })
        original.state = 'paid'
        fee = original.lines.filtered('markup_generated')
        self.method.markup_fixed_amount = 9
        def refund(qty):
            return self.env['pos.order'].create({
                'session_id': session.id, 'amount_tax': 0, 'amount_total': 2 * qty,
                'amount_paid': 2 * qty, 'amount_return': 0,
                'lines': [(0, 0, {'product_id': fee.product_id.id, 'qty': qty,
                                  'price_unit': 2, 'price_subtotal': 2 * qty,
                                  'price_subtotal_incl': 2 * qty, 'refunded_orderline_id': fee.id})],
            })
        with self.assertRaises(UserError), self.cr.savepoint():
            duplicate = refund(-.6)
            duplicate.lines.copy({'order_id': duplicate.id, 'refunded_orderline_id': fee.id})
            duplicate.state = 'paid'
        first = refund(-.5)
        first.state = 'paid'
        self.assertEqual(first.lines.price_unit, 2)
        self.assertTrue(first.lines.markup_generated)
        second = refund(-.5)
        second.state = 'paid'
        with self.assertRaises(UserError), self.cr.savepoint():
            refund(-.1).state = 'paid'
        with self.assertRaises(UserError), self.cr.savepoint():
            fee.price_unit = 9
        with self.assertRaises(UserError), self.cr.savepoint():
            original.payment_ids.markup_amount = 9
        # A retry revalidates the same refund without counting it twice.
        second._validate_markup_evidence()
