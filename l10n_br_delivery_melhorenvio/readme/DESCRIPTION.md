This module ships through [Melhor Envio](https://melhorenvio.com.br),
the Brazilian shipping broker:

- the checkout offers one delivery option per Melhor Envio service
  (Correios PAC, Sedex, Jadlog, Loggi and so on), each with its real
  price and estimated delivery time, so the customer picks the best one;
- validating the delivery order buys the shipment (paid with the Melhor
  Envio wallet), generates the transport label and attaches its PDF to
  the picking, ready to print and hand to the drop-off point;
- the access key of the authorized NF-e is sent with the shipment; a
  delivery without NF-e goes out as non commercial, with the declaração
  de conteúdo;
- the tracking events feed the delivery states of the `delivery_state`
  module, whose scheduled action keeps the pickings up to date and can
  notify the customer on delivery.
