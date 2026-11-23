// Direct execution of unchanged production consumers on an actual route result.
// Usage: node consumer_regression.mjs <addon-root> <odoo18-root> <route.json>
// Framework storage/services are minimal test doubles, NOT a browser/ORM claim.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const [addons, odoo, input] = process.argv.slice(2);
const context = vm.createContext({console, luxon: {},
    _t: (x) => x, registry: {category: () => ({add() {}})},
    register_payment_method() {}, PaymentInterface: class {}, PaymentScreen: class {},
    Base: class {update(values) {Object.assign(this, values);}},
});
function load(path, exports = '') {
    const source = fs.readFileSync(path, 'utf8').replace(/^import .*;\n/gm, '').replace(/^export /gm, '');
    vm.runInContext(source + '\n' + exports, context, {filename: path});
}
load(`${odoo}/addons/web/static/src/core/utils/numbers.js`);
load(`${odoo}/addons/web/static/src/core/utils/patch.js`);
load(`${odoo}/addons/point_of_sale/static/src/app/models/pos_payment.js`, 'globalThis.PosPayment = PosPayment;');
load(`${addons}/pos_cardpointe_poc/static/src/js/pos_cardpointe_poc.js`, 'globalThis.CardPointePOC = CardPointePOC;');
load(`${addons}/pos_cardpointe_poc_tipping/static/src/js/pos_cardpointe_poc_tipping.js`);
load(`${addons}/pos_cardpointe_poc/static/src/js/program_receipt.js`);
const response = JSON.parse(fs.readFileSync(input, 'utf8'));
assert.equal(response.status, 'approved');
assert.equal(response.cardpointe_tip_prompted, false);
function consume(result) {
    const line = new context.PosPayment();
    line.pos_order_id = {assert_editable() {}, currency: {decimal_places: 2}};
    line.payment_method_id = {name: 'Synthetic program card'};
    line.amount = 0; // output cannot pass by retaining a pre-seeded tender
    const consumer = new context.CardPointePOC();
    consumer.pos = {set_tip() {throw new Error('Disabled tips must not be applied');}};
    consumer._applyApprovedCardPointeResult(line, result);
    assert.equal(line.get_payment_status(), 'done');
    assert.equal(consumer._cardpointeNativeTipPromise, undefined);
    return line;
}
const broken = {...response}; delete broken.program_receipt;
const negative = consume(broken);
assert.equal(negative.get_amount(), 103); // mutation control reproduces excess tender
const line = consume(response);
assert.equal(line.get_amount(), 100);
assert.equal(line.cardpointe_program_receipt.fee, '3.00');
assert.equal(line.cardpointe_program_receipt.total, '103.00');
assert.equal(line.export_for_printing().cardpointe_program_receipt, line.cardpointe_program_receipt);
const payment = Object.fromEntries(Object.entries(line).filter(([key]) => !['pos_order_id', 'payment_method_id'].includes(key)));
const reloaded = consume(response);
Object.assign(reloaded, JSON.parse(JSON.stringify(payment)));
assert.equal(reloaded.get_amount(), 100);
assert.equal(reloaded.export_for_printing().cardpointe_program_receipt.total, '103.00');
const tender = line.get_amount();
const change = tender - 100; // sale fixture subtotal; actual core finalize follows in POS browser
assert.equal(change, 0);
console.log(JSON.stringify({verdict: 'PASS', tender, surcharge: Number(line.cardpointe_program_receipt.fee),
    charged: Number(line.cardpointe_program_receipt.total), change, payment,
    negative_control: {tender: negative.get_amount(), change: negative.get_amount() - 100},
    scope: 'Actual base+tipping consumers, native PosPayment setters and receipt exporter; minimal framework storage doubles. ORM reload and browser finalization are separate proofs.'}));
