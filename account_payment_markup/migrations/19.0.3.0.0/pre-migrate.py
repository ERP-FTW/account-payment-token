from odoo.tools.sql import SQL, column_exists, table_exists


def migrate(cr, version):
    # Keep the settings that formerly lived on provider/method rows, including the
    # optional bridge's columns, before Odoo retires that model.
    if table_exists(cr, 'payment_method_fee'):
        cr.execute("CREATE TABLE IF NOT EXISTS legacy_payment_method_fee AS TABLE payment_method_fee")
    elif column_exists(cr, 'payment_provider', 'markup_enabled'):
        cr.execute("""
            CREATE TABLE IF NOT EXISTS legacy_payment_method_fee AS
            SELECT r.payment_method_id AS method_id, p.id AS provider_id,
                   p.markup_product_id, p.markup_percent, p.markup_tax_included
              FROM payment_provider p JOIN payment_method_payment_provider_rel r
                ON r.payment_provider_id = p.id WHERE p.markup_enabled
        """)
    # Existing journal entries and their historical fee data survive the removal
    # of the custom Register Payment and refund features from the ORM.
    cr.execute("""
        SELECT f.model, f.name FROM ir_model_fields f JOIN ir_model_data d
          ON d.model='ir.model.fields' AND d.res_id=f.id
         WHERE d.module='account_payment_markup' AND f.store
           AND f.model IN ('account.move','account.move.line','account.payment',
                           'account.payment.method.line','payment.transaction')
           AND f.name LIKE 'markup_%'
    """)
    for model, field in cr.fetchall():
        if model == 'payment.transaction' and field in ('markup_amount', 'markup_fee_move_id', 'markup_invoice_line_id'):
            continue
        table = model.replace('.', '_')
        if column_exists(cr, table, field):
            cr.execute(SQL('ALTER TABLE %s RENAME COLUMN %s TO %s', SQL.identifier(table),
                           SQL.identifier(field), SQL.identifier('legacy_' + field)))
    # The optional bridge's previous xpath targeted a nested list being removed.
    cr.execute("""
        UPDATE ir_ui_view SET active=false WHERE id IN (
            SELECT res_id FROM ir_model_data WHERE model='ir.ui.view'
            AND module='account_payment_markup_cardpointe' AND name='online_card_program')
    """)
