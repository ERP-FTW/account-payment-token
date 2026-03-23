from decimal import Decimal, ROUND_HALF_UP


def dollars_to_implied_cents(amount_dollars):
    value = Decimal(str(amount_dollars or '0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return str(int(value * 100))


def cents_to_dollars_str(amount_cents):
    cents = Decimal(str(amount_cents or '0'))
    return f"{(cents / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"


def format_gateway_amount(amount_dollars):
    value = Decimal(str(amount_dollars or '0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return f"{value:.2f}"
