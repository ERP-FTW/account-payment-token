const fs = require('fs'), vm = require('vm'), assert = require('assert');
const path = require('path');
const sourceRoot = process.env.ODOO_SOURCE;
if (!sourceRoot) throw new Error('Set ODOO_SOURCE to an exact Odoo 16 checkout');
const src = fs.readFileSync(path.join(sourceRoot, 'addons/point_of_sale/static/src/js/models.js'), 'utf8');
const taxStart = src.indexOf('    _compute_all(tax,');
const taxEnd = src.indexOf('\n    /**\n     * Taxes after fiscal position mapping.', taxStart);
const a = src.indexOf('class Payment extends PosModel {'), b = src.indexOf('Registries.Model.add(Payment)', a);
class Orderline { static create(_, o) { return {product:o.product, can_be_merged_with:()=>false, export_as_JSON(){return {markup_generated:this.markup_generated};}}; } }
const ctx = {Orderline, PosModel: class {}, _t:s=>s, _:(xs)=>({each:f=>xs.forEach(f)}),
  round_pr:(v,r)=>Math.round(v/r)*r, round_di:(v,d)=>Math.round(v*10**d)/10**d,
  time:{datetime_to_str:()=>''}};
vm.createContext(ctx); vm.runInContext(src.slice(a,b)+'\nglobalThis.Payment=Payment;', ctx);
vm.runInContext('globalThis.NativeTax=class {' + src.slice(taxStart,taxEnd) + '};', ctx);
const start = src.indexOf('    add_product(product, options){'), end = src.indexOf('    set_orderline_options(', start);
vm.runInContext('globalThis.NativeOrder=class {' + src.slice(start,end) + '};', ctx);
class Order extends ctx.NativeOrder {
  get_paymentlines(){return this.payments;} get_orderlines(){return this.lines;}
  remove_paymentline(l){this.payments=this.payments.filter(x=>x!==l);}
  remove_orderline(l){this.lines=this.lines.filter(x=>x!==l);} assert_editable(){}
  fix_tax_included_price(){} set_orderline_options(l,o){Object.assign(l,o);}
  add_orderline(l){this.lines.push(l);} select_orderline(l){this.selected_orderline=l;}
  get_last_orderline(){return this.lines[this.lines.length-1];}
}
let Payment, ExtendedOrder;
const req = n => ({
  'point_of_sale.models': {Payment:ctx.Payment, Order, Orderline},
  'point_of_sale.Registries': {Model:{extend:(base,mix)=>{if(base===Order) ExtendedOrder=mix(base); else if(base===ctx.Payment) Payment=mix(base); else mix(base);}}},
  'web.utils': {round_precision:(v,r)=>Math.round(v/r)*r},
}[n]);
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/src/js/payment_markup.js'),'utf8'), {odoo:{define:(n,f)=>f(req)}});
const product = {id:99, taxes_id:[]};
const method = {id:1, markup_enabled:true, markup_percent:3, markup_product_id:[99,'fee']};
const order = new ExtendedOrder(); order.lines=[]; order.payments=[]; order.orderlines={get length(){return order.lines.length;}, at:i=>order.lines[i]};
const nativeTax = new ctx.NativeTax();
nativeTax.company = {tax_calculation_rounding_method:'round_per_line'};
nativeTax.currency = {rounding:.01};
const pos = {doNotAllowRefundAndSales:()=>false, currency:{rounding:.01,decimal_places:2}, payment_methods_by_id:{1:method}, get_order:()=>order,
  get_taxes_after_fp:(ids)=>ids.map(id=>({id})),
  compute_all:nativeTax.compute_all.bind(nativeTax),
  db:{get_product_by_id:id=>id===99?product:{id}}}; order.pos=pos;
const make = () => new Payment({}, {pos,order,payment_method:method});
const evidence = {provenance:'cardpointe-server', verified:true, server_verified:true, server_trusted_markup_evidence:true, card_type:'credit', surcharge_present:false};
let p=make(); order.payments=[p]; p.set_amount(400); p.set_markup_evidence(evidence);
assert.equal(p.amount,412); assert.equal(order.lines.length,1); assert.equal(order.lines[0].markup_generated,true); assert.equal(order.lines[0].price_manually_set,true);
p.set_markup_evidence(evidence); assert.equal(p.amount,412); assert.equal(order.lines.length,1); assert.equal(order.lines[0].price,12);
p.payment_method={id:2,markup_enabled:false}; p.set_markup_evidence(null); order._sync_markup_orderline();
assert.equal(p.amount,400); assert.equal(order.lines.length,0);
order.lines=[]; p=make(); order.payments=[p]; p.set_amount(400); p.set_markup_evidence(evidence); p.set_markup_authorized(412,evidence); p.set_amount(999); assert.equal(p.amount,412);
const json=p.export_as_JSON(); const p2=new Payment({}, {pos,order,json}); p2.set_amount(999); assert.equal(p2.amount,412);

