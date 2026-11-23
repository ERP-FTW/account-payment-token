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
        cls.provider = cls.env.ref('payment_cardpointe.payment_provider_cardpointe')
        cls.ach = cls.env.ref('payment_cardpointe_ach.payment_method_ach')
        cls.card = cls.env.ref('payment.payment_method_card')
        cls.usd = cls.env.ref('base.USD')
        cls.provider.write({'state':'test','is_published':True,'cardpointe_mid':'LAB-NONROUTABLE',
            'cardpointe_api_base':'https://fts-uat.cardconnect.com/cardconnect/rest/',
            'cardpointe_tokenizer_url':'https://fts-uat.cardconnect.com/itoke/ajax-tokenizer.html',
            'payment_method_ids':[(6,0,[cls.ach.id,cls.card.id])],
            'cp_ach_capability_ref':'Synthetic capabilities; NOT merchant evidence',
            'cp_ach_agreement_ref':'Synthetic agreement; NOT merchant evidence',
            'cp_ach_consumer_checking':True,'cp_ach_consumer_savings':True,
            'cp_ach_business_checking':True,'cp_ach_business_savings':True})
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

    def test_enabled_combinations_payload_and_consent(self):
        for key, acct, sec in [('consumer_checking','ECHK','WEB'),('consumer_savings','ESAV','WEB'),('business_checking','ECHK','CCD'),('business_savings','ESAV','CCD')]:
            with self.subTest(account_class=key):
                self.partner.write({'is_company':sec=='CCD','cp_ach_ccd_agreement_ref':'Synthetic payer agreement' if sec=='CCD' else False})
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

    def test_declared_method_must_match_saved_transaction(self):
        for method, other in [(self.card, self.ach), (self.ach, self.card)]:
            tx = self.tx(method)
            with patch.object(type(self.provider), "_cp_online_request") as call:
                for declared in [None, "", other.code]:
                    with self.assertRaises(ValidationError):
                        tx._cp_check_payment_method(declared)
                tx._cp_check_payment_method(method.code)
            call.assert_not_called()
            self.assertFalse(tx.cp_order_id)
            self.assertFalse(tx.cp_ach_consent_text)

    def test_declined(self):
        tx=self.tx()
        with patch.object(type(self.provider),'_cp_online_request',side_effect=lambda *a,**k:self.answer(tx,'C')) as call:
            self.submit(tx)
            self.submit(tx)
        self.assertEqual(tx.state,'error')
        self.assertEqual(call.call_count,1)
        self.assertFalse(tx.provider_reference)

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
        for change in [{'amount':'11.00'},{'orderid':'other'},{'merchid':'other'},{'retref':None},{'amount':None},{'respstat':'B'},{'currency':'EUR'}]:
            with self.subTest(change=change):
                tx=self.tx()
                with patch.object(type(self.provider),'_cp_online_request',side_effect=lambda *a,**k:self.answer(tx,**change)):
                    self.submit(tx)
                    self.assertEqual(tx.state,'pending')
                tx._set_canceled('Test isolation')

    def test_consent_currency_and_capability_fail_closed(self):
        tx=self.tx()
        with patch.object(type(self.provider),'_cp_online_request') as call:
            for kw in [{'consent':False},{'consent':'true'},{'consent_amount':'11.00'},{'account_class':'unsupported'}]:
                with self.assertRaises(ValidationError): self.submit(tx,**kw)
            tx.currency_id=self.env.ref('base.EUR')
            with self.assertRaises(ValidationError): self.submit(tx)
            tx.currency_id=self.usd
            self.provider.cp_ach_capability_ref=False
            with self.assertRaises(ValidationError): self.submit(tx)
            self.provider.cp_ach_capability_ref='Synthetic'
            self.provider.cp_ach_agreement_ref=False
            with self.assertRaises(ValidationError): self.submit(tx,account_class='business_checking')
            self.provider.cp_ach_agreement_ref='Synthetic merchant agreement'
            with self.assertRaises(ValidationError): self.submit(tx,account_class='business_checking')
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

    def test_unknown_blocks_new_transaction_across_methods(self):
        tx=self.tx()
        with patch.object(type(self.provider),'_cp_online_request',return_value={}) as call:
            self.submit(tx)
            with self.assertRaises(ValidationError): self.submit(self.tx(self.card))
        self.assertEqual(call.call_count,1)

    def test_changed_merchant_or_amount_never_resends(self):
        tx=self.tx()
        with patch.object(type(self.provider),'_cp_online_request',return_value={}): self.submit(tx)
        with patch.object(type(self.provider),'_cp_online_request') as call:
            self.provider.cardpointe_mid='CHANGED'
            self.submit(tx)
            self.provider.cardpointe_mid=tx.cp_merchant_id
            self.provider.cardpointe_api_base='https://changed.example.invalid/'
            self.submit(tx)
            self.provider.cardpointe_api_base='https://fts-uat.cardconnect.com/cardconnect/rest/'
            tx.currency_id=self.env.ref('base.EUR')
            self.submit(tx)
            tx.currency_id=self.usd
            tx.amount=20
            self.submit(tx)
        call.assert_not_called()
        self.assertEqual(tx.state,'pending')

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

    def test_invoice_duplicate_drafts_different_payers(self):
        receivable=self.env['account.account'].create({'name':'Synthetic receivable','code':'TESTREC','account_type':'asset_receivable','reconcile':True})
        income=self.env['account.account'].create({'name':'Synthetic income','code':'TESTINC','account_type':'income'})
        self.partner.property_account_receivable_id=receivable
        journal=self.env['account.journal'].create({'name':'Synthetic sales','code':'TSA','type':'sale'})
        invoice=self.env['account.move'].create({'move_type':'out_invoice','partner_id':self.partner.id,'journal_id':journal.id,
            'invoice_line_ids':[(0,0,{'name':'Synthetic item','quantity':1,'price_unit':10,'account_id':income.id,'tax_ids':[(5,0,0)]})]})
        invoice.action_post()
        first=self.tx(); second=self.tx()
        first.invoice_ids=invoice; second.invoice_ids=invoice
        second.partner_id=self.env['res.partner'].create({'name':'Another synthetic payer'})
        with patch.object(type(self.provider),'_cp_online_request',return_value={}) as call:
            self.submit(first)
            with self.assertRaises(ValidationError): self.submit(second)
        self.assertEqual(call.call_count,1)
        first._cp_apply_response(self.answer(first))
        with patch.object(type(self.provider),'_cp_online_request') as call:
            with self.assertRaises(ValidationError): self.submit(second)
        call.assert_not_called()

    def test_tokenizer_ach_mode_and_no_surcharge_dependency(self):
        url=self.provider._cp_tokenizer_url('cardpointe_ach')
        for param in ['fullmobilekeyboard=true','useexpiry=false','usecvv=false','cardnumbernumericonly=false']:
            self.assertIn(param,url)
        self.assertFalse(self.env['ir.module.module'].search([
            ('state', '=', 'installed'), '|',
            ('name', 'in', ['account_payment_markup', 'pos_payment_markup']),
            ('name', 'ilike', 'surcharge'),
        ]))
