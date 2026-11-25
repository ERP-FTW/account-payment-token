#!/usr/bin/env python3
"""Focused tests for the disposable-lab proof script boundary."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent
PROOF_SCRIPTS = (
    ROOT / "lab_prepare_prerequisites.py",
    ROOT / "lab_verify.py",
)


def proof_lab_guard(script: Path, database: str) -> bool:
    """Evaluate only the script's first assertion against a synthetic database."""
    tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
    guard = next(node for node in tree.body if isinstance(node, ast.Assert))
    expression = compile(ast.Expression(guard.test), str(script), "eval")
    env = SimpleNamespace(cr=SimpleNamespace(dbname=database))
    return bool(eval(expression, {"env": env}))  # noqa: S307 - reviewed local AST only


class ProofLabGuardTest(unittest.TestCase):
    def test_accepts_producer_and_reviewer_owned_odoo_lab_databases(self):
        for script in PROOF_SCRIPTS:
            with self.subTest(script=script.name, database="guild_t_7179e09b"):
                self.assertTrue(proof_lab_guard(script, "guild_t_7179e09b"))
            with self.subTest(script=script.name, database="guild_t_7179e09b-review-2807"):
                self.assertTrue(proof_lab_guard(script, "guild_t_7179e09b-review-2807"))
            with self.subTest(script=script.name, database="guild_t_other-reviewer"):
                self.assertTrue(proof_lab_guard(script, "guild_t_other-reviewer"))

    def test_refuses_non_lab_databases(self):
        for script in PROOF_SCRIPTS:
            for database in ("create-pos", "fb_odoo19_stage", "prod"):
                with self.subTest(script=script.name, database=database):
                    self.assertFalse(proof_lab_guard(script, database))


if __name__ == "__main__":
    unittest.main()
