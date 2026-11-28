import secrets
from unittest.mock import patch
from odoo.tests import TransactionCase, tagged
from odoo.exceptions import ValidationError
from odoo.addons.payment import utils
from odoo.addons.payment_cardpointe_base.models import cardpointe_client


@tagged('post_install', '-at_install')
class TestOnlineACH(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env['payment.provider'].create({
            'name': 'Synthetic CardPointe checkout', 'code': 'cardpointe',
            'inline_form_view_id': cls.env.ref('payment_cardpointe.inline_form').id,
        })
        cls.ach = cls.env.ref('payment_cardpointe_ach.payment_method_ach')
        cls.card = cls.env.ref('payment.payment_method_card')
        cls.usd = cls.env.ref('base.USD')
        cls.provider.write({'state':'test','is_published':True,'cardpointe_mid':'LAB-NONROUTABLE',
            'cardpointe_api_base':'https://fts-uat.cardconnect.com/cardconnect/rest/',
            'cardpointe_tokenizer_url':'https://fts-uat.cardconnect.com/itoke/ajax-tokenizer.html',
            'payment_method_ids':[(6,0,[cls.ach.id,cls.card.id])]})
        cls.partner = cls.env['res.partner'].create({'name':'Synthetic test payer','country_id':cls.env.ref('base.us').id})

    def tx(self, method=None, amount=10):
        return self.env['payment.transaction'].create({'provider_id':self.provider.id,'payment_method_id':(method or self.ach).id,
            'reference':secrets.token_hex(8),'amount':amount,'currency_id':self.usd.id,'partner_id':self.partner.id,'operation':'online_direct'})

    def submit(self, tx, **kwargs):
        values = dict(token=secrets.token_hex(16),consent=True,account_class='consumer_checking',consent_amount='10.00')
        values.update(kwargs)
        return tx._cp_process(**values)

    def answer(self, tx, status='A', **changes):
        data = dict(respstat=status,retref='LAB-REFERENCE',merchid=tx.cp_merchant_id,orderid=tx.cp_order_id,amount='10.00')
        data.update(changes)
        return {'ok':True,'data':data}

    def test_method_has_required_form_image(self):
        self.assertTrue(self.ach.image, 'The payment method form requires an image to save.')

    def test_enabled_combinations_payload_and_consent(self):
        for key, acct, sec in [('consumer_checking','ECHK','WEB'),('consumer_savings','ESAV','WEB'),('business_checking','ECHK','CCD'),('business_savings','ESAV','CCD')]:
            with self.subTest(account_class=key):
                tx=self.tx()
                def reply(method, endpoint, payload=None):
                    self.assertEqual((method,endpoint),('PUT','auth'))
                    self.assertEqual((payload['accttype'],payload['achEntryCode']),(acct,sec))
                    self.assertEqual(payload['amount'],'10.00')
                    self.assertNotIn('bankaba',payload)
                    self.assertNotIn('profile',payload)
                    if sec=='WEB': self.assertEqual(payload['achDescription'],'PURCHASE')
                    return self.answer(tx)
                with patch.object(type(self.provider),'_cp_online_request',side_effect=reply) as call:
                    self.submit(tx,account_class=key)
                self.assertEqual(call.call_count,1)
                self.assertEqual(tx.state,'done')
                self.assertTrue(tx.provider_reference)
                self.assertIn('10.00 USD',tx.cp_ach_consent_text)
                self.assertTrue(tx.cp_ach_consent_at)
                self.assertEqual(tx.cp_ach_consent_version,'one-time-ach-v1')
                self.assertEqual(tx.cp_ach_entry_code,sec)

    def test_declined(self):
        tx = self.tx()
        response = {'ok': True, 'data': {
            'respstat': 'C', 'respproc': 'PPS', 'respcode': '48',
            'resptext': 'Echeck not supported', 'retref': 'LAB-REFERENCE',
        }}
        with patch.object(type(self.provider), '_cp_online_request', return_value=response) as call:
            self.submit(tx)
            self.submit(tx)
        self.assertEqual(tx.state, 'error')
        self.assertEqual(tx.provider_reference, 'LAB-REFERENCE')
        self.assertIn('PPS 48 Echeck not supported', tx.state_message)
        self.assertEqual(call.call_count, 1)

    def test_unknown_retries_inquire_never_auth(self):
        tx=self.tx()
        with patch.object(type(self.provider),'_cp_online_request',return_value={}) as call:
            self.submit(tx)
            original=tx.cp_order_id
            for _ in range(3): self.submit(tx)
        self.assertEqual(tx.state,'pending')
        self.assertEqual(tx.cp_order_id,original)
        self.assertEqual([c.args[0] for c in call.call_args_list],['PUT','GET','GET','GET'])
        self.assertTrue(all(original in c.args[1] for c in call.call_args_list[1:]))

    def test_matching_inquiry_resolves_once(self):
        tx=self.tx()
        with patch.object(type(self.provider),'_cp_online_request',return_value={}): self.submit(tx)
        with patch.object(type(self.provider),'_cp_online_request',return_value=self.answer(tx)) as call:
            self.submit(tx)
            self.submit(tx)
        self.assertEqual(tx.state,'done')
        self.assertEqual(call.call_count,1)
        self.assertEqual(call.call_args.args[0],'GET')

    def test_wrong_identity_or_missing_reference_stays_unknown(self):
        # An inquiry must never resolve an attempt with conflicting order IDs.
        tx = self.tx()
        with patch.object(type(self.provider), '_cp_online_request', return_value={}):
            self.submit(tx)
        response = self.answer(tx, 'C', orderId='another-order')
        tx._cp_apply_response(response)
        self.assertEqual(tx.state, 'pending')
        del response['data']['orderid']
        tx._cp_apply_response(response)
        self.assertEqual(tx.state, 'pending')
        del response['data']['orderId']
        tx._cp_apply_response(response)
        self.assertEqual(tx.state, 'pending')
        tx._set_canceled('Test isolation')

        for change in [{'amount':'11.00'},{'orderid':'other'},{'merchid':'other'},{'retref':None},{'amount':None},{'respstat':'B'},{'currency':'EUR'}]:
            with self.subTest(change=change):
                tx=self.tx()
                with patch.object(type(self.provider),'_cp_online_request',side_effect=lambda *a,**k:self.answer(tx,**change)):
                    self.submit(tx)
                    expected = 'error' if 'amount' in change or 'currency' in change else 'pending'
                    self.assertEqual(tx.state, expected)
                if tx.state == 'pending':
                    tx._set_canceled('Test isolation')

    def test_echeck_decline_from_order_inquiry(self):
        tx = self.tx()
        with patch.object(type(self.provider), '_cp_online_request', return_value={}):
            self.submit(tx)
        response = {'ok': True, 'data': {
            'orderId': tx.cp_order_id, 'merchid': tx.cp_merchant_id,
            'retref': 'LAB-REFERENCE', 'respproc': 'PPS', 'respcode': '48',
            'respstat': 'C', 'setlstat': 'Declined', 'amount': '10.00',
        }}
        with patch.object(type(self.provider), '_cp_online_request', return_value=response) as gateway:
            self.assertEqual(tx._cp_process()['state'], 'error')
            self.assertEqual(tx._cp_process()['state'], 'error')
        gateway.assert_called_once_with('GET', f'inquireByOrderid/{tx.cp_order_id}/{tx.cp_merchant_id}')

    def test_consent_currency_and_account_type(self):
        tx=self.tx()
        with patch.object(type(self.provider),'_cp_online_request') as call:
            for kw in [{'consent':False},{'consent':'true'},{'consent_amount':'11.00'},{'account_class':'unsupported'}]:
                with self.assertRaises(ValidationError): self.submit(tx,**kw)
            tx.currency_id=self.env.ref('base.EUR')
            with self.assertRaises(ValidationError): self.submit(tx)
        call.assert_not_called()
        self.assertFalse(tx.cp_order_id)

    def test_three_method_configurations(self):
        for methods in [self.card,self.ach,self.card|self.ach]:
            self.provider.payment_method_ids=methods
            for method in methods:
                tx=self.tx(method)
                with patch.object(type(self.provider),'_cp_online_request',side_effect=lambda *a,**k:self.answer(tx)):
                    self.submit(tx)
                    self.assertEqual(tx.state,'done')
        self.provider.payment_method_ids=self.card
        with patch.object(type(self.provider),'_cp_online_request') as call:
            with self.assertRaises(ValidationError): self.submit(self.tx())
        call.assert_not_called()

    def test_unknown_attempt_does_not_freeze_payer(self):
        ach = self.tx()
        card = self.tx(self.card)
        with patch.object(type(self.provider), '_cp_online_request', return_value={}) as gateway:
            self.submit(ach)
            self.submit(card)
            self.submit(ach)
        self.assertNotEqual(ach.cp_order_id, card.cp_order_id)
        self.assertEqual([call.args[:2] for call in gateway.call_args_list], [
            ('PUT', 'auth'), ('PUT', 'auth'),
            ('GET', f'inquireByOrderid/{ach.cp_order_id}/{ach.cp_merchant_id}'),
        ])

    def test_inquiry_uses_the_original_merchant_id(self):
        tx = self.tx()
        with patch.object(type(self.provider), '_cp_online_request', return_value={}):
            self.submit(tx)
        self.provider.cardpointe_mid = 'CHANGED'
        with patch.object(type(self.provider), '_cp_online_request', return_value=self.answer(tx)) as call:
            self.submit(tx)
        call.assert_called_once_with('GET', f'inquireByOrderid/{tx.cp_order_id}/{tx.cp_merchant_id}')
        self.assertEqual(tx.state, 'done')

    def test_access_is_bound_to_transaction_provider_amount_partner(self):
        from types import SimpleNamespace
        request_patch = patch('odoo.addons.payment.utils.request', new=SimpleNamespace(env=self.env))
        request_patch.start()
        self.addCleanup(request_patch.stop)
        tx=self.tx()
        access=tx._get_specific_processing_values({})['access_token']
        self.assertTrue(utils.check_access_token(access,tx.reference,tx.partner_id.id,tx.amount,tx.currency_id.id,tx.provider_id.id))
        other=self.tx()
        self.assertFalse(utils.check_access_token(access,other.reference,other.partner_id.id,other.amount,other.currency_id.id,other.provider_id.id))
        self.assertFalse(utils.check_access_token(access,tx.reference,tx.partner_id.id,11,tx.currency_id.id,tx.provider_id.id))

    def test_transport_exception_holds_attempt(self):
        tx=self.tx()
        with patch.object(type(self.provider),'_cp_online_request',side_effect=TimeoutError):
            self.submit(tx)
        self.assertEqual(tx.state,'pending')
        self.assertTrue(tx.cp_submitted_at)

    def test_private_gateway_logs_no_nested_or_text_payment_data(self):
        secret=secrets.token_hex(16)
        self.provider.cardpointe_debug_logging=True
        for data,text in [({'error':{'account':secret}},''),({},secret)]:
            with patch.object(cardpointe_client,'request_json',return_value=(500,{},text,data)), patch.object(cardpointe_client,'_logger') as logger:
                self.provider._cp_online_request('PUT','auth',{'account':secret})
            self.assertNotIn(secret,str(logger.mock_calls))

    def test_wrong_merchant_company_refuses_before_send(self):
        company=self.env['res.company'].create({'name':'Synthetic other company'})
        cfg=self.env['cardpointe.merchant.config'].create({'name':'Synthetic mismatched config','company_id':company.id,'mid':'LAB-NONROUTABLE'})
        self.provider.cardpointe_merchant_config_id=cfg
        with patch.object(type(self.provider),'_cp_online_request') as call:
            with self.assertRaises(ValidationError): self.submit(self.tx())
        call.assert_not_called()

    def invoice(self, amount=100):
        receivable = self.env['account.account'].create({
            'name': 'Synthetic installment receivable', 'code': 'INSTREC',
            'account_type': 'asset_receivable', 'reconcile': True,
        })
        income = self.env['account.account'].create({
            'name': 'Synthetic installment income', 'code': 'INSTINC',
            'account_type': 'income',
        })
        self.partner.property_account_receivable_id = receivable
        journal = self.env['account.journal'].create({
            'name': 'Synthetic installment sales', 'code': 'INS', 'type': 'sale',
        })
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.partner.id,
            'journal_id': journal.id, 'currency_id': self.usd.id,
            'invoice_line_ids': [(0, 0, {
                'name': 'Synthetic installment item', 'quantity': 1,
                'price_unit': amount, 'account_id': income.id,
                'tax_ids': [(5, 0, 0)],
            })],
        })
        invoice.action_post()
        self.assertEqual(invoice.amount_residual, amount)
        return invoice

    def test_pending_ach_allows_card_on_another_invoice(self):
        invoice = self.invoice(amount=10)
        other_invoice = invoice.copy()
        other_invoice.action_post()
        ach = self.tx()
        ach.invoice_ids = invoice
        with patch.object(type(self.provider), '_cp_online_request', return_value={}):
            self.submit(ach)
        self.assertEqual(ach.state, 'pending')

        self.env['ir.config_parameter'].sudo().set_param('account_payment.enable_portal_payment', True)
        self.assertFalse(invoice._has_to_be_paid())
        self.assertTrue(other_invoice._has_to_be_paid())
        card = self.tx(self.card)
        card.invoice_ids = other_invoice
        with patch.object(type(self.provider), '_cp_online_request',
                          side_effect=lambda *args: self.answer(card)) as gateway:
            self.assertEqual(self.submit(card)['state'], 'done')
            self.assertEqual(self.submit(card)['state'], 'done')
        self.assertEqual([call.args[:2] for call in gateway.call_args_list], [('PUT', 'auth')])
        self.assertEqual(ach.state, 'pending')

    def test_partial_payment_uses_odoo_transaction_amount(self):
        tx = self.tx(amount=25)
        tx.invoice_ids = self.invoice(amount=100)
        with patch.object(type(self.provider), '_cp_online_request',
                          side_effect=lambda *args: self.answer(tx, amount='25.00')) as gateway:
            self.submit(tx, consent_amount='25.00')
        self.assertEqual(gateway.call_args.args[2]['amount'], '25.00')
        self.assertEqual(tx.state, 'done')

    def render_consent(self, method=None, principal=100):
        from lxml import html
        rendered = self.env['ir.ui.view']._render_template(
            'payment_cardpointe.inline_form', {
                'provider_sudo': self.provider, 'pm_sudo': method or self.ach,
                'amount': principal, 'currency': self.usd, 'partner_id': self.partner.id,
            })
        return html.fromstring(str(rendered))



    def test_tokenizer_ach_mode(self):
        url=self.provider._cp_tokenizer_url('cardpointe_ach')
        for param in ['fullmobilekeyboard=true','useexpiry=false','usecvv=false','cardnumbernumericonly=false']:
            self.assertIn(param,url)
