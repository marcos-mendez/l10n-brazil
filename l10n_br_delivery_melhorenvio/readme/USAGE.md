At the checkout the customer sees one option per published Melhor Envio
service, with the price and the estimated delivery time for their ZIP
code.

Validating the delivery order buys the shipment and attaches the
transport label PDF to the picking: print it, stick it on the package
with the *DANFE Simplificado - Etiqueta* of the NF-e (see the
`l10n_br_nfe` module) and hand the package at the drop-off point. The
tracking number fills in as soon as the carrier assigns it, and the
*Update delivery states* scheduled action follows the delivery.

Cancelling the shipment at the picking also cancels it at Melhor Envio.
