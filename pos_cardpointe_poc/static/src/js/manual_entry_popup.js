/** @odoo-module */

import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { Dialog } from "@web/core/dialog/dialog";

export class CardPointeManualEntryPopup extends Component {
    static template = "pos_cardpointe_poc.CardPointeManualEntryPopup";
    static components = { Dialog };
    static props = [
        "title",
        "tokenizerUrl",
        "allowedEcominds",
        "defaultEcomind",
        "cancelLabel",
        "close",
        "onToken",
    ];

    setup() {
        this.state = useState({
            ecomind: this.props.defaultEcomind || "E",
            tokenError: "",
            iframeLoaded: false,
            submitInProgress: false,
            lastMessageOrigin: "",
        });
        this._boundHandler = (event) => this._handleMessage(event);
        onMounted(() => {
            console.info("[CARDPOINTE POS MANUAL] popup mounted", {
                tokenizerUrlPresent: !!this.props.tokenizerUrl,
                defaultEcomind: this.state.ecomind,
            });
            window.addEventListener("message", this._boundHandler);
        });
        onWillUnmount(() => {
            console.info("[CARDPOINTE POS MANUAL] popup unmounted");
            window.removeEventListener("message", this._boundHandler);
        });
    }

    onIframeLoad() {
        this.state.iframeLoaded = true;
        console.info("[CARDPOINTE POS MANUAL] tokenizer iframe load event fired");
    }

    _safePreview(payload) {
        try {
            const text = typeof payload === "string" ? payload : JSON.stringify(payload || {});
            return text.length > 500 ? `${text.slice(0, 500)}...` : text;
        } catch {
            return "[unserializable payload]";
        }
    }

    _extractToken(payload) {
        if (!payload) {
            return "";
        }

        let data = payload;
        if (typeof data === "string") {
            try {
                data = JSON.parse(data);
            } catch {
                // Some tokenizer implementations post the token as a plain string.
                // Accept only token-looking values, not arbitrary text.
                const trimmed = data.trim();
                if (/^[A-Za-z0-9_-]{12,}$/.test(trimmed)) {
                    return trimmed;
                }
                return "";
            }
        }
        if (typeof data !== "object") {
            return "";
        }

        const candidates = [
            data,
            data.message,
            data.response,
            data.data,
            data.tokenizeResponse,
            data.tokenizerResponse,
        ].filter((item) => item && typeof item === "object");

        for (const candidate of candidates) {
            const token = candidate.token || candidate.account || candidate.acctid || candidate.profile || "";
            if (token) {
                return String(token);
            }
        }
        return "";
    }

    async _handleMessage(event) {
        this.state.lastMessageOrigin = event.origin || "";
        console.info("[CARDPOINTE POS MANUAL] postMessage received", {
            origin: event.origin,
            preview: this._safePreview(event.data),
        });

        const token = this._extractToken(event.data);
        if (!token) {
            this.state.tokenError = _t("Received tokenizer message, but no token was found. Check browser console payload preview.");
            console.warn("[CARDPOINTE POS MANUAL] tokenizer message did not contain token");
            return;
        }

        this.state.tokenError = "";
        this.state.submitInProgress = true;
        console.info("[CARDPOINTE POS MANUAL] token extracted; submitting manual auth", {
            tokenPresent: true,
            ecomind: this.state.ecomind,
        });
        try {
            await this.props.onToken({ token, ecomind: this.state.ecomind });
            console.info("[CARDPOINTE POS MANUAL] manual auth callback completed; closing popup");
            this.props.close();
        } catch (error) {
            console.error("[CARDPOINTE POS MANUAL] manual auth callback failed", error);
            this.state.submitInProgress = false;
            this.state.tokenError = _t("Manual authorization failed. Check the POS error message and server logs.");
        }
    }

    cancel() {
        console.info("[CARDPOINTE POS MANUAL] popup cancelled by cashier");
        this.props.close();
    }
}