// Manual terminal mode freezes the already disclosed provider-configured
// total without requiring surcharge-program evidence. A changed approval
// amount cannot retain the local fee decision.
order.lines=[]; p=make(); order.payments=[p]; p.set_amount(400); p.set_markup_authorized(412,null);
assert.equal(p.amount,412); assert.equal(p.markup_amount_source,'local'); assert.equal(order.lines.length,1);
const manualJson=p.export_as_JSON(); const manualReload=new Payment({}, {pos,order,json:manualJson});
manualReload.set_amount(999); assert.equal(manualReload.amount,412);
order.lines=[]; const mismatched=make(); order.payments=[mismatched]; mismatched.set_amount(400);
mismatched.set_markup_authorized(411,null); assert.equal(mismatched.amount,411);
assert.equal(mismatched.markup_amount_source,'none'); assert.equal(order.lines.length,0);

// Exercise the actual _sync_markup_orderline -> add_product path for the
// four product/rate tax combinations.  A fee line's unit price must be net
// for excluded-tax products and gross for fully included-tax products.
for (const productIncluded of [false, true]) {
  for (const rateIncluded of [false, true]) {
    product.taxes_id = [1];
    pos.get_taxes_after_fp = () => [{id:1, name:'10%', sequence:1, amount_type:'percent', amount:10,
      price_include:productIncluded, include_base_amount:false, is_base_affected:true}];
    const net = rateIncluded ? 2.73 : 3.00;
    const gross = rateIncluded ? 3.00 : 3.30;
    const taxPayment = make();
    taxPayment.markup_base_amount = 100;
    taxPayment.markup_amount = net;
    taxPayment.markup_total_amount = 100 + gross;
    taxPayment.markup_amount_source = 'local';
    order.lines = []; order.payments = [taxPayment];
    order._sync_markup_orderline();
    assert.equal(order.lines.length, 1);
    assert.ok(Math.abs(order.lines[0].price - (productIncluded ? gross : net)) < 0.001,
      `fee unit price (${productIncluded}, ${rateIncluded})`);
    assert.equal(order.lines[0].markup_generated, true);
    // The serialized generated line remains present for backend order import.
    assert.equal(order.lines[0].export_as_JSON().markup_generated, true);
  }
}

// Mixed included/excluded taxes require inversion of the native tax total,
// not simply the net or gross fee.  This mirrors Odoo's effective-tax line
// calculation for a 10% included + 5% excluded product tax set.
product.taxes_id = [1, 2];
pos.get_taxes_after_fp = () => [
  {id:1, name:'10% included', sequence:1, amount_type:'percent', amount:10,
    price_include:true, include_base_amount:false, is_base_affected:true},
  {id:2, name:'5% excluded', sequence:2, amount_type:'percent', amount:5,
    price_include:false, include_base_amount:false, is_base_affected:true},
];
for (const [rateIncluded, netFee, grossFee] of [[false, 3.00, 3.45], [true, 2.61, 3.00]]) {
  const mixedPayment = make();
  mixedPayment.markup_base_amount = 100;
  mixedPayment.markup_amount = netFee;
  mixedPayment.markup_total_amount = 100 + grossFee;
  mixedPayment.payment_method.markup_tax_included = rateIncluded;
  order.lines = []; order.payments = [mixedPayment];
  order._sync_markup_orderline();
  assert.equal(order.lines.length, 1);
  const mixedResult = pos.compute_all(pos.get_taxes_after_fp(), order.lines[0].price, 1, pos.currency.rounding);
  assert.ok(Math.abs(mixedResult.total_included - grossFee) < 0.001,
    `mixed-tax line gross (${rateIncluded}): ${mixedResult.total_included}`);
  assert.equal(order.lines[0].markup_generated, true);
}

// Split tenders must stay as separate native lines.  Aggregating same-product
// fees first lets Odoo round tax once on the combined amount, which can create
// an extra cent compared with the independently disclosed tender fees.
product.taxes_id = [1];
pos.get_taxes_after_fp = () => [{id:1, name:'10% excluded', sequence:1, amount_type:'percent', amount:10,
  price_include:false, include_base_amount:false, is_base_affected:true}];
