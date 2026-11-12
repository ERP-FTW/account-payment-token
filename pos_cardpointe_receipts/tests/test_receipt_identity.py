from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'cardpointe')
class TestCardPointeReceiptIdentity(TransactionCase):

    def test_terminal_order_id_precedes_pos_reference_fallback(self):
        payment = self.env['pos.payment'].new({
            'cardpointe_terminal_order_id': 'CPTERM123456789',
        })

        self.assertEqual(
            payment._get_cardpointe_receipt_order_id(),
            'CPTERM123456789',
        )