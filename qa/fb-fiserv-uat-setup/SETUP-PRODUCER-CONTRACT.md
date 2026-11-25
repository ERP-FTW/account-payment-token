# Full Belly Fiserv UAT setup producer contract

## Decision and supported route

The installed `hermes-odoo-uat-fixture/v3` route remains the supported choice
when every reused relation already has a stable Odoo external ID. It supports
ordered `$ref` relations and bounded post-seed actions such as
`account.move.action_post`, with `expect_before` / `expect_after` state checks.
It intentionally does **not** guess an arbitrary existing record that lacks an
external ID.

The Full Belly target needs exact same-company relations that may lack fixture
XML IDs: the linked CardPointe merchant, markup product and its income account,
the native sales journal, the Demo bank journal, and the partner property
accounts. The bounded supported alternate for this one setup is therefore a
**reviewed Odoo fixture addon**, `fb_fiserv_uat_fixture`, carried by the same
candidate as the Fiserv implementation. It uses the already-supported reviewed
code publication, backup, module-install, and module-state routes. No generic
stage-write capability, new engine schema, direct RPC, or unreviewed stage
Python is introduced.

Candidate source is in `fb_fiserv_uat_fixture/`. The sibling `proof/` directory
is disposable-lab evidence support only and must not be copied into the client
candidate.

## Exact behavior

The addon install hook:

- resolves exactly one USD company named **Counter Shop Demo**;
- resolves exactly one same-company CardPointe merchant configuration and the
  installed `payment_cardpointe.payment_provider_cardpointe` provider;
- reuses the provider's markup product, or exactly one active same-company or
  shared service product named **Manual checkout fee** when the relation is
  absent;
- requires that product to be untaxed and backed by a valid income account;
- uses Odoo's native customer-invoice default journal for the company;
- reuses the Demo provider's bank journal, or exactly one active same-company
  bank journal when the relation is absent;
- adopts exactly one customer with `fiserv-uat@example.invalid`, or creates it
  if absent and the installed chart provides valid receivable/payable defaults;
- adopts only an exact, fully-due, posted, untaxed invoice with no payment
  transactions, otherwise refuses without rewriting it;
- creates missing invoices and posts them with native
  `account.move.action_post()`:
  - `FB-UAT-success` — USD 10
  - `FB-UAT-decline` — USD 10
  - `FB-UAT-markup_on` — USD 100
  - `FB-UAT-markup_off` — USD 100
  - `FB-UAT-token-portable-v3` — USD 10
- publishes stable `fb_fiserv_uat_fixture.*` aliases for every resolved or
  created relation and invoice;
- never resets, reposts, deletes, pays, refunds, sends, notifies, or invokes a
  processor;
- never reads, writes, serializes, or logs merchant credential fields.

All lookups precede the first setup mutation. Odoo module installation is one
database transaction, so a refusal rolls the installation back. An existing
paid, partially paid, transaction-linked, wrong-company, wrong-amount, taxed,
or ambiguous row is preserved and causes a refusal; the producer must choose a
new reviewed ref rather than repurpose it.

Provider scalar state remains in the three existing
`hermes-odoo-stage-configuration/v1` artifacts. This addon only supplies the
missing relational setup, synthetic records, native invoice posting, and stable
aliases.

## Producer qualification (no stage access)

From the producer workspace, after placing the module at the candidate root:

```bash
python3 validate_setup_fixture.py \
  <candidate-root>/fb_fiserv_uat_fixture \
  --output fb-fiserv-uat-fixture-qualification.json
```

A passing receipt has schema
`hermes-fb-fiserv-uat-fixture-qualification/v1`, the exact five refs/amounts,
file SHA-256 values, `native_invoice_transition=account.move.action_post`,
`external_payment_effect=false`, and `secret_fields_in_payload=false`.

The producer then qualifies the exact candidate in a fresh own lab. The proof
scripts accept any database created by the supported `odoo-lab` runtime
(`env.cr.dbname` begins with `guild_`) and refuse non-lab/client databases. They
are intentionally independent of the producer task ID so an independent
reviewer can run the committed scripts unchanged in a reviewer-owned lab. The
commands below show the supported sequence; use the current worker's own
task-scoped lab name and exact candidate source:

