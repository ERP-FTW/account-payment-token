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

    def test_register_program_never_falls_back_to_manual_fee(self):
        if 'cardpointe' not in dict(self.env['payment.provider']._fields['code']._description_selection(self.env)):
            self.skipTest('Optional gateway not installed')
        company = self.env.company
        company.country_id = self.env.ref('base.us')
        def account(code, kind, reconcile=False):
            return self.env['account.account'].create({
                'name': code, 'code': code, 'account_type': kind,
                'company_ids': [(6, 0, company.ids)], 'reconcile': reconcile,
            })
        income = account('PRGI', 'income')
        receivable = account('PRGR', 'asset_receivable', True)
        outstanding = account('PRGO', 'asset_current', True)
        partner = self.env['res.partner'].create({
            'name': 'Synthetic program customer', 'property_account_receivable_id': receivable.id,
        })
        fee = self.env['product.product'].create({
            'name': 'Synthetic program fee', 'type': 'service',
            'property_account_income_id': income.id, 'taxes_id': [(5, 0, 0)],
        })
        provider = self.env['payment.provider'].create({
            'name': 'Synthetic program provider', 'code': 'cardpointe',
            'payment_method_ids': [(6, 0, self.env.ref('payment.payment_method_card').ids)],
            'markup_use_cardpointe_program': True, 'markup_enabled': True,
            'markup_percent': 3, 'markup_product_id': fee.id,
        })
        bank = self.env['account.journal'].create({'name': 'Program bank', 'code': 'PRB', 'type': 'bank'})
        sale = self.env['account.journal'].create({'name': 'Program sale', 'code': 'PRS', 'type': 'sale'})
        method = bank.inbound_payment_method_line_ids[:1]
        method.write({'payment_account_id': outstanding.id, 'markup_product_id': fee.id,
                      'markup_percent': 3, 'markup_fixed_amount': 2})
        # Context-supplied instrument claims are NOT verified gateway evidence.
        # Even a trusted-context flag cannot manufacture evidence the wizard lacks.
        for instrument in ('unknown', 'debit', 'credit'):
            for mode, expected in [('disabled', 0), ('percentage', 3), ('fixed', 2)]:
                with self.subTest(instrument=instrument, mode=mode):
                    method.markup_mode = mode
                    invoice = self.env['account.move'].create({
                        'move_type': 'out_invoice', 'partner_id': partner.id, 'journal_id': sale.id,
                        'invoice_line_ids': [(0, 0, {'name': 'Synthetic sale', 'quantity': 1,
                            'price_unit': 100, 'account_id': income.id, 'tax_ids': [(5, 0, 0)]})],
                    })
                    invoice.action_post()
                    wizard = self.env['account.payment.register'].with_context(
                        active_model='account.move', active_ids=invoice.ids,
                        card_type=instrument, server_trusted_markup_evidence=True,
                    ).create({'journal_id': bank.id, 'payment_method_line_id': method.id,
                              'markup_provider_id': provider.id})
                    self.assertEqual(wizard.markup_gross_amount, expected)
                    self.assertEqual(wizard._markup_payment_values()['total'], 100 + expected)
                    payment = wizard._create_payments()
                    self.assertEqual(payment.amount, 100 + expected)
                    self.assertEqual(payment.markup_gross_amount, expected)
                    self.assertEqual(invoice.amount_residual, 0)
                    self.assertEqual(payment.move_id.state, 'posted')
                    fee_lines = payment.move_id.line_ids.filtered(
                        lambda line: line.markup_decision_source == 'manual')
                    self.assertEqual(sum(fee_lines.mapped('credit')), expected)

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
