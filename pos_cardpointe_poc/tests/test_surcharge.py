"""Synthetic terminal transport + persisted receipt, never gateway UAT."""
from unittest.mock import patch
from odoo import Command
from odoo.tests import tagged
from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon
from odoo.addons.payment_cardpointe_base.services.surcharge import agreement
from odoo.addons.pos_cardpointe_poc.services.terminal_recovery import resolve_terminal_outcome
from .cardpointe_fake import FakeCardPointe, TIMEOUT


@tagged('post_install', '-at_install', 'cardpointe_program')
class TestTerminalProgram(TestPointOfSaleHttpCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.merchant = cls.env['cardpointe.merchant.config'].create({'name':'Synthetic program merchant','mid':'LAB-NONROUTABLE', 'surcharge_program':True, 'gateway_username':'fixture-only', 'gateway_password':'fixture-only'})
        cls.terminal = cls.env['pos.cardpointe.terminal.config'].create({'name':'Synthetic terminal','merchant_config_id':cls.merchant.id,'device_serial':'LAB-NONROUTABLE','auth_key':'fixture-only','signature_mode':'never'})
        cls.pm = cls.env['pos.payment.method'].create({'name':'Synthetic CardPointe program','journal_id':cls.bank_journal.id,'use_payment_terminal':'cardpointe_poc','cardpointe_config_id':cls.terminal.id})
        cls.main_pos_config.write({'payment_method_ids':[Command.link(cls.pm.id)]})

    def setUp(self):
        super().setUp()
        self.fake=FakeCardPointe(); self.fake.patch_transport(self)
        self.authenticate('pos_user','pos_user')

    def start(self):
        result=self.make_jsonrpc_request('/pos_cardpointe_poc/start',{'pos_config_id':self.main_pos_config.id,'payment_method_id':self.pm.id,'amount':100,'currency':'USD','order_uid':'synthetic-order','payment_line_uuid':'synthetic-line'})
        self.assertEqual(result['status'],'ready',result)
        return result

    def auth(self,started):
        return self.make_jsonrpc_request('/pos_cardpointe_poc/auth',{'request_id':started['request_id'],'amount':100})

    def inquiry(self,started):
        return self.make_jsonrpc_request('/pos_cardpointe_poc/inquire',{'payment_method_id':self.pm.id,'terminal_order_id':started['terminal_order_id'],'amount':100})

    def test_terminal_nonzero_zero_receipt_persistence(self):
        self.main_pos_config.open_ui()
        for fee,total,kind in [('3.00','103.00','SURCHRG'),('0.00','100.00','SURCHRG_WAIVED')]:
            self.fake.auth_card.update(amount=total,fee_amount=fee,fee_type=kind,merchid=self.merchant.mid)
            started=self.start(); self.fake.auth_card['orderid']=started['terminal_order_id']
            result=self.auth(started)
            self.assertEqual(result['status'],'approved',result)
            self.assertEqual(self.fake.paths('v4/authCard')[-1]['json']['amount'],'10000')
            self.assertFalse(self.fake.paths('rest/surcharge'))
            receipt=result['program_receipt']
            self.assertEqual((receipt['subtotal'],receipt['fee'],receipt['total']),('100.00',fee,total))
            order=self.env['pos.order'].create({'session_id':self.main_pos_config.current_session_id.id,'amount_tax':0,'amount_total':100,'amount_paid':100,'amount_return':0})
            payment=self.env['pos.payment'].create({'pos_order_id':order.id,'payment_method_id':self.pm.id,'amount':100,'cardpointe_retref':result['retref'],'cardpointe_terminal_order_id':started['terminal_order_id'],'cardpointe_program_receipt':receipt})
            self.env.flush_all(); self.env.invalidate_all()
            self.assertEqual(self.env['pos.payment'].browse(payment.id).cardpointe_program_receipt,receipt)
            self.assertNotIn('token',receipt)

    def test_unusable_terminal_fee_stays_unknown(self):
        for change in [{'fee_amount':'bad','fee_type':'SURCHRG','amount':'103.00'}, {'fee_amount':'3.00','fee_type':'SURCHRG_WAIVED','amount':'103.00'}]:
            self.fake.auth_card.update(change)
            started=self.start(); result=self.auth(started)
            self.assertEqual(result['status'],'unknown',result)
            before=len(self.fake.paths('v4/authCard'))
            self.assertEqual(self.inquiry(started)['status'],'unknown')
            self.assertEqual(len(self.fake.paths('v4/authCard')),before)

    def test_timeout_original_identity_recovery_and_not_found_hold(self):
        self.fake.auth_card_error=TIMEOUT
        started=self.start()
        self.assertEqual(self.auth(started)['status'],'unknown')
        self.assertEqual(self.inquiry(started)['status'],'unknown')
        self.fake.inquire_by_orderid={'respstat':'A','retref':'LAB-RECOVERED','amount':'103.00','fee_amount':'3.00','fee_type':'SURCHRG','merchid':self.merchant.mid,'orderid':started['terminal_order_id']}
        result=self.inquiry(started)
        self.assertEqual(result['status'],'approved',result)
        self.assertEqual(result['program_receipt']['fee'],'3.00')
        self.assertEqual(len(self.fake.paths('v4/authCard')),1)
        self.fake.inquire_by_orderid['merchid']='OTHER'
        self.assertEqual(self.inquiry(started)['status'],'unknown')
