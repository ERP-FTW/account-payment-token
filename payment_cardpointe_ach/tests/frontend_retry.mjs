// Execute the production flow with an in-memory RPC/DOM boundary; never emit tokens.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
const source = fs.readFileSync(new URL('../../payment_cardpointe/static/src/js/payment_form.js', import.meta.url), 'utf8');
for (const [first, second] of [['card', 'cardpointe_ach'], ['cardpointe_ach', 'card']]) {
    const calls = [], dialogs = [], iframe = {};
    let selected = first, consent = true, held = true, sequence = 0;
    const ach = {dataset: {consentAmount: '10.00'}, querySelector: s => s === '.o_cp_consent' ? {checked: consent} : {value: 'consumer_checking'}};
    const forms = Object.fromEntries([first, second].map(method => [method, {
        querySelector: s => s === 'iframe' ? iframe : method === 'cardpointe_ach' ? ach : null,
    }]));
    const rpc = async (route, params) => {
        calls.push({route, reference: params.reference});
        if (route === '/payment/transaction') return {reference: `DRAFT-${++sequence}`, access_token: 'synthetic-access'};
        if (held) {
            if (first === 'card') throw new Error('Connection lost');
            throw Object.assign(new Error(), {data: {
                name: 'odoo.exceptions.ValidationError', message: 'Previous payment unresolved',
            }});
        }
        return {state: 'pending'};
    };
    class PaymentForm {
        async _initiatePaymentFlow(provider, option, method) {
            const values = await rpc(this.paymentContext.transactionRoute, this._prepareTransactionRouteParams());
            return this._processDirectFlow(provider, option, method, values);
        }
    }
    const context = {PaymentForm, rpc, _t: s => s, window: {}, URL,
        patch: (target, extension) => {
            Object.setPrototypeOf(extension, {_initiatePaymentFlow: target._initiatePaymentFlow, ...target});
            Object.assign(target, extension);
        }};
    const achSource = fs.readFileSync(new URL('../static/src/js/ach.js', import.meta.url), 'utf8');
    for (const script of [source, achSource]) {
        vm.runInNewContext(script.replace(/^import .*;$/gm, '').replace('export function', 'function'), context);
    }
    const widget = {__proto__: PaymentForm.prototype,
        paymentContext: {transactionRoute: '/payment/transaction', partnerId: 1, amount: 10, currencyId: 1},
        el: {querySelector: () => ({})}, _getInlineForm: () => ({querySelector: () => forms[selected]}),
        _prepareTransactionRouteParams: () => ({}), _displayErrorDialog: (title, message) => dialogs.push(message), _enableButton: () => {}};
    const pay = async () => {
        widget._cpTokens = new Map([[iframe, crypto.randomBytes(16).toString('hex')]]);
        await widget._initiatePaymentFlow( 'cardpointe', selected === 'card' ? 1 : 2, selected, 'direct');
    };
    await pay();
    if (first === 'card') assert.match(dialogs.at(-1), /original reference/);
    else assert.equal(dialogs.at(-1), 'Previous payment unresolved');
    selected = second; held = false;
    if (second === 'cardpointe_ach') {
        consent = false; const before = calls.length; await pay(); assert.equal(calls.length, before);
        consent = true;
    }
    await pay();
    assert.equal(sequence, 2, 'Method switch must create a distinct draft');
    assert.equal(calls.at(-1).reference, 'DRAFT-2');
    await pay();
    assert.equal(sequence, 2, 'Pending retry must keep its original identity');
    assert.equal(calls.at(-1).reference, 'DRAFT-2');
    selected = first; await pay();
    assert.equal(sequence, 2, 'Switching back must preserve the original attempt');
    assert.equal(calls.at(-1).reference, 'DRAFT-1');
}
console.log(JSON.stringify({status: 'PASS', cases: ['card_to_ach', 'ach_to_card', 'consent_required_on_switch', 'pending_identity_preserved', 'switch_back_identity_preserved'], gateway_tested: false}));
