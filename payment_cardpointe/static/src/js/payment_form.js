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
        if (providerCode !== 'cardpointe' || flow === 'token') return super._prepareInlineForm(...arguments);
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
    _cpFormError(form, retry) { return null; },
    _cpFormValues(form) { return {}; },
    _cpHandleResult(form, result) { return false; },
    _cpSelectedForm() {
        const radio = this.el.querySelector('input[name="o_payment_radio"]:checked');
        return this._getInlineForm(radio).querySelector('.o_cp_form');
    },
    async _initiatePaymentFlow(providerCode, paymentOptionId, paymentMethodCode, flow) {
        if (providerCode !== 'cardpointe' || flow !== 'direct') {
            return super._initiatePaymentFlow(...arguments);
        }
        const form = this._cpSelectedForm();
        const attempt = this._cpAttempts?.get(form);
        const error = this._cpFormError(form, Boolean(attempt))
            || (!attempt && !this._cpTokens?.get(form.querySelector('iframe'))
                && _t('Complete the secure Fiserv form before paying.'));
        if (error) {
            this._displayErrorDialog(_t('Check payment details'), error);
            this._enableButton();
            return;
        }
        if (attempt) {
            return this._processDirectFlow(providerCode, paymentOptionId, paymentMethodCode, attempt);
        }
        return super._initiatePaymentFlow(...arguments);
    },
    async _processDirectFlow(providerCode, paymentOptionId, paymentMethodCode, values) {
        if (providerCode !== 'cardpointe') return super._processDirectFlow(...arguments);
        const form = this._cpSelectedForm();
        // A quote or lost reply can be retried on this form using the same Odoo transaction.
        this._cpAttempts ??= new WeakMap();
        this._cpAttempts.set(form, {reference: values.reference, access_token: values.access_token});
        try {
            const result = await rpc('/payment/cardpointe/process', {
                reference: values.reference, access_token: values.access_token,
                token: this._cpTokens?.get(form.querySelector('iframe')) || null,
                ...this._cpFormValues(form),
            });
            if (this._cpHandleResult(form, result)) {
                this._enableButton();
                return;
            }
            this._cpTokens?.clear();
            if (['done', 'authorized', 'error', 'cancel'].includes(result.state)) {
                this._cpAttempts.delete(form);
            }
            window.location = result.redirect_url || '/payment/status';
        } catch (error) {
            this._cpTokens?.clear();
            const reason = error?.data?.name === 'odoo.exceptions.ValidationError' && error.data.message;
            this._displayErrorDialog(_t('Payment not confirmed'),
                reason || _t('Retry here to check the payment using its original reference.'));
            this._enableButton();
        }
    },
});
