from odoo import api, fields, models


class BiotexPurchaseRequest(models.Model):
    _inherit = 'biotex.purchase.request'

    is_paid = fields.Boolean(compute='_compute_is_paid', store=True)

    @api.depends('purchase_order_ids.biotex_payment_state', 'purchase_order_ids.state')
    def _compute_is_paid(self):
        for rec in self:
            orders = rec.purchase_order_ids.filtered(lambda p: p.state in ('purchase', 'done'))
            rec.is_paid = bool(orders) and all(p.biotex_payment_state == 'paid' for p in orders)
