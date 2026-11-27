// Exact exported production function, isolated from the Odoo loader for boundary negatives.
import fs from 'node:fs';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
const source=fs.readFileSync(new URL('../static/src/js/payment_form.js',import.meta.url),'utf8');
const fn=source.slice(source.indexOf('export function trustedToken'),source.indexOf('patch(PaymentForm.prototype')).replace('export ','');
const trustedToken=Function(fn+'; return trustedToken;')();
const win={}; const iframe={contentWindow:win,src:'https://fts-uat.cardconnect.com/itoke/ajax-tokenizer.html'};
const token=crypto.randomBytes(16).toString('hex');
const event={source:win,origin:'https://fts-uat.cardconnect.com',data:JSON.stringify({token,errorCode:'0'})};
assert.equal(trustedToken(event,iframe),token);
assert.equal(trustedToken({...event,source:{}},iframe),null);
assert.equal(trustedToken({...event,origin:'https://other.example.invalid'},iframe),null);
assert.equal(trustedToken({...event,data:'malformed'},iframe),null);
assert.equal(trustedToken({...event,data:{token,errorCode:'1'}},iframe),null);
assert.equal(trustedToken({...event,data:{token:crypto.randomBytes(2).toString('hex')}},iframe),null);
console.log(JSON.stringify({status:'PASS',checks:6,scope:'iframe source/origin/error/shape boundaries; opaque values not emitted'}));
