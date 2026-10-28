# Copyright 2026 PopSolutions
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

{
    "name": "Melhor Envio Shipping",
    "summary": "Shipping quotes, labels and tracking through Melhor Envio",
    "category": "Delivery",
    "license": "AGPL-3",
    "author": "PopSolutions, Odoo Community Association (OCA)",
    "maintainers": ["marcos-mendez"],
    "website": "https://github.com/OCA/l10n-brazil",
    "development_status": "Beta",
    "version": "18.0.1.0.0",
    "depends": [
        "delivery_state",
    ],
    "data": [
        "data/product_product_data.xml",
        "views/delivery_carrier_view.xml",
        "views/stock_picking_view.xml",
    ],
    "installable": True,
}
