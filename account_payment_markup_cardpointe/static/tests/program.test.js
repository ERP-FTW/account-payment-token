/** @odoo-module **/
import { PaymentForm } from '@payment/interactions/payment_form';
import { expect, getFixture, test } from '@odoo/hoot';
import '@payment_cardpointe/js/payment_form';
import '@account_payment_markup_cardpointe/js/program';
import { rpc } from '@web/core/network/rpc';
import { patchWithCleanup } from '@web/../tests/web_test_helpers';


for (const fee of ['3.00', '0.00']) {
    test(`disclose ${fee}, confirm, hold and repeat original identity`, async () => {
        const el=getFixture();
        el.innerHTML=`<input name="o_payment_radio" checked="checked" type="radio"/><div class="o_cp_form"><iframe></iframe><div class="o_cp_program"><input class="o_cp_postal" value="19000"/><p class="o_cp_quote_text"></p><input class="o_cp_quote_confirm" type="checkbox"/></div></div>`;
        const frame=el.querySelector('iframe');
        const calls=[];
        const ctx={__proto__: PaymentForm.prototype, el, paymentContext:{transactionRoute:'/synthetic/transaction',partnerId:1,amount:100,currencyId:1},
            waitFor: promise => promise, _getInlineForm:()=>el, _prepareTransactionRouteParams:()=>({}),
            _cpTokens:new Map([[frame,'SYNTHETICNOTROUTABLE']]),
            _enableButton:()=>{}, _displayErrorDialog:()=>{}};
        sessionStorage.clear();
        patchWithCleanup(rpc,{async _rpc(route,params) {
            calls.push({route,params});
            if (route==='/synthetic/transaction') return {reference:'SYNTHETIC-REFERENCE',access_token:'synthetic-access'};
            if (!params.quote_id) return {state:'quote',quote_id:'synthetic-quote',quote:{subtotal:'100.00',fee,total:fee==='3.00'?'103.00':'100.00',currency:'USD'}};
            throw new Error('Synthetic lost authorization response');
        }});
        const submit=()=>PaymentForm.prototype._initiatePaymentFlow.call(ctx,'cardpointe',1,'card','direct');
        await submit();
        expect(el.querySelector('.o_cp_quote_text').textContent).toInclude(`gateway fee ${fee}`);
        expect(calls).toHaveLength(2);
        await submit();
        expect(calls).toHaveLength(2);
        el.querySelector('.o_cp_quote_confirm').checked=true;
        await submit();
        expect(calls[2].params.quote_id).toBe('synthetic-quote');
        expect(ctx._cpTokens.size).toBe(0);
        await submit();
        expect(calls[3].params.reference).toBe('SYNTHETIC-REFERENCE');
        expect(calls[3].params.token).toBe(null);
        expect(JSON.stringify(sessionStorage)).not.toInclude('SYNTHETICNOTROUTABLE');
        sessionStorage.clear();
    });
}
