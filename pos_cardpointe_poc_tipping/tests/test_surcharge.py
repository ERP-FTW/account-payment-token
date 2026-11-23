"""Exercise the installed tipping controller, not its superseded base route.

All processor calls are intercepted by the inherited synthetic transport.
"""
from unittest.mock import patch

from odoo.tests import tagged
from odoo.addons.pos_cardpointe_poc.tests.test_surcharge import TestTerminalProgram
from odoo.addons.pos_cardpointe_poc.services.cardpointe_terminal import CardPointeTerminalClient


@tagged('post_install', '-at_install', 'cardpointe_tipping_program')
class TestTippingProgram(TestTerminalProgram):
    # Inherited cases exercise nonzero/zero receipt persistence, malformed fees,
    # timeout/inquiry-only recovery and foreign identity with tips disabled.
    def auth(self, started):
        result = super().auth(started)
        if result['status'] == 'approved':
            self.assertIn('cardpointe_tip_prompted', result, 'Must reach tipping override')
        return result

    def test_terminal_nonzero_zero_receipt_persistence(self):
        self.assertFalse(self.terminal.enable_tips)
        super().test_terminal_nonzero_zero_receipt_persistence()

    def test_unusable_terminal_fee_stays_unknown(self):
        super().test_unusable_terminal_fee_stays_unknown()

    def test_timeout_original_identity_recovery_and_not_found_hold(self):
        super().test_timeout_original_identity_recovery_and_not_found_hold()

    def test_tip_and_program_remain_separate(self):
        self.terminal.enable_tips = True
        self.main_pos_config.write({
            'iface_tipproduct': True,
            'tip_product_id': self.env['product.product'].create({
                'name': 'Synthetic tip', 'type': 'service', 'list_price': 0,
                'available_in_pos': True, 'taxes_id': [(5, 0, 0)],
            }).id,
        })
        for program, tip, fee, total in [
            (True, 10, '3.00', '113.00'),
            (True, 0, '0.00', '100.00'),
            (False, 10, None, '110.00'),
        ]:
            with self.subTest(program=program, tip=tip), patch.object(
                CardPointeTerminalClient, 'tip_with_session',
                return_value={'ok': True, 'tip_amount': tip},
            ) as prompt:
                self.merchant.surcharge_program = program
                self.fake.auth_card.update(amount=total, merchid=self.merchant.mid)
                if program:
                    self.fake.auth_card.update(fee_amount=fee, fee_type='SURCHRG')
                else:
                    self.fake.auth_card.pop('fee_amount', None)
                    self.fake.auth_card.pop('fee_type', None)
                started = self.start()
                self.fake.auth_card['orderid'] = started['terminal_order_id']
                result = self.auth(started)
                self.assertEqual(result['status'], 'approved', result)
                prompt.assert_called_once()
                self.assertTrue(result['cardpointe_tip_prompted'])
                self.assertEqual(result['cardpointe_tip_amount'], tip)
                self.assertEqual(result['cardpointe_base_amount'], 100)
                self.assertEqual(result['cardpointe_total_amount'], 100 + tip)
                self.assertEqual(self.fake.paths('v4/authCard')[-1]['json']['amount'], str((100 + tip) * 100))
                if program:
                    self.assertEqual(result['program_receipt']['subtotal'], f'{100 + tip:.2f}')
                    self.assertEqual(result['program_receipt']['fee'], fee)
                    self.assertEqual(result['program_receipt']['total'], total)
                else:
                    self.assertFalse(result.get('program_receipt'))

    def test_decline_does_not_expose_program_receipt(self):
        self.fake.auth_card.update(respstat='C', respcode='05', resptext='Declined')
        result = self.auth(self.start())
        self.assertEqual(result['status'], 'declined', result)
        self.assertFalse(result.get('program_receipt'))
        self.assertEqual(len(self.fake.paths('v4/authCard')), 1)
        self.assertEqual(len(self.fake.paths('v2/disconnect')), 1)
