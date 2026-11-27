// Supplementary synthetic JS regression: executes the actual checkout method.
// This does not replace Odoo asset/browser or real hosted-form evidence.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const source = readFileSync(new URL('../static/src/js/payment_form.js', import.meta.url), 'utf8')
    .replace(/^import .*;\n/gm, '').replace('export function trustedToken', 'function trustedToken');
class PaymentForm {}
const calls = [];
const stored = new Map();
vm.runInNewContext(source, {
    PaymentForm, patch: (prototype, methods) => Object.assign(prototype, methods),
    rpc: async (route, params) => {
        calls.push({ route, params });
        if (route === '/synthetic/transaction') return {reference: `TX${calls.length}`, access_token: 'synthetic-access'};
        return { state: 'pending' };
    },
    _t: text => text, URL, Map, window: {},
    sessionStorage: {getItem: key => stored.get(key), setItem: (key, value) => stored.set(key, value)},
});
const iframe = {};
const form = {querySelector: selector => selector === 'iframe' ? iframe : null};
const ctx = {
    __proto__: PaymentForm.prototype,
    el: {querySelector: () => ({})},
    paymentContext: {transactionRoute: '/synthetic/transaction', partnerId: 1, amount: 10, currencyId: 1, providerId: 7},
    _getInlineForm: () => ({querySelector: () => form}),
    _prepareTransactionRouteParams: () => ({provider_id: ctx.paymentContext.providerId}),
    _cpTokens: new Map([[iframe, 'SYNTHETICNOTROUTABLE']]),
    _enableButton() {}, _displayErrorDialog() {throw new Error('Unexpected payment failure');},
};
const submit = () => ctx._initiatePaymentFlow('cardpointe', 39, 'card', 'direct');
await submit();
assert.equal(calls.length, 2);
assert.equal(calls[1].params.provider_id, 7);
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
assert.equal(calls[4].params.provider_id, 8);
assert.notEqual(calls[4].params.reference, originalReference);
assert.equal(stored.size, 2);
assert.ok(!JSON.stringify([...stored.values()]).includes('SYNTHETICNOTROUTABLE'));
console.log('PASS: same-provider retry reuses identity; changed provider is isolated; provider is submitted; no hosted token persisted');
