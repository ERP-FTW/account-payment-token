// Execute the production flow with an in-memory RPC/DOM boundary; never emit tokens.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
const source = fs.readFileSync(new URL('../../payment_cardpointe/static/src/js/payment_form.js', import.meta.url), 'utf8');
for (const [first, second] of [['card', 'cardpointe_ach'], ['cardpointe_ach', 'card']]) {
    const storage = new Map(), calls = [], iframe = {};
    let methods, selected = first, consent = true, held = true, sequence = 0;
    const ach = {dataset: {consentAmount: '10.00'}, querySelector: s => s === '.o_cp_consent' ? {checked: consent} : {value: 'consumer_checking'}};
    const form = {querySelector: s => s === 'iframe' ? iframe : selected === 'cardpointe_ach' ? ach : null};
    const rpc = async (route, params) => {
        calls.push({route, method: params.payment_method_code, reference: params.reference});
        if (route === '/payment/transaction') return {reference: `DRAFT-${++sequence}`, access_token: 'synthetic-access'};
        if (held) throw Error('Previous payment unresolved');
        return {state: 'pending'};
    };
    const context = {paymentForm: {include: value => {methods = value;}}, rpc, _t: s => s,
        sessionStorage: {getItem: k => storage.get(k), setItem: (k,v) => storage.set(k,v), removeItem: k => storage.delete(k)}, window: {}, URL};
    vm.runInNewContext(source.replace(/^import .*;$/gm, '').replace('export function', 'function'), context);
    const widget = {paymentContext: {transactionRoute: '/payment/transaction', partnerId: 1, amount: 10, currencyId: 1},
        el: {querySelector: () => ({})}, _getInlineForm: () => ({querySelector: () => form}),
        _prepareTransactionRouteParams: () => ({}), _displayErrorDialog: () => {}, _enableButton: () => {}};
    const pay = async () => {
        widget._cpTokens = new Map([[iframe, crypto.randomBytes(16).toString('hex')]]);
        await methods._initiatePaymentFlow.call(widget, 'cardpointe', selected === 'card' ? 1 : 2, selected, 'direct');
    };
    await pay();
    selected = second; held = false;
    if (second === 'cardpointe_ach') {
        consent = false; const before = calls.length; await pay(); assert.equal(calls.length, before);
        consent = true;
    }
    await pay();
    assert.equal(sequence, 2, 'Method switch must create a distinct draft');
    assert.equal(calls.at(-1).method, second);
    assert.equal(calls.at(-1).reference, 'DRAFT-2');
    await pay();
    assert.equal(sequence, 2, 'Pending retry must keep its original identity');
    assert.equal(calls.at(-1).reference, 'DRAFT-2');
    selected = first; await pay();
    assert.equal(sequence, 2, 'Switching back must preserve the original attempt');
    assert.equal(calls.at(-1).reference, 'DRAFT-1');
    assert.equal(calls.at(-1).method, first);
}
console.log(JSON.stringify({status: 'PASS', cases: ['card_to_ach', 'ach_to_card', 'consent_required_on_switch', 'pending_identity_preserved', 'switch_back_identity_preserved'], gateway_tested: false}));
