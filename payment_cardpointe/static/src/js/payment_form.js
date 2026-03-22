odoo.define('payment_cardpointe.payment_form', require => {
    'use strict';

    const core = require('web.core');

    const checkoutForm = require('payment.checkout_form');
    const manageForm = require('payment.manage_form');

    const _t = core._t;

    const cardpointeMixin = {
        _prepareInlineForm: function (code, paymentOptionId, flow) {
            if (code !== 'cardpointe') {
                return this._super(...arguments);
            }
            if (flow === 'token') {
                return Promise.resolve();
            }

            this._setPaymentFlow('direct');
            this._ensureCardpointeListener();
            return Promise.resolve();
        },

        _processPayment: function (code, paymentOptionId, flow) {
            if (code !== 'cardpointe' || flow === 'token') {
                return this._super(...arguments);
            }

            this._requestCardpointeTokenization(paymentOptionId);
            return this._waitForCardpointeToken(paymentOptionId).then(tokenPayload => {
                if (!tokenPayload || !tokenPayload.token) {
                    const wrapper = this._getCardpointeWrapperByProviderId(paymentOptionId);
                    const tokenizerUrl = wrapper ? (wrapper.dataset.tokenizerUrl || '') : '';
                    console.warn(
                        '[CARDPOINTE] Missing token from Hosted iFrame Tokenizer.',
                        {providerId: paymentOptionId, tokenizerUrl: tokenizerUrl}
                    );
                    this._enableButton();
                    $('body').unblock();
                    this._displayError(
                        _t("CardPointe"),
                        _t("We could not retrieve a payment token."),
                        _t("Please complete the secure card form and try again.")
                    );
                    return null;
                }

                const saveToken = this._getCardpointeSaveTokenValue(paymentOptionId);

                return this._rpc({
                    route: this.txContext.transactionRoute,
                    params: this._prepareTransactionRouteParams(
                        'cardpointe',
                        paymentOptionId,
                        'direct'
                    ),
                }).then(processingValues => {
                    return this._rpc({
                        route: '/payment/cardpointe/process',
                        params: {
                            'reference': processingValues.reference,
                            'partner_id': processingValues.partner_id,
                            'token': tokenPayload.token,
                            'meta': tokenPayload.meta || {},
                            'save_token': saveToken,
                            'access_token': processingValues.access_token || this.txContext.accessToken,
                        }
                    });
                }).then(result => {
                    if (!result || !result.success) {
                        this._enableButton();
                        $('body').unblock();
                        this._displayError(
                            _t("CardPointe"),
                            _t("We are not able to process your payment."),
                            (result && result.message) || _t("Please try again or use another payment method.")
                        );
                        return;
                    }
                    window.location = result.redirect_url || '/payment/status';
                });
            }).guardedCatch((error) => {
                error.event.preventDefault();
                this._displayError(
                    _t("Server Error"),
                    _t("We are not able to process your payment."),
                    error.message.data.message
                );
            });
        },

        _ensureCardpointeListener: function () {
            if (this._cardpointeListenerAttached) {
                return;
            }
            this._cardpointeListenerAttached = true;
            this._cardpointeTokens = {};
            this._cardpointeTokenWaiters = {};

            window.addEventListener('message', event => {
                const data = this._normalizeCardpointeMessage(event);
                if (!data) {
                    return;
                }

                this._applyCardpointeIframeUpdate(data.providerId, data.payload);

                const tokenPayload = this._extractCardpointeToken(data.payload);
                if (!tokenPayload) {
                    return;
                }

                this._cardpointeTokens[data.providerId] = tokenPayload;
                if (this._cardpointeTokenWaiters[data.providerId]) {
                    this._cardpointeTokenWaiters[data.providerId](tokenPayload);
                    delete this._cardpointeTokenWaiters[data.providerId];
                }
            });
        },

        _requestCardpointeTokenization: function (providerId) {
            const wrapper = this._getCardpointeWrapperByProviderId(providerId);
            const iframe = wrapper ? wrapper.querySelector('iframe.o_cardpointe_iframe') : null;
            if (!iframe || !iframe.contentWindow) {
                return;
            }

            const targetOrigin = this._getCardpointeOrigin(wrapper.dataset.tokenizerUrl || '') || '*';
            const tokenizeMessages = [
                'tokenize',
                JSON.stringify({action: 'tokenize'}),
                JSON.stringify({message: 'tokenize'}),
            ];
            tokenizeMessages.forEach(message => {
                iframe.contentWindow.postMessage(message, targetOrigin);
            });
        },

        _normalizeCardpointeMessage: function (event) {
            const wrapper = this._getCardpointeWrapperByOrigin(event.origin);
            if (!wrapper) {
                return null;
            }
            const providerId = parseInt(wrapper.dataset.providerId, 10);
            if (!providerId) {
                return null;
            }

            const payload = this._parseCardpointeMessage(event.data);
            if (!payload) {
                return null;
            }
            return {
                providerId: providerId,
                payload: payload,
            };
        },

        _applyCardpointeIframeUpdate: function (providerId, payload) {
            const iframeHeight = this._extractCardpointeIframeHeight(payload);
            if (!iframeHeight) {
                return;
            }

            const wrapper = this._getCardpointeWrapperByProviderId(providerId);
            const iframe = wrapper ? wrapper.querySelector('iframe.o_cardpointe_iframe') : null;
            if (!iframe) {
                return;
            }
            iframe.style.height = `${iframeHeight}px`;
            iframe.style.minHeight = `${iframeHeight}px`;
        },

        _extractCardpointeIframeHeight: function (payload) {
            if (!payload || typeof payload !== 'object') {
                return null;
            }

            const nestedData = this._parseCardpointeMessage(payload.data);
            const nestedResponse = this._parseCardpointeMessage(payload.response);
            const nestedMessage = this._parseCardpointeMessage(payload.message);
            const candidates = [
                payload.height,
                payload.iframeHeight,
                payload.frameHeight,
                nestedData && nestedData.height,
                nestedData && nestedData.iframeHeight,
                nestedResponse && nestedResponse.height,
                nestedResponse && nestedResponse.iframeHeight,
                nestedMessage && nestedMessage.height,
                nestedMessage && nestedMessage.iframeHeight,
            ];

            for (const candidate of candidates) {
                const value = this._normalizeCardpointeDimension(candidate);
                if (value) {
                    return value;
                }
            }
            return null;
        },

        _normalizeCardpointeDimension: function (value) {
            if (value === null || value === undefined || value === false) {
                return null;
            }
            const raw = typeof value === 'string' ? value.replace(/px$/i, '').trim() : value;
            const parsed = parseInt(raw, 10);
            if (!Number.isFinite(parsed) || parsed <= 0) {
                return null;
            }
            return parsed;
        },

        _extractCardpointeToken: function (payload) {
            if (!payload || typeof payload !== 'object') {
                return null;
            }

            const nestedData = this._parseCardpointeMessage(payload.data);
            const nestedResponse = this._parseCardpointeMessage(payload.response);
            const nestedMessage = this._parseCardpointeMessage(payload.message);
            const tokenCandidates = [
                payload.token,
                nestedData && nestedData.token,
                nestedResponse && nestedResponse.token,
                nestedMessage && nestedMessage.token,
                this._looksLikeCardpointeToken(payload.message) ? payload.message : null,
                this._looksLikeCardpointeToken(payload.data) ? payload.data : null,
            ];
            const token = tokenCandidates.find(candidate => this._looksLikeCardpointeToken(candidate));
            if (!token) {
                return null;
            }
            return {
                token: token,
                meta: {
                    expiry_month: payload.expiry_month || (nestedData && nestedData.expiry_month),
                    expiry_year: payload.expiry_year || (nestedData && nestedData.expiry_year),
                    brand: payload.brand || (nestedData && nestedData.brand),
                    last4: payload.last4 || (nestedData && nestedData.last4),
                },
            };
        },

        _looksLikeCardpointeToken: function (value) {
            return typeof value === 'string' && /^[A-Za-z0-9]{10,}$/.test(value.trim());
        },

        _getCardpointeTokenPayload: function (providerId) {
            return this._cardpointeTokens && this._cardpointeTokens[providerId];
        },

        _waitForCardpointeToken: function (providerId) {
            const existing = this._getCardpointeTokenPayload(providerId);
            if (existing) {
                return Promise.resolve(existing);
            }
            return new Promise(resolve => {
                this._cardpointeTokenWaiters[providerId] = resolve;
                window.setTimeout(() => {
                    if (this._cardpointeTokenWaiters[providerId]) {
                        delete this._cardpointeTokenWaiters[providerId];
                        resolve(null);
                    }
                }, 12000);
            });
        },

        _getCardpointeWrapperByProviderId: function (providerId) {
            const wrappers = document.querySelectorAll('.o_cardpointe_iframe_wrapper');
            for (const wrapper of wrappers) {
                const wrapperProviderId = parseInt(wrapper.dataset.providerId, 10);
                if (wrapperProviderId === providerId) {
                    return wrapper;
                }
            }
            return null;
        },

        _getCardpointeSaveTokenValue: function (providerId) {
            const inlineForm = document.querySelector(`#o_payment_provider_inline_form_${providerId}`);
            if (!inlineForm) {
                return false;
            }
            const checkbox = inlineForm.querySelector('input[name="o_payment_save_as_token"]');
            return !!(checkbox && checkbox.checked);
        },

        _parseCardpointeMessage: function (payload) {
            if (typeof payload === 'string') {
                const trimmed = payload.trim();
                if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) {
                    return null;
                }
                try {
                    return JSON.parse(trimmed);
                } catch (err) {
                    return null;
                }
            }
            if (payload && typeof payload === 'object') {
                return payload;
            }
            return null;
        },

        _getCardpointeWrapperByOrigin: function (origin) {
            const wrappers = document.querySelectorAll('.o_cardpointe_iframe_wrapper');
            for (const wrapper of wrappers) {
                const allowedOrigin = this._getCardpointeOrigin(wrapper.dataset.tokenizerUrl || '');
                if (allowedOrigin && allowedOrigin === origin) {
                    return wrapper;
                }
            }
            return null;
        },

        _getCardpointeOrigin: function (tokenizerUrl) {
            if (!tokenizerUrl) {
                return '';
            }
            try {
                return new URL(tokenizerUrl).origin;
            } catch (err) {
                return '';
            }
        },
    };

    checkoutForm.include(cardpointeMixin);
    manageForm.include(cardpointeMixin);
});