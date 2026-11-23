from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.exceptions import ValidationError
from ..services.calculation import calculate_markup


@tagged('post_install', '-at_install')
class TestProgramBoundary(TransactionCase):
    def test_ordinary_provider_cannot_enable_program(self):
        provider = self.env['payment.provider'].create({'name': 'Synthetic ordinary provider', 'code': 'none'})
        self.assertFalse(provider.markup_cardpointe_card_available)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            provider.markup_use_cardpointe_program = True
        view = self.env.ref('account_payment_markup.payment_provider_form_markup')
        self.assertIn('invisible="not markup_cardpointe_card_available"', view.arch_db)

    def test_optional_cardpointe_card_ach_and_zero(self):
        if 'cardpointe' not in dict(self.env['payment.provider']._fields['code']._description_selection(self.env)):
            self.skipTest('Optional gateway not installed; accounting has no gateway dependency')
        card = self.env.ref('payment.payment_method_card')
        ach = self.env.ref('payment.payment_method_ach_direct_debit')
        provider = self.env['payment.provider'].create({
            'name': 'Synthetic CardPointe boundary', 'code': 'cardpointe',
            'payment_method_ids': [(6, 0, (card | ach).ids)],
            'markup_use_cardpointe_program': True,
        })
        self.assertFalse(provider.cardpointe_mid)
        self.assertTrue(provider.markup_cardpointe_card_available)
        self.assertFalse(provider._markup_card_eligible())
        trusted = provider.with_context(server_trusted_markup_evidence=True)
        for instrument, expected in [('credit', True), ('ach', False), ('debit', False), ('unknown', False)]:
            eligible = trusted._markup_card_eligible(instrument, evidence_verified=True,
                        evidence_trusted=True, processor_surcharge_present=False)
            self.assertEqual(eligible, expected)
        self.assertFalse(provider._markup_card_eligible('credit', evidence_verified=True,
                         evidence_trusted=True, processor_surcharge_present=False))
        self.assertEqual(calculate_markup(100, 3, self.env.company.currency_id.round,
                                         eligible=True, processor_amount=0)['amount'], 0)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            provider.payment_method_ids = ach
        provider.write({'markup_use_cardpointe_program': False, 'payment_method_ids': [(6, 0, ach.ids)]})
        self.assertFalse(provider.markup_cardpointe_card_available)
