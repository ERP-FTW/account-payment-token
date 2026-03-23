import json
from decimal import Decimal


def amount_meets_threshold(amount_dollars, threshold):
    amount = Decimal(str(amount_dollars or 0))
    threshold_value = Decimal(str(threshold or 0))
    return amount >= threshold_value


def parse_emv_tag_data(data):
    if not data:
        return {}
    if isinstance(data, dict):
        return data
    if isinstance(data, str):
        text = data.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _to_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {'true', '1', 'yes', 'y'}:
            return True
        if normalized in {'false', '0', 'no', 'n'}:
            return False
    return None


def emv_indicates_signature_applicable(emv_dict):
    if not isinstance(emv_dict, dict) or not emv_dict:
        return False

    signature_value = _to_bool(emv_dict.get('Signature'))
    if signature_value is not None:
        return signature_value

    # Fallback: inspect CVM Results (9F34) when present.
    # If value indicates PIN verification, signature is not applicable.
    cvm_raw = emv_dict.get('9F34') or emv_dict.get('cvmResults') or emv_dict.get('CVMResults')
    if not cvm_raw:
        return False

    cvm_text = str(cvm_raw).strip().lower()
    if any(token in cvm_text for token in ('verified by pin', 'enciphered pin', 'online pin', 'offline pin')):
        return False

    # If CVM info exists and doesn't indicate PIN verification, allow signature capture.
    return True
