# Copyright 2026 PopSolutions
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import logging
import re

import requests

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

MELHORENVIO_URL = "https://melhorenvio.com.br"
MELHORENVIO_SANDBOX_URL = "https://sandbox.melhorenvio.com.br"
MELHORENVIO_TIMEOUT = 30
# Some carriers refuse weightless volumes; Melhor Envio quotes fractions of
# kg without problem.
MINIMUM_WEIGHT_KG = 0.05

# Status of the Melhor Envio order -> delivery_state of the picking.
STATUS_TO_DELIVERY_STATE = {
    "pending": "shipping_recorded_in_carrier",
    "released": "shipping_recorded_in_carrier",
    "generated": "shipping_recorded_in_carrier",
    "posted": "in_transit",
    "delivered": "customer_delivered",
    "undelivered": "incident",
    "canceled": "canceled_shipment",
}


class DeliveryCarrier(models.Model):
    _inherit = "delivery.carrier"

    delivery_type = fields.Selection(
        selection_add=[("melhorenvio", "Melhor Envio")],
        ondelete={
            "melhorenvio": lambda records: records.write(
                {"delivery_type": "fixed", "fixed_price": 0}
            )
        },
    )

    melhorenvio_token = fields.Char(
        groups="base.group_system",
        help="Access token of the Melhor Envio account, from Painel > "
        "Integrações > Tokens de acesso (or the OAuth application).",
    )

    melhorenvio_service_id = fields.Integer(
        readonly=True,
        help="Identifier of the shipping service in Melhor Envio "
        "(e.g. 1 for Correios PAC). Filled by the service synchronization.",
    )

    melhorenvio_service_name = fields.Char(readonly=True)

    melhorenvio_company_name = fields.Char(
        string="Melhor Envio Carrier",
        readonly=True,
        help="Carrier that runs this service (e.g. Correios, Jadlog).",
    )

    melhorenvio_package_length = fields.Float(
        default=20.0, help="Length in cm of the box the order ships in."
    )

    melhorenvio_package_width = fields.Float(
        default=15.0, help="Width in cm of the box the order ships in."
    )

    melhorenvio_package_height = fields.Float(
        default=12.0, help="Height in cm of the box the order ships in."
    )

    melhorenvio_receipt = fields.Boolean(
        string="Acknowledgment of Receipt (AR)",
        help="Ship with the acknowledgment of receipt service of the carrier.",
    )

    melhorenvio_own_hand = fields.Boolean(
        string="Own Hand (MP)",
        help="Ship with the own hand (mãos próprias) service of the carrier.",
    )

    # ------------------------------------------------------------------
    # Melhor Envio API
    # ------------------------------------------------------------------

    def _melhorenvio_base_url(self):
        self.ensure_one()
        if self.prod_environment:
            return MELHORENVIO_URL
        return MELHORENVIO_SANDBOX_URL

    def _melhorenvio_user_agent(self):
        company = self.company_id or self.env.company
        return f"{company.name} ({company.email or ''})"

    def _melhorenvio_request(self, method, endpoint, payload=None):
        self.ensure_one()
        if not self.melhorenvio_token:
            raise UserError(
                _("Set the Melhor Envio token on the delivery method %s.") % self.name
            )
        try:
            response = requests.request(
                method,
                self._melhorenvio_base_url() + "/api/v2" + endpoint,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.melhorenvio_token}",
                    "User-Agent": self._melhorenvio_user_agent(),
                    "Accept": "application/json",
                },
                timeout=MELHORENVIO_TIMEOUT,
            )
        except requests.RequestException as error:
            _logger.warning("Melhor Envio: %s %s failed: %s", method, endpoint, error)
            raise UserError(
                _("Melhor Envio could not be reached, try again later.")
            ) from error
        try:
            result = response.json()
        except ValueError as error:
            raise UserError(_("Melhor Envio returned an unreadable answer.")) from error
        if response.status_code >= 400 or (
            isinstance(result, dict) and result.get("errors")
        ):
            message = (
                isinstance(result, dict)
                and (result.get("message") or result.get("error"))
                or response.reason
            )
            errors = isinstance(result, dict) and result.get("errors") or {}
            details = "\n".join(
                "- " + " ".join(messages) for messages in errors.values()
            )
            raise UserError(
                _("Melhor Envio refused the request: %s")
                % "\n".join(filter(None, [message, details]))
            )
        return result

    @staticmethod
    def _melhorenvio_clean_zip(value):
        return re.sub(r"\D", "", value or "")

    # ------------------------------------------------------------------
    # Service synchronization
    # ------------------------------------------------------------------

    def action_melhorenvio_sync_services(self):
        """Create or update one delivery method per service available in
        Melhor Envio, so the customer picks the service at the checkout."""
        self.ensure_one()
        services = self._melhorenvio_request("GET", "/me/shipment/services")
        if not services:
            raise UserError(_("Melhor Envio returned no shipping service."))
        for service in services:
            values = {
                "melhorenvio_service_id": service["id"],
                "melhorenvio_service_name": service["name"],
                "melhorenvio_company_name": service["company"]["name"],
            }
            carrier = self.search(
                [
                    ("delivery_type", "=", "melhorenvio"),
                    ("melhorenvio_service_id", "=", service["id"]),
                ],
                limit=1,
            )
            if carrier:
                carrier.write(values)
                continue
            self.create(
                {
                    **values,
                    "name": f"{service['company']['name']} {service['name']}",
                    "delivery_type": "melhorenvio",
                    "melhorenvio_token": self.melhorenvio_token,
                    "melhorenvio_package_length": self.melhorenvio_package_length,
                    "melhorenvio_package_width": self.melhorenvio_package_width,
                    "melhorenvio_package_height": self.melhorenvio_package_height,
                    "prod_environment": self.prod_environment,
                    "product_id": self.product_id.id,
                    "company_id": self.company_id.id,
                }
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Melhor Envio Services"),
            "res_model": "delivery.carrier",
            "view_mode": "list,form",
            "domain": [("delivery_type", "=", "melhorenvio")],
        }

    # ------------------------------------------------------------------
    # Quotation
    # ------------------------------------------------------------------

    def _melhorenvio_goods_lines(self, order):
        return order.order_line.filtered(
            lambda line: not line.display_type
            and not line.is_delivery
            and line.product_id.type != "service"
        )

    def _melhorenvio_quote_payload(self, order):
        self.ensure_one()
        lines = self._melhorenvio_goods_lines(order)
        weight = sum(line.product_id.weight * line.product_uom_qty for line in lines)
        value = sum(lines.mapped("price_total"))
        return {
            "from": {
                "postal_code": self._melhorenvio_clean_zip(
                    order.company_id.partner_id.zip
                )
            },
            "to": {
                "postal_code": self._melhorenvio_clean_zip(
                    order.partner_shipping_id.zip
                )
            },
            "products": [
                {
                    "id": "package",
                    "length": self.melhorenvio_package_length,
                    "width": self.melhorenvio_package_width,
                    "height": self.melhorenvio_package_height,
                    "weight": max(weight, MINIMUM_WEIGHT_KG),
                    "insurance_value": round(value, 2),
                    "quantity": 1,
                }
            ],
            "options": {
                "receipt": self.melhorenvio_receipt,
                "own_hand": self.melhorenvio_own_hand,
            },
            "services": str(self.melhorenvio_service_id),
        }

    def melhorenvio_rate_shipment(self, order):
        self.ensure_one()

        def error(message):
            return {
                "success": False,
                "price": 0.0,
                "error_message": message,
                "warning_message": False,
            }

        if not self.melhorenvio_service_id:
            return error(_("Synchronize the Melhor Envio services first."))
        payload = self._melhorenvio_quote_payload(order)
        if len(payload["to"]["postal_code"]) != 8:
            return error(_("Set the ZIP code of the delivery address."))
        try:
            result = self._melhorenvio_request(
                "POST", "/me/shipment/calculate", payload
            )
        except UserError as user_error:
            return error(str(user_error))
        # Filtered to a single service, the API answers the object itself
        # instead of a list.
        if isinstance(result, dict):
            result = [result]
        service = next(
            (item for item in result if item.get("id") == self.melhorenvio_service_id),
            None,
        )
        if not service or service.get("error"):
            message = (service or {}).get("error") or _(
                "Service unavailable for this address."
            )
            return error(message)
        delivery_time = int(service.get("delivery_time") or 0)
        return {
            "success": True,
            "price": float(service["price"]),
            "error_message": False,
            "warning_message": delivery_time
            and _("Estimated delivery: %s working day(s).") % delivery_time,
            "melhorenvio_delivery_time": delivery_time,
        }

    # ------------------------------------------------------------------
    # Shipping: buy, generate and print the label
    # ------------------------------------------------------------------

    def _melhorenvio_address(self, partner):
        return {
            "name": partner.name,
            "phone": partner.phone or partner.mobile or "",
            "email": partner.email or "",
            "document": self._melhorenvio_clean_zip(partner.vat)
            if partner.company_type != "company"
            else "",
            "company_document": self._melhorenvio_clean_zip(partner.vat)
            if partner.company_type == "company"
            else "",
            "address": partner.street or "",
            "number": getattr(partner, "street_number", "") or "S/N",
            "complement": partner.street2 or "",
            "district": getattr(partner, "district", "") or "",
            "city": partner.city or "",
            "state_abbr": partner.state_id.code or "",
            "country_id": "BR",
            "postal_code": self._melhorenvio_clean_zip(partner.zip),
        }

    def _melhorenvio_cart_payload(self, picking):
        self.ensure_one()
        order = picking.sale_id
        partner = picking.partner_id
        lines = order and self._melhorenvio_goods_lines(order)
        value = sum(lines.mapped("price_total")) if lines else 0.0
        weight = picking.shipping_weight or picking.weight
        invoice_key = picking._melhorenvio_invoice_key()
        options = {
            "insurance_value": round(value, 2),
            "receipt": self.melhorenvio_receipt,
            "own_hand": self.melhorenvio_own_hand,
            "reverse": False,
            # Without an NF-e key Melhor Envio ships with the declaração de
            # conteúdo (non commercial shipment).
            "non_commercial": not invoice_key,
        }
        if invoice_key:
            options["invoice"] = {"key": invoice_key}
        return {
            "service": self.melhorenvio_service_id,
            "from": self._melhorenvio_address(picking.company_id.partner_id),
            "to": self._melhorenvio_address(partner),
            "products": [
                {
                    "name": move.product_id.display_name,
                    "quantity": int(move.quantity) or 1,
                    "unitary_value": round(move.product_id.list_price, 2),
                }
                for move in picking.move_ids
            ],
            "volumes": [
                {
                    "length": self.melhorenvio_package_length,
                    "width": self.melhorenvio_package_width,
                    "height": self.melhorenvio_package_height,
                    "weight": max(weight, MINIMUM_WEIGHT_KG),
                }
            ],
            "options": options,
        }

    def melhorenvio_send_shipping(self, pickings):
        """Buy the shipment, generate the label and attach it to the picking:
        cart -> checkout (paid with the Melhor Envio wallet) -> generate ->
        print."""
        result = []
        for picking in pickings:
            order = self._melhorenvio_request(
                "POST", "/me/cart", self._melhorenvio_cart_payload(picking)
            )
            order_id = order["id"]
            picking.melhorenvio_order_id = order_id
            self._melhorenvio_request(
                "POST", "/me/shipment/checkout", {"orders": [order_id]}
            )
            self._melhorenvio_request(
                "POST", "/me/shipment/generate", {"orders": [order_id]}
            )
            self._melhorenvio_attach_label(picking)
            tracking = order.get("tracking") or order.get("protocol") or ""
            result.append(
                {
                    "exact_price": float(order.get("price") or 0.0),
                    "tracking_number": tracking,
                }
            )
        return result

    def _melhorenvio_attach_label(self, picking):
        self.ensure_one()
        printing = self._melhorenvio_request(
            "POST",
            "/me/shipment/print",
            {"mode": "private", "orders": [picking.melhorenvio_order_id]},
        )
        url = printing.get("url")
        if not url:
            return
        try:
            response = requests.get(url, timeout=MELHORENVIO_TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as error:
            _logger.warning("Melhor Envio: label download failed: %s", error)
            picking.message_post(body=_("The Melhor Envio label is at %s") % url)
            return
        self.env["ir.attachment"].create(
            {
                "name": f"etiqueta-melhorenvio-{picking.name}.pdf".replace("/", "-"),
                "datas": base64.b64encode(response.content),
                "res_model": picking._name,
                "res_id": picking.id,
                "mimetype": "application/pdf",
            }
        )

    def melhorenvio_get_tracking_link(self, picking):
        self.ensure_one()
        if not picking.melhorenvio_order_id:
            return False
        return (
            f"{self._melhorenvio_base_url()}/rastreio"
            f"/{picking.melhorenvio_order_id}"
        )

    def melhorenvio_cancel_shipment(self, pickings):
        for picking in pickings:
            if not picking.melhorenvio_order_id:
                continue
            self._melhorenvio_request(
                "POST",
                "/me/shipment/cancel",
                {
                    "order": {
                        "id": picking.melhorenvio_order_id,
                        "reason_id": "2",
                        "description": _("Cancelled from Odoo"),
                    }
                },
            )
            picking.write(
                {"melhorenvio_order_id": False, "carrier_tracking_ref": False}
            )

    # ------------------------------------------------------------------
    # Tracking (delivery_state)
    # ------------------------------------------------------------------

    def melhorenvio_tracking_state_update(self, picking):
        self.ensure_one()
        if not picking.melhorenvio_order_id:
            return
        result = self._melhorenvio_request(
            "POST",
            "/me/shipment/tracking",
            {"orders": [picking.melhorenvio_order_id]},
        )
        data = result.get(picking.melhorenvio_order_id)
        if not data:
            return
        if data.get("tracking") and not picking.carrier_tracking_ref:
            picking.carrier_tracking_ref = data["tracking"]
        status = data.get("status") or ""
        picking.tracking_state = status
        picking.delivery_state = STATUS_TO_DELIVERY_STATE.get(status, "in_transit")
