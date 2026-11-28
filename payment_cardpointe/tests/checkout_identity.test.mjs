// Supplementary synthetic JS regression: executes the actual checkout method.
// This does not replace Odoo asset/browser or real hosted-form evidence.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const source = readFileSync(new URL('../static/src/js/payment_form.js', import.meta.url), 'utf8')
    .replace(/^import .*;\n/gm, '').replace('export function trustedToken', 'function trustedToken');
class PaymentForm {
    async _initiatePaymentFlow(provider, option, method) {
        const values = await rpc(this.paymentContext.transactionRoute, this._prepareTransactionRouteParams());
        return this._processDirectFlow(provider, option, method, values);
    }
}
const calls = [];
const rpc = async (route, params) => {
        calls.push({ route, params });
        if (route === '/synthetic/transaction') return {reference: `TX${calls.length}`, access_token: 'synthetic-access'};
        return { state: 'pending' };
};
vm.runInNewContext(source, {
    PaymentForm, rpc, patch: (prototype, methods) => {
        Object.setPrototypeOf(methods, {_initiatePaymentFlow: prototype._initiatePaymentFlow});
        Object.assign(prototype, methods);
    },
    _t: text => text, URL, Map, window: {},
});
const iframe = {};
const forms = [7, 8].map(() => ({querySelector: selector => selector === 'iframe' ? iframe : null}));
const ctx = {
    __proto__: PaymentForm.prototype,
    el: {querySelector: () => ({})},
    paymentContext: {transactionRoute: '/synthetic/transaction', partnerId: 1, amount: 10, currencyId: 1, providerId: 7},
    _getInlineForm: () => ({querySelector: () => forms[ctx.paymentContext.providerId - 7]}),
    _prepareTransactionRouteParams: () => ({provider_id: ctx.paymentContext.providerId}),
    _cpTokens: new Map([[iframe, 'SYNTHETICNOTROUTABLE']]),
    _enableButton() {}, _displayErrorDialog() {throw new Error('Unexpected payment failure');},
};
const submit = () => ctx._initiatePaymentFlow('cardpointe', 39, 'card', 'direct');
await submit();
assert.equal(calls.length, 2);
const originalReference = calls[1].params.reference;
ctx._cpTokens.set(iframe, 'SYNTHETICNOTROUTABLE');
await submit();
assert.equal(calls.length, 3);
assert.equal(calls[2].params.reference, originalReference);
ctx.paymentContext.providerId = 8;
ctx._cpTokens.set(iframe, 'SYNTHETICNOTROUTABLE');
await submit();
assert.equal(calls.length, 5, 'changing provider must create its own transaction');
assert.equal(calls[3].route, '/synthetic/transaction');
assert.notEqual(calls[4].params.reference, originalReference);
assert.ok(forms.every(form => ctx._cpAttempts.has(form)));
assert.ok(!JSON.stringify(forms.map(form => ctx._cpAttempts.get(form))).includes('SYNTHETICNOTROUTABLE'));
// Opening a different checkout for the same amount always uses the normal Odoo transaction route.
const next = {__proto__: PaymentForm.prototype, ...ctx, _cpAttempts: undefined, _cpTokens: new Map([[iframe, 'SYNTHETICNOTROUTABLE']])};
await next._initiatePaymentFlow('cardpointe', 39, 'card', 'direct');
assert.notEqual(calls.at(-1).params.reference, calls[4].params.reference);
console.log('PASS: each open checkout retries its own transaction and new checkouts are independent');
