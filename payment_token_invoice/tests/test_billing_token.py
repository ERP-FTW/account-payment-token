"""Billing-role regression: provider metadata is not charge authority."""
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.addons.account_payment.tests.common import AccountPaymentCommon


@tagged('post_install', '-at_install')
class TestBillingToken(AccountPaymentCommon):
    def setUp(self):
        super().setUp()
        self.billing = self._prepare_user(self.internal_user, 'account.group_account_invoice')
        self.invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.partner.id,
            'invoice_date': fields.Date.today(),
            'journal_id': self.company_data['default_journal_sale'].id,
            'invoice_line_ids': [Command.create({
                'name': 'Synthetic service', 'price_unit': 10,
                'account_id': self.company_data['default_account_revenue'].id,
                'tax_ids': [Command.clear()],
            })],
        })
        self.invoice.action_post()
        self.token = self._create_token()
        self.wizard = self.env['account.invoice.token.wizard'].with_user(self.billing).create({
            'invoice_id': self.invoice.id, 'token_id': self.token.id, 'amount': 10,
        })

    def test_billing_charge_keeps_caller_and_provider_private(self):
        self.assertFalse(self.billing.has_group('base.group_system'))
        with self.assertRaises(AccessError):
            self.provider.with_user(self.billing).read(['code'])
        observed = []

        def capture(tx):
            observed.append((tx.env.uid, tx.env.su, tx.amount, tx.token_id.id))
            tx._set_done()

        with patch.object(type(self.env['payment.transaction']), '_send_payment_request', capture):
            self.wizard.action_charge_with_token()
        self.assertEqual(observed, [(self.billing.id, False, 10, self.token.id)])
        tx = self.env['payment.transaction'].search([('invoice_ids', 'in', self.invoice.ids)])
        self.assertEqual(len(tx), 1)
        self.assertEqual(tx.state, 'done')
        self.assertFalse(self.provider.with_user(self.billing).has_access('write'))

    def test_wrong_customer_refused_before_charge(self):
        self.token.partner_id = self.env['res.partner'].create({'name': 'Other customer'})
        with patch.object(type(self.env['payment.transaction']), '_send_payment_request') as send:
            with self.assertRaises(UserError):
                self.wizard.action_charge_with_token()
            send.assert_not_called()
        self.assertFalse(self.env['payment.transaction'].search([('invoice_ids', 'in', self.invoice.ids)]))