```bash
LAB_TASK="${HERMES_KANBAN_TASK}-fiserv-setup"
odoo-lab create --task "$LAB_TASK" --version 19.0 --edition community \
  --addons <candidate-root> \
  --addon-module payment_cardpointe_base \
  --addon-module payment_cardpointe \
  --addon-module account_payment_markup \
  --addon-module account_payment_markup_cardpointe \
  --addon-module payment_token_partner_form \
  --addon-module payment_token_invoice \
  --addon-module fb_fiserv_uat_fixture
odoo-lab install --task "$LAB_TASK" --module payment_token_invoice
odoo-lab install --task "$LAB_TASK" --module payment_token_partner_form
odoo-lab install --task "$LAB_TASK" --module payment_cardpointe
odoo-lab install --task "$LAB_TASK" --module account_payment_markup_cardpointe
# Prepare non-routable lab-only prerequisites; never run this script on stage.
odoo-lab shell --task "$LAB_TASK" --command "$(<proof/lab_prepare_prerequisites.py)"
# Prove one ambiguous symbolic relation refuses before any fixture ORM mutation,
# then removes its temporary duplicate and leaves the lab ready for installation.
odoo-lab shell --task "$LAB_TASK" --command "$(<proof/lab_test_ambiguous_relation.py)"
odoo-lab install --task "$LAB_TASK" --module fb_fiserv_uat_fixture
odoo-lab shell --task "$LAB_TASK" --command "$(<proof/lab_verify.py)"
# Re-run the exact hook in the disposable lab to prove convergence/no duplicates.
odoo-lab shell --task "$LAB_TASK" --command \
  'from odoo.addons.fb_fiserv_uat_fixture.hooks import post_init_hook; post_init_hook(env)'
odoo-lab shell --task "$LAB_TASK" --command "$(<proof/lab_verify.py)"
odoo-lab gate --task "$LAB_TASK" --module fb_fiserv_uat_fixture \
  --check status --json-result fb-fiserv-uat-fixture-gate.json
```

The proof preparation uses only a non-routable lab merchant and creates no
payment transaction. The ambiguity proof temporarily creates a second
non-routable merchant relation, guards every ORM `create`/`write` entry point,
requires the exact two-record refusal, verifies fixture state is unchanged,
and removes the duplicate before normal installation. The authoritative proof
is its PASS result, the two identical PASS readbacks around the second hook
invocation, and a PASS gate. The producer and reviewer must each use a fresh lab;
never replay a paid Demo invoice from earlier evidence.

## Packet and independent review

The same canonical packet must contain:

- the exact candidate commit containing `fb_fiserv_uat_fixture/`;
- `fb-fiserv-uat-fixture-qualification.json`;
- fresh-lab preparation and ambiguity-refusal logs, install/readback logs, and
  gate receipt;
- the three unchanged scalar configuration artifacts;
- the CardPointe UAT selector package and all prior portable-v3 evidence;
- an implementation plan that installs the fixture addon only after exact
  candidate deployment and a completed stage backup.

Independent review repeats the qualification and fresh-lab install under the
reviewer's own lab identity using the committed proof scripts unchanged. It must
run the ambiguity-refusal proof, inspect that the hook has no secret-field
access or external-effect method, and prove that the second invocation creates
no additional customer or invoice.

## Reviewed stage execution

Only `odoo-implement` on the owning requirement card executes the reviewed
candidate. After exact deployment, current target/deploy readback, and a
completed backup:

```bash
hermes-fb-stage-ops --authority fb-odoo19-stage \
  module-install --backup-id <completed-backup-id> \
  --name fb_fiserv_uat_fixture \
  --output fb-fiserv-uat-fixture-install.json
hermes-fb-stage-ops --authority fb-odoo19-stage \
  module-state --name fb_fiserv_uat_fixture \
  --output fb-fiserv-uat-fixture-state.json
```

The implementer then runs read-only packet assertions resolving the published
`fb_fiserv_uat_fixture.*` aliases, applies only the independently approved
scalar configurations needed for the current case, and continues the existing
CardPointe UAT execution contract. A module-install refusal is evidence of the
exact ambiguous or changed client data and must not be bypassed with raw IDs,
manual stage Python, or a generic write grant.
