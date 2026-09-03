from odoo import models


class BiotexPurchaseRequest(models.Model):
    _inherit = 'biotex.purchase.request'

    def _biotex_on_cancel(self, reason):
        """R11: cancelar la solicitud cierra las solicitudes de pago asociadas."""
        res = super()._biotex_on_cancel(reason)
        payments = self.purchase_order_ids.biotex_payment_request_ids.filtered(lambda p: p.state in ('draft', 'requested', 'approved'))
        payments.action_cancel('Solicitud de compra cancelada: %s' % reason)
        return res
