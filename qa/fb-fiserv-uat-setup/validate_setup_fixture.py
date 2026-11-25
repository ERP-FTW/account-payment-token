#!/usr/bin/env python3
"""Offline producer qualification for fb_fiserv_uat_fixture.

The validator is credential-free and performs no Odoo or network operation.  It
checks the exact module contract that the reviewed candidate must carry and
emits a hash-bound JSON receipt suitable for inclusion in the packet evidence.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path


REQUIRED_DEPENDS = {
    "account",
    "payment",
    "payment_demo",
    "payment_cardpointe_base",
    "payment_cardpointe",
    "account_payment_markup",
    "account_payment_markup_cardpointe",
}
EXPECTED_CASES = (
    ("success", "FB-UAT-success", 10.0),
    ("decline", "FB-UAT-decline", 10.0),
    ("markup_on", "FB-UAT-markup_on", 100.0),
    ("markup_off", "FB-UAT-markup_off", 100.0),
    ("token_portable_v3", "FB-UAT-token-portable-v3", 10.0),
)
FORBIDDEN_HOOK_TEXT = {
    "gateway_username",
    "gateway_password",
    "cardpointe_username",
    "cardpointe_password",
    "cardpointe_mid",
    "execute_kw",
    "requests.",
    "urllib",
    "socket.",
    ".unlink(",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def literal_assignment(tree: ast.Module, name: str):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise ValueError(f"{name} assignment is missing")


def validate(root: Path) -> dict:
    findings = []
    expected_files = {"__init__.py", "__manifest__.py", "hooks.py"}
    present = {item.name for item in root.iterdir()} if root.is_dir() else set()
    missing = sorted(expected_files - present)
    extra_python = sorted(name for name in present if name.endswith(".py") and name not in expected_files)
    if missing:
        findings.append("missing files: " + ", ".join(missing))
    if extra_python:
        findings.append("unexpected Python files: " + ", ".join(extra_python))
    if findings:
        return {"status": "FAIL", "findings": findings}

    manifest_path = root / "__manifest__.py"
    hooks_path = root / "hooks.py"
    init_path = root / "__init__.py"
    try:
        manifest = ast.literal_eval(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        findings.append(f"manifest is not one literal mapping: {type(exc).__name__}")
        manifest = {}
    if manifest.get("version") != "19.0.1.0.0":
        findings.append("manifest version must be 19.0.1.0.0")
    if manifest.get("post_init_hook") != "post_init_hook":
        findings.append("manifest must declare post_init_hook")
    if manifest.get("installable") is not True or manifest.get("auto_install") is not False:
        findings.append("module must be explicitly installable and never auto-install")
    if set(manifest.get("depends") or ()) != REQUIRED_DEPENDS:
        findings.append("manifest dependency set differs from the reviewed contract")
    if manifest.get("data") != []:
        findings.append("module must not carry implicit XML/CSV mutation files")

    hooks_text = hooks_path.read_text(encoding="utf-8")
    for token in sorted(FORBIDDEN_HOOK_TEXT):
        if token in hooks_text:
            findings.append(f"hooks contain forbidden capability or secret field token: {token}")
    try:
        tree = ast.parse(hooks_text, filename=str(hooks_path))
        cases = tuple(tuple(item) for item in literal_assignment(tree, "INVOICE_CASES"))
        if cases != EXPECTED_CASES:
            findings.append("INVOICE_CASES differs from the five reviewed refs and amounts")
        function_names = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        required_functions = {
            "post_init_hook",
            "_company",
            "_partner",
            "_providers",
            "_accounting_relations",
            "_existing_invoices",
            "_create_invoice",
            "_bind",
        }
        missing_functions = sorted(required_functions - function_names)
        if missing_functions:
            findings.append("missing bounded hook functions: " + ", ".join(missing_functions))
    except Exception as exc:
        findings.append(f"hooks AST validation failed: {type(exc).__name__}: {exc}")

    required_markers = (
        "move.action_post()",
        "payment_cardpointe.payment_provider_cardpointe",
        "payment.payment_provider_demo",
        "cardpointe_merchant_config_id",
        "markup_product_id",
        "payment.transaction",
        "precision_rounding=currency.rounding",
    )
    for marker in required_markers:
        if marker not in hooks_text:
            findings.append(f"required bounded behavior is absent: {marker}")
    init_text = init_path.read_text(encoding="utf-8").strip()
    if init_text != "from .hooks import post_init_hook":
        findings.append("__init__.py must export only post_init_hook")

    files = {
        path.name: {"sha256": digest(path), "bytes": path.stat().st_size}
        for path in (init_path, manifest_path, hooks_path)
    }
    return {
        "schema": "hermes-fb-fiserv-uat-fixture-qualification/v1",
        "module": "fb_fiserv_uat_fixture",
        "status": "PASS" if not findings else "FAIL",
        "candidate_contract": {
            "company": "Counter Shop Demo",
            "currency": "USD",
            "customer_email": "fiserv-uat@example.invalid",
            "invoice_cases": [
                {"key": key, "ref": ref, "amount": amount}
                for key, ref, amount in EXPECTED_CASES
            ],
            "native_invoice_transition": "account.move.action_post",
            "external_payment_effect": False,
            "secret_fields_in_payload": False,
        },
        "files": files,
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("module_root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate(args.module_root.resolve())
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
