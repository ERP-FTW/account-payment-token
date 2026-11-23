"""Explicit disposable-lab setup only; run via odoo-lab shell, never imported.

Builds synthetic records from the real fee-validation paths with fixture transports.
The accompanying browser checks are fixture UI proof, NOT processor tests.
"""
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode
from odoo.addons.payment import utils
from odoo.addons.pos_cardpointe_poc.services.cardpointe_terminal import CardPointeTerminalClient

assert env.cr.dbname.startswith('guild_'), 'Disposable lab only'
company=env.company
company.write({'name':'Synthetic program company','country_id':env.ref('base.us').id,'currency_id':env.ref('base.USD').id})
merchant=env['cardpointe.merchant.config'].search([('name','=','Synthetic browser program')],limit=1)
if not merchant:
    merchant=env['cardpointe.merchant.config'].create({'name':'Synthetic browser program','mid':'LAB-NONROUTABLE','surcharge_program':True,'gateway_base_url':'https://example.invalid/cardconnect/rest/'})
terminal=env['pos.cardpointe.terminal.config'].search([('name','=','Synthetic browser terminal')],limit=1)
if not terminal:
    terminal=env['pos.cardpointe.terminal.config'].create({'name':'Synthetic browser terminal','merchant_config_id':merchant.id,'device_serial':'LAB-NONROUTABLE','auth_key':'fixture-only','signature_mode':'never'})
bank=env['account.journal'].search([('type','=','bank')],limit=1)
pm=env['pos.payment.method'].search([('name','=','Synthetic program card')],limit=1)
if not pm:
    pm=env['pos.payment.method'].create({'name':'Synthetic program card','journal_id':bank.id,'use_payment_terminal':'cardpointe_poc','cardpointe_config_id':terminal.id})
config=env['pos.config'].search([('name','=','Synthetic program POS')],limit=1)
if not config:
    config=env['pos.config'].create({'name':'Synthetic program POS','payment_method_ids':[(6,0,pm.ids)],'cash_control':False,'iface_print_auto':False})
cashier=env['res.users'].search([('login','=like','lab_sales_%'),('groups_id','in',env.ref('point_of_sale.group_pos_user').id)],limit=1)
assert cashier and not cashier.has_group('base.group_system'), 'Provision lab POS user first'
if not config.current_session_id:
    config.with_user(cashier).open_ui()
config.current_session_id.with_user(cashier).set_opening_control(0, 'Synthetic fixture opening')
product=env['product.product'].search([('name','=','Synthetic program item')],limit=1)
if not product:
    product=env['product.product'].create({'name':'Synthetic program item','list_price':100,'available_in_pos':True,'type':'consu','taxes_id':[(5,0,0)]})
order=env['pos.order'].search([('pos_reference','=','SYNTHETIC-PROGRAM-RECEIPT')],limit=1)
if not order:
    order=env['pos.order'].create({'session_id':config.current_session_id.id,'pos_reference':'SYNTHETIC-PROGRAM-RECEIPT','amount_tax':0,'amount_total':100,'amount_paid':100,'amount_return':0,
        'lines':[(0,0,{'product_id':product.id,'qty':1,'price_unit':100,'price_subtotal':100,'price_subtotal_incl':100,'tax_ids':[(5,0,0)]})]})
    data={'merchid':merchant.mid,'orderid':'LABTERMINALORDER','retref':'LAB-POS-RECEIPT','respstat':'A','respcode':'000','amount':'103.00','fee_amount':'3.00','fee_type':'SURCHRG'}
    with patch.object(CardPointeTerminalClient,'_request',return_value={'ok':True,'http_status':200,'data':data}):
        result=CardPointeTerminalClient(terminal).auth_card_with_session(100,'LABTERMINALORDER','fixture-only')
    assert result['status']=='approved',result
    env['pos.payment'].create({'pos_order_id':order.id,'payment_method_id':pm.id,'amount':100,'payment_status':'done','cardpointe_retref':data['retref'],'cardpointe_terminal_order_id':data['orderid'],'cardpointe_program_receipt':result['program_receipt'],'cardpointe_ok':True,'cardpointe_status':'approved'})
