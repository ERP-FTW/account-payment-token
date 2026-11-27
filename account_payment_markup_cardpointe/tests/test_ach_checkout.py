"""Optional real online-adapter regressions, with all gateway traffic fenced."""
import secrets
from types import SimpleNamespace
from unittest.mock import patch
from lxml import html

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools.mail import is_html_empty


@tagged('post_install', '-at_install')
class TestOnlineManualFee(TransactionCase):
    def setUp(self):
        super().setUp()
        if not self.env.ref('payment_cardpointe_ach.payment_method_ach', raise_if_not_found=False):
            self.skipTest('Optional online ACH adapter not installed')
        self.company = self.env.company
        self.company.country_id = self.env.ref('base.us')
        self.company.currency_id = self.env.ref('base.USD')
        def account(code, kind, reconcile=False):
            return self.env['account.account'].create({
                'name': code, 'code': code, 'account_type': kind,
                'company_ids': [(6, 0, self.company.ids)], 'reconcile': reconcile})
        self.income = account('ONFI', 'income')
        self.receivable = account('ONFR', 'asset_receivable', True)
        outstanding = account('ONFO', 'asset_current', True)
        self.partner = self.env['res.partner'].create({
            'name': 'Synthetic online fee payer', 'country_id': self.env.ref('base.us').id,
            'property_account_receivable_id': self.receivable.id})
        self.product = self.env['product.product'].create({
            'name': 'Synthetic online fee', 'type': 'service',
            'property_account_income_id': self.income.id, 'taxes_id': [(5, 0, 0)]})
        self.sale = self.env['account.journal'].create({'name': 'Online sales', 'code': 'ONFS', 'type': 'sale'})
        self.bank = self.env['account.journal'].create({'name': 'Online bank', 'code': 'ONFB', 'type': 'bank'})
        self.p = self.env.ref('payment_cardpointe.payment_provider_cardpointe')
        self.ach = self.env.ref('payment_cardpointe_ach.payment_method_ach')
        self.card = self.env.ref('payment.payment_method_card')
        self.cfg = self.env['cardpointe.merchant.config'].create({
            'name': 'Synthetic online fee config', 'mid': 'LAB-NONROUTABLE',
            # Render only; HTTP is fenced below and no browser loads this URL.
            'tokenizer_url': 'https://synthetic.cardconnect.com/itoke/ajax-tokenizer.html'})
        self.p.write({'state': 'test', 'is_published': True, 'journal_id': self.bank.id,
            'cardpointe_merchant_config_id': self.cfg.id,
            'payment_method_ids': [(6, 0, (self.ach | self.card).ids)],
            'cp_ach_agreement_ref': 'Synthetic only',
            'cp_ach_consumer_checking': True})
        self.ach_fee = self.env['payment.method.fee'].create({
            'method_id': self.ach.id, 'provider_id': self.p.id, 'markup_percent': 3,
            'markup_product_id': self.product.id})
        self.card_fee = self.env['payment.method.fee'].create({
            'method_id': self.card.id, 'provider_id': self.p.id, 'use_cardpointe_program': True})
        self.bank.inbound_payment_method_line_ids.payment_account_id = outstanding
        # Supply only the normal Odoo request environment, not a fake access token.
        for ctx in [patch('odoo.addons.payment.utils.request', new=SimpleNamespace(env=self.env)),
                    patch('requests.sessions.Session.request', side_effect=AssertionError('Network forbidden'))]:
            ctx.start()
            self.addCleanup(ctx.stop)

    def transaction(self, amount=100.0, method=None, invoice=None):
        if invoice is None:
            invoice = self.env['account.move'].create({
                'move_type': 'out_invoice', 'partner_id': self.partner.id, 'journal_id': self.sale.id,
                'invoice_line_ids': [(0, 0, {'name': 'Synthetic sale', 'quantity': 1,
                    'price_unit': 100, 'account_id': self.income.id, 'tax_ids': [(5, 0, 0)]})]})
            invoice.action_post()
        tx = self.env['payment.transaction'].create({'provider_id': self.p.id,
            'payment_method_id': (method or self.ach).id, 'reference': secrets.token_hex(8),
            'amount': amount, 'currency_id': self.company.currency_id.id,
            'partner_id': self.partner.id, 'operation': 'online_direct',
            'invoice_ids': [(6, 0, invoice.ids)]})
        return tx

    def process(self, tx, consent_amount=None):
        return tx._cp_process(token=secrets.token_hex(16), consent=True,
            account_class='consumer_checking',
            consent_amount='%.2f' % (tx.amount if consent_amount is None else consent_amount))

    def rendered_consent_amount(self, principal):
        rendered = self.env['ir.qweb']._render('payment.method_form', {
            'providers_sudo': self.p, 'pm_sudo': self.ach, 'amount': principal,
            'mode': 'payment', 'is_selected': True, 'payment_methods_sudo': self.ach,
            'currency': self.company.currency_id, 'partner_id': self.partner.id,
            'is_html_empty': is_html_empty,
        })
        node = html.fromstring(str(rendered)).xpath('//div[contains(@class,"o_cp_ach")]')[0]
        amount = node.get('data-consent-amount')
        self.assertIn('%s USD' % amount, node.text_content())
        return float(amount)

    def approval(self, tx, payload):
        return {'ok': True, 'data': {'respstat': 'A', 'retref': 'LAB-MANUAL-FEE',
            'merchid': tx.cp_merchant_id, 'orderid': tx.cp_order_id, 'amount': payload['amount']}}

    def test_enrolled_card_and_manual_ach_disclosure_accounting_and_replay(self):
        tx = self.transaction()
        original = tx.invoice_ids
        consent_amount = self.rendered_consent_amount(100)
        self.assertEqual(consent_amount, 103)
        self.assertEqual(tx._get_processing_values()['amount'], 103)
        fee = tx.markup_fee_move_id
        self.assertEqual(tx._get_processing_values()['amount'], 103)
        self.assertEqual(tx.markup_fee_move_id, fee)
        self.assertEqual((fee.state, fee.amount_total), ('draft', 3))
        def send(method, endpoint, payload):
            self.assertEqual((method, endpoint), ('PUT', 'auth'))
            self.assertEqual(payload['amount'], '103.00')
            self.assertEqual(payload['accttype'], 'ECHK')
            self.assertNotIn('fee_amount', payload)
            return self.approval(tx, payload)
        with patch.object(type(self.p), '_cp_online_request', side_effect=send) as call:
            self.assertEqual(self.process(tx, float(consent_amount))['state'], 'done')
            tx._cp_process()
            self.assertEqual(call.call_count, 1)
        self.assertIn('103.00 USD', tx.cp_ach_consent_text)
        self.assertEqual(tx.cp_submitted_amount, 103)
        tx._post_process()
        self.assertEqual(fee.state, 'posted')
        self.assertEqual(tx.payment_id.amount, 103)
        self.assertEqual(tx.payment_id.move_id.state, 'posted')
        self.assertEqual((original.amount_residual, fee.amount_residual), (0, 0))
        payment = tx.payment_id
        tx._post_process()
        self.assertEqual(tx.payment_id, payment)
        self.assertEqual(tx.markup_fee_move_id, fee)
        self.assertEqual(tx.invoice_ids, original | fee)


    def test_disclosed_fee_survives_a_later_rate_change(self):
        tx = self.transaction()
        self.assertEqual(tx._get_processing_values()['amount'], 103)
        fee = tx.markup_fee_move_id
        self.ach_fee.markup_percent = 2
        self.assertEqual(tx._get_processing_values()['amount'], 103)
        self.assertEqual(tx.markup_fee_move_id, fee)
        self.assertEqual(tx._get_payment_principal(), 100)
        self.assertEqual(self.transaction()._get_processing_values()['amount'], 102)

    def test_stale_consent_and_changed_fee_refused_before_transport(self):
        for changed in ('consent', 'total', 'fee', 'missing', 'foreign', 'tax', 'principal'):
            with self.subTest(changed=changed), self.cr.savepoint():
                tx = self.transaction()
                tx._get_processing_values()
                if changed == 'total':
                    tx.amount = 104
                elif changed == 'fee':
                    tx.markup_fee_move_id.invoice_line_ids.price_unit = 4
                elif changed == 'missing':
                    tx.markup_fee_move_id = False
                elif changed == 'foreign':
                    tx.markup_fee_move_id.ref = 'Another payment'
                elif changed == 'tax':
                    tx.markup_fee_move_id.invoice_line_ids.tax_ids = self.env['account.tax'].create({
                        'name': 'Changed fee tax', 'amount': 10, 'amount_type': 'percent', 'type_tax_use': 'sale'})
                elif changed == 'principal':
                    tx.write({'amount': 104, 'markup_base_amount': 101, 'markup_total_amount': 104})
                with patch.object(type(self.p), '_cp_online_request') as call:
                    with self.assertRaises(ValidationError):
                        self.process(tx, 100 if changed == 'consent' else None)
                    call.assert_not_called()
                self.assertFalse(tx.cp_order_id)
                tx._set_canceled()

    def test_original_invoice_ceiling_and_pending_attempt_still_enforced(self):
        excessive = self.transaction(amount=101)
        excessive._get_processing_values()
        with patch.object(type(self.p), '_cp_online_request') as call:
            with self.assertRaises(ValidationError):
                self.process(excessive)
            call.assert_not_called()
        excessive._set_canceled()
        tx = self.transaction()
        tx._get_processing_values()
        with patch.object(type(self.p), '_cp_online_request', return_value={}) as call:
            self.assertEqual(self.process(tx)['state'], 'pending')
            competitor = self.transaction(invoice=tx.invoice_ids)
            competitor._get_processing_values()
            with self.assertRaises(ValidationError):
                self.process(competitor)
            tx._cp_process()
            self.assertEqual([c.args[0] for c in call.call_args_list], ['PUT', 'GET'])
        self.assertEqual(tx.state, 'pending')

    def test_installment_then_exact_remaining_with_manual_fee(self):
        tx = self.transaction(amount=25)
        original = tx.invoice_ids
        self.assertEqual(tx._get_processing_values()['amount'], 25.75)
        consent_amount = self.rendered_consent_amount(25)
        self.assertEqual(consent_amount, 25.75)
        with patch.object(type(self.p), '_cp_online_request',
                          side_effect=lambda method, endpoint, payload: self.approval(tx, payload)) as call:
            self.assertEqual(self.process(tx, consent_amount)['state'], 'done')
            self.assertEqual(call.call_args.args[2]['amount'], '25.75')
        self.assertIn('25.75 USD', tx.cp_ach_consent_text)
        tx._post_process()
        self.assertEqual(tx.markup_fee_move_id.amount_total, 0.75)
        self.assertEqual(tx.markup_fee_move_id.amount_residual, 0)
        self.assertEqual(original.amount_residual, 75)
        # The fee exception must not admit a principal over the remaining balance.
        over = self.transaction(amount=75.01, invoice=original)
        over._get_processing_values()
        with patch.object(type(self.p), '_cp_online_request') as call:
            with self.assertRaises(ValidationError):
                self.process(over)
            call.assert_not_called()
        over._set_canceled()
        final = self.transaction(amount=75, invoice=original)
        self.assertEqual(final._get_processing_values()['amount'], 77.25)
        final_consent = self.rendered_consent_amount(75)
        self.assertEqual(final_consent, 77.25)
        with patch.object(type(self.p), '_cp_online_request',
                          side_effect=lambda method, endpoint, payload: self.approval(final, payload)) as call:
            self.assertEqual(self.process(final, final_consent)['state'], 'done')
            self.assertEqual(call.call_args.args[2]['amount'], '77.25')
        self.assertIn('77.25 USD', final.cp_ach_consent_text)
        final._post_process()
        self.assertEqual(final.markup_fee_move_id.amount_total, 2.25)
        self.assertEqual(final.markup_fee_move_id.amount_residual, 0)
        self.assertEqual(original.amount_residual, 0)
        self.assertEqual(tx.payment_id.amount + final.payment_id.amount, 103)

    def test_partial_taxed_ach_and_definite_decline(self):
        tax = self.env['account.tax'].create({
            'name': 'Synthetic excluded fee tax', 'amount': 10,
            'amount_type': 'percent', 'type_tax_use': 'sale'})
        self.product.taxes_id = tax
        tx = self.transaction(amount=25)
        self.assertEqual(tx._get_processing_values()['amount'], 25.83)
        fee = tx.markup_fee_move_id
        with patch.object(type(self.p), '_cp_online_request',
                          side_effect=lambda method, endpoint, payload: self.approval(tx, payload)):
            self.assertEqual(self.process(tx)['state'], 'done')
        tx._post_process()
        self.assertEqual(fee.amount_total, 0.83)
        self.assertEqual(fee.amount_residual, 0)
        self.assertEqual((tx.invoice_ids - fee).amount_residual, 75)
        self.assertEqual(tx.payment_id.amount, 25.83)
        self.product.taxes_id = False
        declined = self.transaction()
        declined._get_processing_values()
        with patch.object(type(self.p), '_cp_online_request', side_effect=lambda *args: {
                'ok': True, 'data': {'respstat': 'C', 'merchid': declined.cp_merchant_id,
                                     'orderid': declined.cp_order_id}}) as call:
            self.assertEqual(self.process(declined)['state'], 'error')
            declined._cp_process()
            self.assertEqual(call.call_count, 1)
        self.assertEqual(declined.markup_fee_move_id.state, 'cancel')
        self.assertFalse(declined.payment_id)
