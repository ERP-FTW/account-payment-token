from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCardCheckout(TransactionCase):

    def test_switching_fee_source_keeps_manual_rate(self):
        card = self.env.ref('payment.payment_method_card')
        provider = self.env.ref('payment_cardpointe.payment_provider_cardpointe')
        product = self.env['product.product'].create({'name': 'Checkout fee', 'type': 'service', 'taxes_id': False})
        card.write({'markup_percent': 3, 'markup_product_id': product.id, 'use_cardpointe_program': True})
        args = (provider, 100, self.env.ref('base.USD'), self.env.user.partner_id)
        self.assertFalse(card._checkout_markup(*args))
        card.use_cardpointe_program = False
        self.assertEqual(card._checkout_markup(*args)['total'], 103)
