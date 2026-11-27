"""Card-independent checkout arithmetic; the companion tests its own adapter.

No processor is called. These cases must run even without the optional ACH addon.
"""
from odoo.tests.common import TransactionCase


class TestInvoiceCheckout(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env.company
        company.country_id = cls.env.ref('base.us')
        cls.currency = cls.env.ref('base.USD')
        cls.currency.active = True
        income = cls.env['account.account'].create({
            'name': 'Checkout income', 'code': 'MKI19',
            'account_type': 'income', 'company_ids': [(6, 0, company.ids)],
        })
        receivable = cls.env['account.account'].create({
            'name': 'Checkout receivable', 'code': 'MKR19',
            'account_type': 'asset_receivable', 'reconcile': True,
            'company_ids': [(6, 0, company.ids)],
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Synthetic checkout customer',
            'property_account_receivable_id': receivable.id,
        })
        product = cls.env['product.product'].create({
            'name': 'Manual checkout fee', 'type': 'service',
            'property_account_income_id': income.id, 'taxes_id': [(5, 0, 0)],
        })
        cls.provider = cls.env['payment.provider'].create({
            'name': 'Synthetic markup provider', 'code': 'none',
            'company_id': company.id,
        })
        cls.method = cls.env.ref('payment.payment_method_card')
        cls.fee = cls.env['payment.method.fee'].create({
            'provider_id': cls.provider.id, 'method_id': cls.method.id,
            'markup_percent': 3, 'markup_product_id': product.id,
        })
        journal = cls.env['account.journal'].create({
            'name': 'Checkout sales', 'code': 'MK19', 'type': 'sale',
            'company_id': company.id,
        })
        cls.invoice = cls.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': cls.partner.id,
            'currency_id': cls.currency.id, 'journal_id': journal.id,
            'invoice_line_ids': [(0, 0, {
                'name': 'Synthetic principal', 'quantity': 1, 'price_unit': 100,
                'account_id': income.id, 'tax_ids': [(5, 0, 0)],
            })],
        })
        cls.invoice.action_post()

    def _transaction(self):
        return self.env['payment.transaction'].create({
            'provider_id': self.provider.id,
            'payment_method_id': self.env.ref('payment.payment_method_card').id,
            'reference': 'MARKUP19-CHECKOUT', 'amount': 100,
            'currency_id': self.currency.id, 'partner_id': self.partner.id,
            'operation': 'online_direct', 'invoice_ids': [(6, 0, self.invoice.ids)],
        })

    def test_enabled_checkout_has_one_fee_on_repeated_processing(self):
        tx = self._transaction()
        first = tx._get_processing_values()
        fee = tx.markup_fee_move_id
        self.assertTrue(fee)
        self.assertEqual(first['amount'], 103)
        self.assertEqual(first['markup_amount'], 3)
        self.assertEqual(fee.amount_total, 3)
        self.assertEqual(len(fee.invoice_line_ids), 1)
        self.assertEqual(tx._get_processing_values()['amount'], 103)
        self.assertEqual(tx.markup_fee_move_id, fee)
        self.assertEqual(self.env['account.move'].search_count([
            ('invoice_origin', '=', tx.reference),
        ]), 1)
        self.assertEqual(self.invoice.amount_total, 100)
        disclosure = self.fee._markup_details(
            100, self.currency, self.partner,
        )
        self.assertEqual(disclosure['gross_amount'], 3)
        self.assertEqual(disclosure['total'], 103)
        html = str(self.env['ir.qweb']._render(
            'account_payment_markup.payment_markup_disclosure',
            {'markup_disclosure': disclosure, 'currency': self.currency},
        ))
        self.assertEqual(html.count('data-markup-disclosure="server"'), 1)
        self.assertIn('data-markup-total="103.0"', html)

    def test_native_done_and_refund_child_lifecycle(self):
        from unittest.mock import patch

        tx = self._transaction()
        tx._get_processing_values()
        self.assertEqual(tx._set_done(), tx)
        self.assertEqual(tx.state, 'done')
        self.assertFalse(tx._set_done(state_message='Repeated completion'))
        self.provider.state = 'test'

        def complete(refund):
            # Evidence must exist before the provider receives the request.
            self.assertEqual(refund.markup_refund_source_id, tx)
            self.assertEqual(refund.markup_refund_pending_amount, abs(refund.amount))
            refund._set_done(state_message='Synthetic refund')

        with patch.object(type(tx), '_send_refund_request', complete):
            first = tx._refund(1)
        self.assertEqual(first.state, 'done')
        self.assertEqual(tx.markup_refunded_amount, 1)
        first._set_done()
        self.assertEqual(tx.markup_refunded_amount, 1)
        self.assertEqual(tx.markup_refund_pending_amount, 0)

        # A pending child reserves capacity until a real terminal transition.
        with patch.object(type(tx), '_send_refund_request', lambda refund: None):
            pending = tx._refund(2)
        self.assertEqual(tx.markup_refund_pending_amount, 2)
        pending._set_error('Synthetic decline')
        pending._set_error('Repeated decline')
        self.assertEqual(tx.markup_refund_pending_amount, 0)
        with patch.object(type(tx), '_send_refund_request', lambda refund: None):
            canceled = tx._refund(2)
        canceled._set_canceled(state_message='Synthetic cancel')
        self.assertEqual(tx.markup_refund_pending_amount, 0)
        with patch.object(type(tx), '_send_refund_request', complete):
            final = tx._refund(2)
        self.assertEqual(final.state, 'done')
        self.assertEqual(tx.markup_refunded_amount, 3)
        self.assertEqual(tx.markup_refund_pending_amount, 0)

    def test_rejected_cancel_does_not_remove_fee(self):
        tx = self._transaction()
        tx._get_processing_values()
        tx._set_done()
        fee = tx.markup_fee_move_id
        self.assertFalse(tx._set_canceled())
        self.assertEqual(fee.state, 'draft')
        self.assertEqual(tx._set_canceled(extra_allowed_states=('done',)), tx)
        self.assertEqual(fee.state, 'cancel')
        self.assertEqual(tx._set_done(extra_allowed_states=('cancel',)), tx)

    def test_disabled_checkout_remains_principal_without_fee(self):
        self.fee.markup_percent = 0
        tx = self._transaction()
        for _ in range(2):
            values = tx._get_processing_values()
            self.assertEqual(values['amount'], 100)
            self.assertEqual(values['markup_amount'], 0)
            self.assertFalse(tx.markup_fee_move_id)
        self.assertEqual(tx.amount, 100)
        self.assertEqual(self.invoice.amount_total, 100)
        disclosure = self.fee._markup_details(
            100, self.currency, self.partner,
        )
        self.assertFalse(disclosure['amount'])
        self.assertEqual(disclosure['total'], 100)
