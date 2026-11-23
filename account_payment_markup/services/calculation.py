UNSET = object()


def _rounded(currency_round, value):
    """Keep Odoo's float_round contract: never pass Decimal to it."""
    return currency_round(float(value or 0.0))


def calculate_taxed_markup(fee_amount, taxes, currency, product=None, partner=None,
                           rate_included=False):
    """Return the net/tax/gross fee under one explicit rate-tax contract.

    ``rate_included`` describes the configured surcharge rate, not the
    product's tax flag: an included rate is the customer-facing gross fee;
    an excluded rate is the tax-excluded net fee.  This distinction is shared
    by portal, payments and POS so a price-included service product cannot
    silently change the surcharge contract.
    """
    fee = currency.round(float(fee_amount or 0.0))
    if not taxes or not fee:
        return {'net_amount': fee, 'tax_amount': 0.0, 'gross_amount': fee}

    def compute(value, handle_price_include=True):
        return taxes.compute_all(
            value, currency=currency, quantity=1.0, product=product,
            partner=partner, handle_price_include=handle_price_include,
        )

    effective_taxes = taxes.flatten_taxes_hierarchy()
    if not rate_included:
        data = compute(fee, handle_price_include=False)
        net, gross = fee, currency.round(data['total_included'])
    elif all(tax.price_include for tax in effective_taxes):
        data = compute(fee)
        net = currency.round(data['total_excluded'])
        # ``fee`` is the configured customer-facing gross for an included-rate
        # fee.  The first native computation already provides the attainable
        # net/tax/gross split; feeding ``net`` back through an included tax
        # would interpret that net as another gross and apply the extraction
        # twice.
        gross = currency.round(data['total_included'])
    else:
        # Invert the excluded-tax calculation so the terminal/customer total
        # remains the configured gross fee.  This is cent-rounded below, then
        # recomputed to keep tax_amount tied to the final posted values.
        low, high = 0.0, fee
        for _i in range(48):
            middle = (low + high) / 2.0
            if compute(middle, handle_price_include=False)['total_included'] > fee:
                high = middle
            else:
                low = middle
        net = currency.round(low)
        data = compute(net, handle_price_include=False)
        # Native tax computation is authoritative for the posted gross.
        gross = currency.round(data['total_included'])
    return {
        'net_amount': net,
        'tax_amount': currency.round(gross - net),
        'gross_amount': gross,
    }


def calculate_markup(base_amount, percent, currency_round, eligible=False, processor_amount=UNSET):
    """Return one immutable fee decision.

    ``processor_amount`` deliberately uses UNSET so an authoritative zero is
    different from a missing processor response.
    """
    base = _rounded(currency_round, base_amount)
    # A processor amount is authoritative only after the server has proven
    # eligibility.  Never let public checkout metadata turn an otherwise
    # disabled/unknown instrument into a fee-bearing payment.
    if eligible and processor_amount is not UNSET:
        amount = _rounded(currency_round, processor_amount)
        return {
            'base_amount': base, 'rate': 0.0, 'amount': amount,
            'total': _rounded(currency_round, base + amount), 'source': 'processor',
        }
    if not eligible:
        return {'base_amount': base, 'rate': 0.0, 'amount': 0.0, 'total': base, 'source': 'none'}
    rate = float(percent or 0.0)
    amount = _rounded(currency_round, base * rate / 100.0)
    return {
        'base_amount': base, 'rate': rate, 'amount': amount,
        'total': _rounded(currency_round, base + amount), 'source': 'local',
    }