# Native POS synchronization keys orders/lines/payments by UUID. ORM fixtures
# must supply those identities too; otherwise the browser creates another draft.
order.write({'floating_order_name':'Synthetic approved receipt', 'uuid':order.uuid or str(uuid4())})
for line in order.lines:
    line.write({'uuid':line.uuid or str(uuid4()), 'full_product_name':product.display_name})
for payment in order.payment_ids:
    payment.write({'uuid':payment.uuid or str(uuid4())})
# A persisted ambiguous line with no reference must never start another charge.
held=env['pos.order'].search([('pos_reference','=','SYNTHETIC-PROGRAM-HELD')],limit=1)
if not held:
    held=env['pos.order'].create({'session_id':config.current_session_id.id, 'uuid':str(uuid4()),
        'pos_reference':'SYNTHETIC-PROGRAM-HELD', 'floating_order_name':'Synthetic held payment',
        'amount_tax':0,'amount_total':100,'amount_paid':100,'amount_return':0,
        'lines':[(0,0,{'uuid':str(uuid4()),'product_id':product.id,'full_product_name':product.display_name,
                      'qty':1,'price_unit':100,'price_subtotal':100,'price_subtotal_incl':100,'tax_ids':[(5,0,0)]})]})
    env['pos.payment'].create({'pos_order_id':held.id,'uuid':str(uuid4()),'payment_method_id':pm.id,
        'amount':100,'payment_status':'retry','cardpointe_status':'unknown','cardpointe_ok':False})
p=env.ref('payment_cardpointe.payment_provider_cardpointe')
p.write({'state':'test','is_published':True,'cardpointe_merchant_config_id':merchant.id,'payment_method_ids':[(6,0,env.ref('payment.payment_method_card').ids)]})
payer=env['res.partner'].search([('name','=','Synthetic browser payer')],limit=1) or env['res.partner'].create({'name':'Synthetic browser payer','country_id':env.ref('base.us').id})
tx=env['payment.transaction'].search([('reference','=','SYNTHETIC-PROGRAM-RECEIPT')],limit=1)
with patch('odoo.addons.payment.utils.request',new=SimpleNamespace(env=env)):
    if not tx:
        tx=env['payment.transaction'].create({'provider_id':p.id,'payment_method_id':env.ref('payment.payment_method_card').id,'reference':'SYNTHETIC-PROGRAM-RECEIPT','amount':100,'currency_id':env.ref('base.USD').id,'partner_id':payer.id,'operation':'online_direct'})
        with patch.object(type(p),'_cp_online_request',return_value={'ok':True,'data':{'token':'CREDIT','postal':'NOT_RESTRICTED','fee_amount':'3.00'}}):
            q=tx._cp_process('SYNTHETICNOTROUTABLE',postal='19000')
        with patch.object(type(p),'_cp_online_request',side_effect=lambda *a,**kw:{'ok':True,'data':{'merchid':merchant.mid,'orderid':tx.cp_order_id,'retref':'LAB-ONLINE-RECEIPT','respstat':'A','amount':'103.00','fee_amount':'3.00','fee_type':'SURCHRG'}}):
            tx._cp_process('SYNTHETICNOTROUTABLE',postal='19000',quote_id=q['quote_id'])
    access=utils.generate_access_token(tx.reference,tx.partner_id.id,tx.amount,tx.currency_id.id,p.id)
    action=env['ir.actions.act_url'].search([('name','=','Synthetic program receipt')],limit=1)
    values={'name':'Synthetic program receipt','url':'/payment/cardpointe/receipt?'+urlencode({'reference':tx.reference,'access_token':access}),'target':'self'}
    if action:
        action.write(values)
    else:
        action=env['ir.actions.act_url'].create(values)
    print('RECEIPT_ACTION_ID='+str(action.id))
env.flush_all(); env.cr.commit(); env.invalidate_all()
assert tx.cp_receipt['total']=='103.00'
assert order.payment_ids.cardpointe_program_receipt['fee']=='3.00'
print('POS_CONFIG_ID='+str(config.id))
print('FIXTURE_READBACK=PASS')
