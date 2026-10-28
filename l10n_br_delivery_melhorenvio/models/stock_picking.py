# Copyright 2026 PopSolutions
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    melhorenvio_order_id = fields.Char(
        string="Melhor Envio Order",
        readonly=True,
        copy=False,
        help="Identifier of the shipment bought in Melhor Envio.",
    )

    def _melhorenvio_invoice_key(self):
        """Access key of the authorized NF-e billing this delivery, when the
        Brazilian fiscal modules are installed; empty otherwise, and the
        shipment goes out with the declaração de conteúdo."""
        self.ensure_one()
        invoices = self.sale_id.invoice_ids.filtered(
            lambda move: move.state == "posted"
        )
        for invoice in invoices:
            key = getattr(invoice, "document_key", "")
            if key:
                return key.replace("NFe", "")
        return ""
