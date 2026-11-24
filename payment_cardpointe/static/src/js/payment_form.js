/** @odoo-module **/
// One-time hosted checkout; no bank fields or persisted gateway tokens.
import { PaymentForm } from '@payment/interactions/payment_form';
import { patch } from '@web/core/utils/patch';
import { rpc } from '@web/core/network/rpc';
import { _t } from '@web/core/l10n/translation';

export function trustedToken(event, iframe) {
    if (!iframe || event.source !== iframe.contentWindow || event.origin !== new URL(iframe.src).origin) return null;
    try {
        const data = typeof event.data === 'string' ? JSON.parse(event.data) : event.data;
        if (!data || (data.errorCode && String(data.errorCode) !== '0')) return null;
        const value = data.token || data.message;
        return typeof value === 'string' && /^[A-Za-z0-9]{10,64}$/.test(value) ? value : null;
    } catch { return null; }
}

patch(PaymentForm.prototype, {
    async _prepareInlineForm(providerId, providerCode, paymentOptionId, paymentMethodCode, flow) {
        if (providerCode !== 'cardpointe') return super._prepareInlineForm(...arguments);
        this._setPaymentFlow('direct');
        if (!this._cpListener) {
            this._cpTokens = new Map();
            this._cpListener = event => {
                for (const iframe of this.el.querySelectorAll('.o_cp_iframe')) {
                    if (event.source === iframe.contentWindow && event.origin === new URL(iframe.src).origin) {
                        const token = trustedToken(event, iframe);
                        if (token) this._cpTokens.set(iframe, token);
                        else this._cpTokens.delete(iframe);
                    }
                }
            };
            window.addEventListener('message', this._cpListener);
        }
    },
    destroy() {
        if (this._cpListener) window.removeEventListener('message', this._cpListener);
        this._cpTokens?.clear();
        return super.destroy(...arguments);
    },
    async _initiatePaymentFlow(providerCode, paymentOptionId, paymentMethodCode, flow) {
        if (providerCode !== 'cardpointe') return super._initiatePaymentFlow(...arguments);
        const radio = this.el.querySelector('input[name="o_payment_radio"]:checked');
        const form = this._getInlineForm(radio).querySelector('.o_cp_form');
        const iframe = form.querySelector('iframe');
        const ach = form.querySelector('.o_cp_ach');
        const program = form.querySelector('.o_cp_program');
        if (program?.dataset.quoteId && !program.querySelector('.o_cp_quote_confirm').checked) {
            this._displayErrorDialog(_t('Quote confirmation required'), _t('Accept the displayed total before authorizing.'));
            this._enableButton();
            return;
        }
        // Persist only the Odoo processing identity, NEVER the hosted token. A lost
        // response/reload therefore inquires the same gateway order, not a new debit.
        const key = 'cp-checkout:' + JSON.stringify([this.paymentContext.transactionRoute, this.paymentContext.partnerId, this.paymentContext.amount, this.paymentContext.currencyId, paymentOptionId, paymentMethodCode]);
        let values;
        try {
            const saved = sessionStorage.getItem(key);
            if (saved) {
                values = JSON.parse(saved);
            } else {
                if (ach && (!ach.querySelector('.o_cp_consent').checked || !ach.querySelector('.o_cp_account_class:checked'))) {
                    this._displayErrorDialog(_t('Authorization required'), _t('Select an enabled account class and authorize the displayed one-time amount.'));
                    this._enableButton();
                    return;
                }
                const token = this._cpTokens?.get(iframe);
                if (!token) {
                    this._displayErrorDialog(_t('Secure form incomplete'), _t('Complete the secure Fiserv form and wait for tokenization before paying.'));
                    this._enableButton();
                    return;
                }
                values = await rpc(this.paymentContext.transactionRoute, this._prepareTransactionRouteParams());
                sessionStorage.setItem(key, JSON.stringify({reference:values.reference, access_token:values.access_token}));
            }
            const result = await rpc('/payment/cardpointe/process', {
                reference: values.reference, access_token: values.access_token,
                payment_method_code: paymentMethodCode,
                token: this._cpTokens?.get(iframe) || null,
                consent: ach ? ach.querySelector('.o_cp_consent').checked : null,
                account_class: ach?.querySelector('.o_cp_account_class:checked')?.value || null,
                consent_amount: ach?.dataset.consentAmount || null,
                postal: program?.querySelector('.o_cp_postal').value || null,
                quote_id: program?.dataset.quoteId || null,
            });
            if (result.state === 'quote' || result.state === 'quote_error') {
                if (program) {
                    program.dataset.quoteId = result.quote_id || '';
                    program.querySelector('.o_cp_quote_confirm').checked = false;
                    program.querySelector('.o_cp_quote_text').textContent = result.quote
                        ? `Subtotal ${result.quote.subtotal}; gateway fee ${result.quote.fee}; total ${result.quote.total} ${result.quote.currency}. Quote valid for five minutes.`
                        : result.message;
                }
                this._enableButton();
                return;
            }
            this._cpTokens?.clear();
            if (['done', 'authorized', 'error', 'cancel'].includes(result.state)) sessionStorage.removeItem(key);
            window.location = result.redirect_url || '/payment/status';
        } catch {
            this._cpTokens?.clear();
            this._displayErrorDialog(_t('Payment not confirmed'), _t('Do not start another payment. Retry here to inquire using the original reference, or contact the merchant.'));
            this._enableButton();
        }
    },
});
