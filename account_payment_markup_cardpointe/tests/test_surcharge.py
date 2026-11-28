"""Synthetic transport/ORM fixtures. No gateway credential or live transaction."""
import secrets
from types import SimpleNamespace
from unittest.mock import patch
from odoo.tests import TransactionCase, tagged
from odoo.addons.account_payment_markup_cardpointe.services.surcharge import money, UnusableSurcharge
from odoo.addons.payment_cardpointe_base.models import cardpointe_client


@tagged('post_install', '-at_install')
class TestSurcharge(TransactionCase):
    def setUp(self):
        super().setUp()
        self.p = self.env['payment.provider'].create({
            'name': 'Synthetic CardPointe checkout', 'code': 'cardpointe',
            'inline_form_view_id': self.env.ref('payment_cardpointe.inline_form').id,
        })
        self.card = self.env.ref('payment.payment_method_card')
        self.cfg = self.env['cardpointe.merchant.config'].create({'name':'Synthetic program fixture', 'mid':'LAB-NONROUTABLE'})
        self.p.write({'state':'test','is_published':True,'cardpointe_merchant_config_id':self.cfg.id,'payment_method_ids':[(6,0,self.card.ids)]})
        self.card.use_cardpointe_program = True
        self.partner = self.env['res.partner'].create({'name':'Synthetic surcharge payer', 'country_id':self.env.ref('base.us').id})
        self.token = secrets.token_hex(16)
        ctx = patch('odoo.addons.payment.utils.request', new=SimpleNamespace(env=self.env))
        ctx.start()
        self.addCleanup(ctx.stop)

    def tx(self):
        return self.env['payment.transaction'].create({'provider_id':self.p.id,'payment_method_id':self.card.id,'reference':secrets.token_hex(8), 'amount':100,'currency_id':self.env.ref('base.USD').id,'partner_id':self.partner.id,'operation':'online_direct'})

    def quote(self, tx, data=None):
        with patch.object(type(self.p),'_cp_online_request',return_value={'ok':True,'data':data or {'token':'CREDIT','postal':'NOT_RESTRICTED','fee_amount':'3.00'}}) as call:
            result=tx._cp_process(self.token,postal='19000')
        self.assertEqual(call.call_args.args[:2], ('GET','surcharge'))
        self.assertEqual(call.call_args.args[2], {'merchid':'LAB-NONROUTABLE','token':self.token,'postal':'19000','amount':'100.00'})
        self.assertFalse(tx.cp_order_id)
        self.assertEqual(result['state'],'quote')
        return result['quote_id']

    def reply(self, tx, fee='3.00', total='103.00', kind='SURCHRG'):
        return {'ok':True,'data':{'merchid':self.cfg.mid,'orderid':tx.cp_order_id,'retref':'LAB-REFERENCE','respstat':'A','amount':total,'fee_amount':fee,'fee_type':kind}}

    def test_first_acceptance_quote_auth_receipt_readback(self):
        for quote, fee, total, kind in [
            ({'token':'CREDIT','postal':'NOT_RESTRICTED','fee_amount':'3.00'},'3.00','103.00','SURCHRG'),
            ({'token':'CREDIT','postal':'NOT_RESTRICTED','fee_amount':'0.00'},'0.00','100.00','SURCHRG_WAIVED'),
            ({'token':'DEBIT','postal':'NOT_RESTRICTED'},'0.00','100.00','SURCHRG_WAIVED'),
            ({'token':'CREDIT','postal':'RESTRICTED'},'0.00','100.00','SURCHRG_WAIVED'),
        ]:
            with self.subTest(quote=quote):
                tx=self.tx(); quote_id=self.quote(tx,quote)
                def send(method,endpoint,payload):
                    self.assertEqual((method,endpoint),('PUT','auth'))
                    self.assertEqual(payload['amount'],'100.00')
                    self.assertNotIn('fee_amount',payload)
                    return self.reply(tx,fee,total,kind)
                with patch.object(type(self.p),'_cp_online_request',side_effect=send) as call:
                    tx._cp_process(self.token,postal='19000',quote_id=quote_id)
                    tx._cp_process()
                self.assertEqual(call.call_count,1)
                self.env.flush_all(); self.env.invalidate_all()
                tx=self.env['payment.transaction'].browse(tx.id)
                self.assertEqual(tx.state,'done')
                self.assertEqual((tx.cp_receipt['subtotal'],tx.cp_receipt['fee'],tx.cp_receipt['total']),('100.00',fee,total))
                self.assertNotIn(self.token,str(tx.read(['cp_quote','cp_receipt'])))

    def test_definite_decline_releases_payer(self):
        for decline_fields in [{}, {'amount': '0.00'}, {
                'amount': '100.00', 'fee_amount': '0.00',
                'fee_type': 'SURCHRG_WAIVED'}]:
            with self.subTest(decline_fields=decline_fields):
                tx = self.tx()
                qid = self.quote(tx)

                def decline(method, endpoint, payload):
                    self.assertEqual((method, endpoint), ('PUT', 'auth'))
                    self.assertEqual(payload['amount'], '100.00')
                    return {'ok': True, 'data': {
                        'merchid': self.cfg.mid, 'orderId': tx.cp_order_id,
                        'respstat': 'C', **decline_fields}}

                with patch.object(type(self.p), '_cp_online_request', side_effect=decline) as call:
                    self.assertEqual(tx._cp_process(
                        self.token, postal='19000', quote_id=qid)['state'], 'error')
                    self.assertEqual(tx._cp_process()['state'], 'error')
                self.assertEqual(call.call_count, 1)
                self.env.flush_all()
                self.env.invalidate_all()
                tx = self.env['payment.transaction'].browse(tx.id)
                self.assertEqual(tx.state, 'error')
                self.assertFalse(tx.cp_receipt)
                # A new attempt by the same payer must not be refused as unresolved.
                following = self.tx()
                following_qid = self.quote(following)
                with patch.object(type(self.p), '_cp_online_request',
                                  side_effect=lambda *a, **k: self.reply(following)) as call:
                    self.assertEqual(following._cp_process(
                        self.token, postal='19000', quote_id=following_qid)['state'], 'done')
                self.assertEqual(call.call_count, 1)

    def test_nonfinal_or_foreign_response_stays_pending(self):
        for response_fields in [
                {'respstat': 'B'},
                {'respstat': 'C', 'merchid': 'OTHER-LAB-MERCHANT'},
                {'respstat': 'C', 'orderid': 'OTHER-LAB-ORDER'},
                {'respstat': 'A', 'merchid': 'OTHER-LAB-MERCHANT'}]:
            with self.subTest(response_fields=response_fields), self.cr.savepoint():
                tx = self.tx()
                qid = self.quote(tx)
                def reply(*args):
                    return {'ok': True, 'data': {
                        'merchid': self.cfg.mid, 'orderid': tx.cp_order_id,
                        **response_fields}}
                with patch.object(type(self.p), '_cp_online_request', side_effect=reply) as call:
                    tx._cp_process(self.token, postal='19000', quote_id=qid)
                    tx._cp_process()
                self.assertEqual([c.args[0] for c in call.call_args_list], ['PUT', 'GET'])
                self.assertEqual(tx.state, 'pending')
                self.assertFalse(tx.cp_receipt)

    def test_invalid_quotes_zero_auth(self):
        for data in [{},{'token':'UNKNOWN','postal':'NOT_RESTRICTED'}, {'token':'CREDIT','postal':'NOT_RESTRICTED'},
                     {'token':'CREDIT','postal':'NOT_RESTRICTED','fee_amount':'NaN'},
                     {'token':'DEBIT','postal':'NOT_RESTRICTED','fee_amount':'3.00'},
                     {'token':'CREDIT','postal':'NOT_RESTRICTED','fee_amount':'3.001'}]:
            with self.subTest(data=data):
                tx=self.tx()
                with patch.object(type(self.p),'_cp_online_request',return_value={'ok':True,'data':data}) as call:
                    self.assertEqual(tx._cp_process(self.token,postal='19000')['state'],'quote_error')
                self.assertEqual(call.call_count,1); self.assertFalse(tx.cp_order_id)
        tx=self.tx()
        with patch.object(type(self.p),'_cp_online_request',side_effect=TimeoutError):
            self.assertEqual(tx._cp_process(self.token,postal='19000')['state'],'quote_error')
        self.assertFalse(tx.cp_order_id)

    def test_changed_card_gets_a_new_quote(self):
        tx = self.tx()
        old_quote = self.quote(tx)
        with patch.object(type(self.p), '_cp_online_request', return_value={'ok': True, 'data': {
                'token': 'DEBIT', 'postal': 'NOT_RESTRICTED'}}) as gateway:
            result = tx._cp_process(secrets.token_hex(16), postal='19000', quote_id=old_quote)
        self.assertEqual(result['state'], 'quote')
        self.assertEqual(result['quote']['total'], '100.00')
        self.assertNotEqual(result['quote_id'], old_quote)
        self.assertEqual(gateway.call_args.args[:2], ('GET', 'surcharge'))
        self.assertFalse(tx.cp_order_id)

    def test_ambiguous_auth_inquiry_never_recharges(self):
        tx=self.tx(); qid=self.quote(tx)
        with patch.object(type(self.p),'_cp_online_request',return_value={}) as call:
            tx._cp_process(self.token,postal='19000',quote_id=qid)
            tx._cp_process(); tx._cp_process()
        self.assertEqual([x.args[0] for x in call.call_args_list],['PUT','GET','GET'])
        self.assertTrue(all(tx.cp_order_id in x.args[1] and self.cfg.mid in x.args[1] for x in call.call_args_list[1:]))
        self.assertEqual(tx.state,'pending')
        with patch.object(type(self.p),'_cp_online_request',return_value=self.reply(tx)) as call:
            tx._cp_process(); tx._cp_process()
        self.assertEqual(call.call_count,1); self.assertEqual(tx.state,'done')

    def test_receipt_records_actual_gateway_fee(self):
        tx = self.tx()
        quote_id = self.quote(tx)
        with patch.object(type(self.p), '_cp_online_request',
                          side_effect=lambda *args: self.reply(tx, '4.00', '104.00')) as gateway:
            self.assertEqual(tx._cp_process(self.token, postal='19000', quote_id=quote_id)['state'], 'done')
            tx._cp_process()
        self.assertEqual(gateway.call_count, 1)
        self.assertEqual(tx.provider_reference, 'LAB-REFERENCE')
        self.assertEqual((tx.cp_receipt['fee'], tx.cp_receipt['total']), ('4.00', '104.00'))

    def test_surcharge_http_uses_query_not_body_and_sanitizes_error(self):
        secret=secrets.token_hex(16)
        with patch.object(cardpointe_client,'request_json',return_value=(200,{},'',{})) as call:
            self.p._cp_online_request('GET','surcharge',{'token':secret,'amount':'100.00'})
        self.assertIsNone(call.call_args.kwargs['json'])
        self.assertEqual(call.call_args.kwargs['params']['token'],secret)
        with patch.object(cardpointe_client,'request_json',side_effect=cardpointe_client.CardPointeRequestError('request failed '+secret)), patch.object(cardpointe_client,'_logger') as log:
            result=self.p._cp_online_request('GET','surcharge',{'token':secret})
        self.assertNotIn(secret,str(result)); self.assertNotIn(secret,str(log.mock_calls))


    def test_malformed_gateway_amounts(self):
        for fee in ['-1','NaN',None,True]:
            with self.assertRaises(UnusableSurcharge):
                money(fee)
