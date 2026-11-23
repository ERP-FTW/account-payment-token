/** @odoo-module **/
import paymentForm from '@payment/js/payment_form';
import '@payment_cardpointe/js/payment_form';
import { rpc } from '@web/core/network/rpc';
import { getFixture, patchWithCleanup } from '@web/../tests/helpers/utils';

QUnit.module('CardPointeProgram');
for (const fee of ['3.00', '0.00']) {
    QUnit.test(`disclose ${fee}, confirm, hold and repeat original identity`, async assert => {
        const el=getFixture();
        el.innerHTML=`<input name="o_payment_radio" checked="checked" type="radio"/><div class="o_cp_form"><iframe></iframe><div class="o_cp_program"><input class="o_cp_postal" value="19000"/><p class="o_cp_quote_text"></p><input class="o_cp_quote_confirm" type="checkbox"/></div></div>`;
        const frame=el.querySelector('iframe');
        const calls=[];
        const ctx={el, paymentContext:{transactionRoute:'/synthetic/transaction',partnerId:1,amount:100,currencyId:1},
            _getInlineForm:()=>el, _prepareTransactionRouteParams:()=>({}),
            _cpTokens:new Map([[frame,'SYNTHETICNOTROUTABLE']]),
            _enableButton:()=>{}, _displayErrorDialog:()=>{}};
        sessionStorage.clear();
        patchWithCleanup(rpc,{async _rpc(route,params) {
            calls.push({route,params});
            if (route==='/synthetic/transaction') return {reference:'SYNTHETIC-REFERENCE',access_token:'synthetic-access'};
            if (!params.quote_id) return {state:'quote',quote_id:'synthetic-quote',quote:{subtotal:'100.00',fee,total:fee==='3.00'?'103.00':'100.00',currency:'USD'}};
            throw new Error('Synthetic lost authorization response');
        }});
        const submit=()=>paymentForm.prototype._initiatePaymentFlow.call(ctx,'cardpointe',1,'card','direct');
        await submit();
        assert.ok(el.querySelector('.o_cp_quote_text').textContent.includes(`gateway fee ${fee}`));
        assert.strictEqual(calls.length,2,'initial call only quotes, no confirmed authorization');
        await submit();
        assert.strictEqual(calls.length,2,'unchecked confirmation cannot authorize');
        el.querySelector('.o_cp_quote_confirm').checked=true;
        await submit();
        assert.strictEqual(calls[2].params.quote_id,'synthetic-quote');
        assert.strictEqual(ctx._cpTokens.size,0,'raw token discarded after ambiguous auth');
        await submit();
        assert.strictEqual(calls[3].params.reference,'SYNTHETIC-REFERENCE','reuses original transaction for server inquiry');
        assert.strictEqual(calls[3].params.token,null,'does not retain payment token');
        assert.notOk(JSON.stringify(sessionStorage).includes('SYNTHETICNOTROUTABLE'));
        sessionStorage.clear();
    });
}
