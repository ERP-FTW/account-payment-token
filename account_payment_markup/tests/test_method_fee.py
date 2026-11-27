from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestInboundMethodFee(TransactionCase):
    def test_grouped_fixed_and_method_independence(self):
        company = self.env.company
        company.country_id = self.env.ref('base.us')
        def account(code, kind, reconcile=False):
            return self.env['account.account'].create({
                'name': code, 'code': code, 'account_type': kind,
                'company_ids': [(6, 0, company.ids)], 'reconcile': reconcile,
            })
        income = account('FEEI', 'income')
        receivable = account('FEER', 'asset_receivable', True)
        outstanding = account('FEEO', 'asset_current', True)
        partner = self.env['res.partner'].create({
            'name': 'Synthetic fee customer', 'property_account_receivable_id': receivable.id,
        })
        fee = self.env['product.product'].create({
            'name': 'Synthetic fee', 'type': 'service',
            'property_account_income_id': income.id, 'taxes_id': [(5, 0, 0)],
        })
        sale = self.env['account.journal'].create({'name': 'Fee sale', 'code': 'FES', 'type': 'sale'})
        bank = self.env['account.journal'].create({'name': 'Fee bank', 'code': 'FEB', 'type': 'bank'})
        manual = bank.inbound_payment_method_line_ids[:1]
        ach = self.env['account.payment.method.line'].create({
            'name': 'Synthetic ACH (no merchant)', 'journal_id': bank.id,
            'payment_method_id': self.env['account.payment.method'].create({
                'name': 'Synthetic ACH', 'code': 'synthetic_ach', 'payment_type': 'inbound',
            }).id,
        })
        for method in manual | ach:
            for mode, expected in [('fixed', 2), ('percentage', 3), ('disabled', 0)]:
                with self.subTest(method=method.name, mode=mode):
                    method.write({'payment_account_id': outstanding.id, 'markup_mode': mode,
                                  'markup_product_id': fee.id, 'markup_percent': 3,
                                  'markup_fixed_amount': 2})
                    invoices = self.env['account.move'].create([{
                        'move_type': 'out_invoice', 'partner_id': partner.id, 'journal_id': sale.id,
                        'invoice_line_ids': [(0, 0, {'name': 'Synthetic sale', 'quantity': 1,
                            'price_unit': amount, 'account_id': income.id, 'tax_ids': [(5, 0, 0)]})],
                    } for amount in (40, 60)])
                    invoices.action_post()
                    wizard = self.env['account.payment.register'].with_context(
                        active_model='account.move', active_ids=invoices.ids,
                    ).create({'journal_id': bank.id, 'payment_method_line_id': method.id, 'group_payment': True})
                    self.assertEqual(wizard.markup_gross_amount, expected)
                    payment = wizard._create_payments()
                    self.assertEqual(len(payment), 1)
                    self.assertEqual(payment.amount, 100 + expected)
                    self.assertEqual(sum(invoices.mapped('amount_residual')), 0)
                    self.assertEqual(payment.markup_gross_amount, expected)
                    if expected:
                        method.write({'markup_mode': 'disabled', 'markup_percent': 9, 'markup_fixed_amount': 9})
                        refund = payment.action_refund_markup()
                        self.assertEqual(refund.markup_gross_amount, expected)
                        self.assertEqual(refund.markup_source_payment_id, payment)
                        with self.assertRaises(ValidationError):
                            payment.action_refund_markup()
