# Copyright 2026 PopSolutions
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import patch

from odoo.tests.common import TransactionCase

REQUESTS = (
    "odoo.addons.l10n_br_delivery_melhorenvio.models.delivery_carrier"
    ".requests.request"
)

SERVICES = [
    {"id": 1, "name": "PAC", "company": {"id": 1, "name": "Correios"}},
    {"id": 2, "name": "SEDEX", "company": {"id": 1, "name": "Correios"}},
    {"id": 4, "name": ".Com", "company": {"id": 2, "name": "Jadlog"}},
]

CALCULATE = [
    {
        "id": 1,
        "name": "PAC",
        "price": "29.33",
        "delivery_time": 5,
        "company": {"id": 1, "name": "Correios"},
    },
    {
        "id": 17,
        "name": "Mini Envios",
        "error": "Valor segurado ultrapassa o limite máximo de R$ 111,93.",
        "company": {"id": 1, "name": "Correios"},
    },
]

CART_ORDER = {
    "id": "0192aa55-6f4e-7d1c-9c3c-d1e6f14f6bfa",
    "protocol": "ORD-202610280001",
    "price": 29.33,
    "status": "pending",
}

TRACKING = {
    "0192aa55-6f4e-7d1c-9c3c-d1e6f14f6bfa": {
        "tracking": "BR267566353956A",
        "status": "delivered",
    }
}


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.reason = "OK"

    def json(self):
        return self.payload


class TestDeliveryMelhorEnvio(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.partner_id.zip = "01130-010"
        cls.carrier = cls.env["delivery.carrier"].create(
            {
                "name": "Melhor Envio",
                "delivery_type": "melhorenvio",
                "melhorenvio_token": "A-TOKEN",
                "product_id": cls.env.ref(
                    "l10n_br_delivery_melhorenvio"
                    ".product_product_delivery_melhorenvio"
                ).id,
            }
        )
        cls.product = cls.env["product.product"].create(
            {"name": "Kit", "type": "consu", "weight": 0.44, "list_price": 914.40}
        )
        cls.partner = cls.env["res.partner"].create(
            {"name": "Cliente", "zip": "83824-505"}
        )
        cls.order = cls.env["sale.order"].create(
            {
                "partner_id": cls.partner.id,
                "order_line": [
                    (0, 0, {"product_id": cls.product.id, "product_uom_qty": 1})
                ],
            }
        )

    def _sync(self):
        with patch(REQUESTS, return_value=FakeResponse(SERVICES)):
            self.carrier.action_melhorenvio_sync_services()
        return self.env["delivery.carrier"].search(
            [
                ("delivery_type", "=", "melhorenvio"),
                ("melhorenvio_service_id", "!=", 0),
            ]
        )

    def test_sync_services(self):
        carriers = self._sync()
        self.assertEqual(len(carriers), 3)
        pac = carriers.filtered(lambda c: c.melhorenvio_service_id == 1)
        self.assertEqual(pac.name, "Correios PAC")
        self.assertEqual(pac.melhorenvio_token, "A-TOKEN")
        self.assertEqual(len(self._sync()), 3)

    def test_rate_shipment(self):
        pac = self._sync().filtered(lambda c: c.melhorenvio_service_id == 1)
        with patch(REQUESTS, return_value=FakeResponse(CALCULATE)) as request:
            rate = pac.melhorenvio_rate_shipment(self.order)
        self.assertTrue(rate["success"])
        self.assertEqual(rate["price"], 29.33)
        self.assertIn("5", rate["warning_message"])
        payload = request.call_args.kwargs["json"]
        self.assertEqual(payload["from"]["postal_code"], "01130010")
        self.assertEqual(payload["to"]["postal_code"], "83824505")
        self.assertEqual(payload["services"], "1")
        self.assertEqual(payload["products"][0]["weight"], self.product.weight)
        self.assertEqual(
            payload["products"][0]["insurance_value"],
            round(self.order.order_line[0].price_total, 2),
        )

    def test_rate_shipment_service_error(self):
        pac = self._sync().filtered(lambda c: c.melhorenvio_service_id == 1)
        pac.melhorenvio_service_id = 17
        with patch(REQUESTS, return_value=FakeResponse(CALCULATE)):
            rate = pac.melhorenvio_rate_shipment(self.order)
        self.assertFalse(rate["success"])
        self.assertIn("Valor segurado", rate["error_message"])

    def test_rate_shipment_single_object_answer(self):
        """Filtered to one service the API answers the object, not a list."""
        pac = self._sync().filtered(lambda c: c.melhorenvio_service_id == 1)
        with patch(REQUESTS, return_value=FakeResponse(CALCULATE[0])):
            rate = pac.melhorenvio_rate_shipment(self.order)
        self.assertTrue(rate["success"])
        self.assertEqual(rate["price"], 29.33)

    def test_send_shipping_buys_and_labels(self):
        pac = self._sync().filtered(lambda c: c.melhorenvio_service_id == 1)
        self.order.carrier_id = pac
        self.order.action_confirm()
        picking = self.order.picking_ids[0]

        responses = iter(
            [
                FakeResponse(CART_ORDER),  # cart
                FakeResponse({"purchase": {}}),  # checkout
                FakeResponse({}),  # generate
                FakeResponse({"url": ""}),  # print (no url: skip download)
            ]
        )
        with patch(REQUESTS, side_effect=lambda *a, **k: next(responses)):
            result = pac.melhorenvio_send_shipping(picking)

        self.assertEqual(picking.melhorenvio_order_id, CART_ORDER["id"])
        self.assertEqual(result[0]["exact_price"], 29.33)
        self.assertEqual(result[0]["tracking_number"], CART_ORDER["protocol"])

    def test_cart_payload_without_invoice_is_non_commercial(self):
        pac = self._sync().filtered(lambda c: c.melhorenvio_service_id == 1)
        self.order.carrier_id = pac
        self.order.action_confirm()
        picking = self.order.picking_ids[0]
        payload = pac._melhorenvio_cart_payload(picking)
        self.assertTrue(payload["options"]["non_commercial"])
        self.assertNotIn("invoice", payload["options"])
        self.assertEqual(payload["service"], 1)
        self.assertEqual(payload["to"]["postal_code"], "83824505")

    def test_tracking(self):
        pac = self._sync().filtered(lambda c: c.melhorenvio_service_id == 1)
        self.order.carrier_id = pac
        self.order.action_confirm()
        picking = self.order.picking_ids[0]
        picking.melhorenvio_order_id = CART_ORDER["id"]
        with patch(REQUESTS, return_value=FakeResponse(TRACKING)):
            pac.melhorenvio_tracking_state_update(picking)
        self.assertEqual(picking.carrier_tracking_ref, "BR267566353956A")
        self.assertEqual(picking.delivery_state, "customer_delivered")

    def test_cancel_shipment(self):
        pac = self._sync().filtered(lambda c: c.melhorenvio_service_id == 1)
        self.order.carrier_id = pac
        self.order.action_confirm()
        picking = self.order.picking_ids[0]
        picking.melhorenvio_order_id = CART_ORDER["id"]
        picking.carrier_tracking_ref = "BR267566353956A"
        with patch(REQUESTS, return_value=FakeResponse({})) as request:
            pac.melhorenvio_cancel_shipment(picking)
        self.assertFalse(picking.melhorenvio_order_id)
        self.assertFalse(picking.carrier_tracking_ref)
        self.assertEqual(
            request.call_args.kwargs["json"]["order"]["id"], CART_ORDER["id"]
        )
