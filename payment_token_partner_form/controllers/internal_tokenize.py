# -*- coding: utf-8 -*-
import logging
import werkzeug

from odoo import http
from odoo.http import request
from odoo.addons.payment import utils as payment_utils

_logger = logging.getLogger(__name__)


class InternalTokenizeController(http.Controller):
    """Internal-only route that reuses Odoo's standard tokenization UI for a chosen partner."""

    @http.route(
        "/payment/internal/payment_method/<int:partner_id>/<int:company_id>",
        type="http",
        auth="user",
        website=True,
        methods=["GET"],
    )
    def internal_payment_method(self, partner_id, company_id, tx_id=None, access_token=None, **kwargs):
        user = request.env.user

        # Require internal users (not portal/public)
        if not user.has_group("base.group_user"):
            _logger.warning(
                "[partner_internal_payment_tokenize] Access denied to user_id=%s (not internal).",
                user.id,
            )
            raise werkzeug.exceptions.Forbidden()

        partner_sudo = request.env["res.partner"].sudo().browse(partner_id).exists()
        if not partner_sudo:
            raise werkzeug.exceptions.NotFound()

        company_sudo = request.env["res.company"].sudo().browse(company_id).exists()
        if not company_sudo:
            company_sudo = request.env.company.sudo()

        _logger.info(
            "[partner_internal_payment_tokenize] Render tokenization page. "
            "user_id=%s partner_id=%s company_id=%s tx_id=%s",
            user.id, partner_sudo.id, company_sudo.id, tx_id,
        )

        availability_report = {}

        # Equivalent to /my/payment_method logic but for chosen partner.
        try:
            request.env["payment.provider"]
        except KeyError:
            provider_model_name = "payment.acquirer"
        else:
            provider_model_name = "payment.provider"
        provider_model = request.env[provider_model_name].sudo().with_company(company_sudo)
        amount = 0.0
        compat_kwargs = dict(kwargs)
        compat_kwargs.setdefault("currency_id", company_sudo.currency_id.id)
        if hasattr(provider_model, "_get_compatible_providers"):
            try:
                providers_sudo = provider_model._get_compatible_providers(
                    company_sudo.id,
                    partner_sudo.id,
                    amount,
                    force_tokenization=True,
                    is_validation=True,
                    report=availability_report,
                    **compat_kwargs,
                )
            except TypeError:
                providers_sudo = provider_model._get_compatible_providers(
                    company_sudo.id,
                    partner_sudo.id,
                    amount,
                    force_tokenization=True,
                    is_validation=True,
                    report=availability_report,
                    **kwargs,
                )
        else:
            try:
                providers_sudo = provider_model._get_compatible_acquirers(
                    company_sudo.id,
                    partner_sudo.id,
                    amount,
                    force_tokenization=True,
                    is_validation=True,
                    report=availability_report,
                    **compat_kwargs,
                )
            except TypeError:
                providers_sudo = provider_model._get_compatible_acquirers(
                    company_sudo.id,
                    partner_sudo.id,
                    amount,
                    force_tokenization=True,
                    is_validation=True,
                    report=availability_report,
                    **kwargs,
                )
        if not providers_sudo:
            provider_domain = [
                ("company_id", "in", [company_sudo.id, False]),
                ("state", "=", "enabled"),
            ]
            if "allow_tokenization" in provider_model._fields:
                provider_domain.append(("allow_tokenization", "=", True))
            providers_sudo = provider_model.search(provider_domain)

        try:
            request.env["payment.method"]
        except KeyError:
            payment_methods_sudo = request.env["payment.token"].sudo().browse()
        else:
            payment_methods_sudo = (
                request.env["payment.method"]
                .sudo()
                ._get_compatible_payment_methods(
                    providers_sudo.ids,
                    partner_sudo.id,
                    force_tokenization=True,
                    report=availability_report,
                )
            )
        token_model = request.env["payment.token"].sudo()
        if hasattr(token_model, "_get_available_tokens"):
            tokens_sudo = token_model._get_available_tokens(
                None, partner_sudo.id, is_validation=True
            )
        else:
            tokens_sudo = token_model.search(
                [
                    ("partner_id", "=", partner_sudo.id),
                    ("company_id", "in", [company_sudo.id, False]),
                    ("active", "=", True),
                ]
            )

        try:
            computed_access_token = payment_utils.generate_access_token(
                partner_sudo.id, None, None
            )
        except TypeError:
            computed_access_token = payment_utils.generate_access_token(
                partner_sudo.id,
                0.0,
                company_sudo.currency_id.id,
            )
        landing_route = f"/payment/internal/payment_method/{partner_sudo.id}/{company_sudo.id}"

        payment_form_values = {
            "mode": "validation",
            "allow_token_selection": False,
            "allow_token_deletion": True,
        }
        payment_context = {
            "reference_prefix": payment_utils.singularize_reference_prefix(prefix="V"),
            "partner_id": partner_sudo.id,
            "providers_sudo": providers_sudo,
            "payment_methods_sudo": payment_methods_sudo,
            "tokens_sudo": tokens_sudo,
            "availability_report": availability_report,
            "transaction_route": "/payment/transaction",
            "landing_route": landing_route,
            "access_token": computed_access_token,
        }
        if provider_model_name == "payment.acquirer":
            payment_context["acquirers_sudo"] = providers_sudo

        rendering_context = {**payment_form_values, **payment_context}
        if tx_id:
            rendering_context["internal_tx_id"] = int(tx_id)
        if access_token:
            rendering_context["internal_access_token"] = access_token

        template = "payment.payment_methods"
        if not request.env["ir.ui.view"].sudo().search([("key", "=", template)], limit=1):
            template = "payment.payment_acquirer_list"
        return request.render(template, rendering_context)