order.lines = [];
const splitOne = make();
const splitTwo = make();
order.payments = [splitOne, splitTwo];
splitOne.set_amount(1);
splitTwo.set_amount(1);
assert.equal(splitOne.amount, 1.03);
assert.equal(splitTwo.amount, 1.03);
assert.equal(order.lines.length, 2);
assert.deepEqual(order.lines.map(line => line.price), [0.03, 0.03]);
const splitGross = order.lines.reduce((total, line) =>
  total + pos.compute_all(pos.get_taxes_after_fp(), line.price, 1, pos.currency.rounding).total_included, 0);
assert.equal(splitGross, 0.06);
// A replay/sync must preserve the same line count and cent-level total.
order._sync_markup_orderline();
assert.equal(order.lines.length, 2);
assert.equal(order.lines.reduce((total, line) =>
  total + pos.compute_all(pos.get_taxes_after_fp(), line.price, 1, pos.currency.rounding).total_included, 0), 0.06);

// Removing one split allocation must immediately remove only its generated
// fee. Removing the final eligible allocation must leave ordinary products
// untouched, and retry/repeated synchronization must create exactly one fee.
const ordinaryLine = {markup_generated:false, price:42, can_be_merged_with:()=>false};
order.lines.unshift(ordinaryLine);
order.remove_paymentline(splitOne);
assert.deepEqual(order.payments, [splitTwo]);
assert.equal(order.lines.length, 2);
assert.equal(order.lines[0], ordinaryLine);
assert.equal(order.lines.filter(line => line.markup_generated).length, 1);
assert.equal(order.lines[1].price, 0.03);
order.remove_paymentline(splitTwo);
assert.equal(order.payments.length, 0);
assert.deepEqual(order.lines, [ordinaryLine]);
product.taxes_id = [];
pos.get_taxes_after_fp = () => [];
const retryPayment = make();
order.payments = [retryPayment];
retryPayment.set_amount(100);
assert.equal(order.lines[0], ordinaryLine);
assert.equal(order.lines.filter(line => line.markup_generated).length, 1);
order._sync_markup_orderline();
assert.equal(order.lines[0], ordinaryLine);
assert.equal(order.lines.filter(line => line.markup_generated).length, 1);

// A cent-level included-rate fee must use the attainable native gross after
// rounding, rather than retaining the configured 0.05 gross.  This is the
// exact set_amount -> sync -> add_product -> compute_all path from review18.
product.taxes_id = [1];
pos.get_taxes_after_fp = () => [{id:1, name:'10% excluded', sequence:1, amount_type:'percent', amount:10,
  price_include:false, include_base_amount:false, is_base_affected:true}];
method.markup_tax_included = true;
order.lines = [];
const smallIncluded = make();
order.payments = [smallIncluded];
smallIncluded.set_amount(1.67);
assert.equal(smallIncluded.markup_amount, 0.04);
assert.equal(smallIncluded.markup_total_amount, 1.71);
assert.equal(order.lines.length, 1);
assert.equal(order.lines[0].price, 0.04);
assert.equal(pos.compute_all(pos.get_taxes_after_fp(), order.lines[0].price, 1,
  pos.currency.rounding).total_included, 0.04);

// An included product with an included rate must extract the configured gross
// once.  The payment, generated line, and native tax split must agree.
product.taxes_id = [1];
pos.get_taxes_after_fp = () => [{id:1, name:'10% included', sequence:1, amount_type:'percent', amount:10,
  price_include:true, include_base_amount:false, is_base_affected:true}];
method.markup_tax_included = true;
order.lines = [];
const includedRate = make();
order.payments = [includedRate];
includedRate.set_amount(100);
assert.equal(includedRate.markup_amount, 2.73);
assert.equal(includedRate.markup_total_amount, 103);
assert.equal(order.lines.length, 1);
assert.equal(order.lines[0].price, 3);
const includedNative = pos.compute_all(pos.get_taxes_after_fp(), order.lines[0].price, 1,
  pos.currency.rounding);
assert.equal(includedNative.total_excluded, 2.73);
assert.equal(includedNative.total_included, 3);
assert.equal(includedNative.total_included - includedNative.total_excluded, 0.27);

method.markup_tax_included = false;
order.lines = [];
const smallExcluded = make();
order.payments = [smallExcluded];
smallExcluded.set_amount(1.67);
assert.equal(smallExcluded.markup_amount, 0.05);
assert.equal(smallExcluded.markup_total_amount, 1.73);
assert.equal(pos.compute_all(pos.get_taxes_after_fp(), order.lines[0].price, 1,
  pos.currency.rounding).total_included, 0.06);

console.log('PASS exact Odoo16 Payment/add_product reproduction: repeat sync, cash switch, authorized JSON reload, four tax-aware fee lines, mixed-tax inversion, split-tender native rounding and serialized generated orderline');
