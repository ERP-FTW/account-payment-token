from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestManualFeeEvidence(TransactionCase):
    def test_manual_disabled_and_refund_original_rate(self):
        company = self.env.company
        company.country_id = self.env.ref('base.us')
        def account(code, kind, reconcile=False):
            return self.env['account.account'].create({
                'name': code, 'code': code, 'account_type': kind,
                'company_ids': [(6, 0, company.ids)], 'reconcile': reconcile,
            })
        income = account('PORTI', 'income')
        receivable = account('PORTR', 'asset_receivable', True)
        outstanding = account('PORTO', 'asset_current', True)
        partner = self.env['res.partner'].create({
            'name': 'Portable fee customer',
            'property_account_receivable_id': receivable.id,
        })
        fee = self.env['product.product'].create({
            'name': 'Portable fee', 'type': 'service',
            'property_account_income_id': income.id, 'taxes_id': [(5, 0, 0)],
        })
        sale = self.env['account.journal'].create({
            'name': 'Portable sale', 'code': 'PTS', 'type': 'sale',
            'company_id': company.id,
        })
        bank = self.env['account.journal'].create({
            'name': 'Portable bank', 'code': 'PTB', 'type': 'bank',
            'company_id': company.id,
        })
        method = bank.inbound_payment_method_line_ids[:1]
        method.payment_account_id = outstanding
        for enabled in (True, False):
            method.write({'markup_mode': 'percentage' if enabled else 'disabled',
                          'markup_percent': 3, 'markup_product_id': fee.id})
            invoice = self.env['account.move'].create({
                'move_type': 'out_invoice', 'partner_id': partner.id,
                'journal_id': sale.id, 'invoice_line_ids': [(0, 0, {
                    'name': 'Tax-free base', 'quantity': 1, 'price_unit': 100,
                    'account_id': income.id, 'tax_ids': [(5, 0, 0)],
                })],
            })
            invoice.action_post()
            wizard = self.env['account.payment.register'].with_context(
                active_model='account.move', active_ids=invoice.ids,
            ).create({'journal_id': bank.id, 'payment_method_line_id': method.id})
            self.assertEqual(wizard.markup_gross_amount, 3 if enabled else 0)
            self.assertEqual(wizard.markup_total_amount, 103 if enabled else 100)
            payment = wizard._create_payments()
            self.assertEqual(payment.amount, 103 if enabled else 100)
            self.assertEqual(invoice.amount_residual, 0)
            self.assertEqual(payment.move_id.state, 'posted')
            if not enabled:
                self.assertFalse(payment.markup_gross_amount)
                continue
            self.assertEqual(payment.markup_base_amount, 100)
            self.assertEqual(payment.markup_rate_applied, 3)
            self.assertEqual(payment.markup_gross_amount, 3)
            self.assertEqual(payment.markup_amount_source, 'manual')
            fee_lines = payment.move_id.line_ids.filtered(
                lambda line: line.markup_decision_source == 'manual')
            self.assertEqual(sum(fee_lines.mapped('credit')), 3)
            method.markup_percent = 9
            refunds = payment.action_refund_markup(1) | payment.action_refund_markup(2)
            self.assertEqual(refunds.mapped('markup_source_payment_id'), payment)
            self.assertTrue(all(move.state == 'posted' for move in refunds))
            self.assertEqual(payment.markup_refunded_amount, 3)
            self.assertEqual(payment.markup_rate_applied, 3)
            with self.assertRaises(ValidationError):
                payment.action_refund_markup(0.01)
