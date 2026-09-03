from odoo import api, fields, models


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    biotex_payment_request_ids = fields.One2many('biotex.payment.request', 'purchase_order_id', string='Solicitudes de pago')
    biotex_payment_state = fields.Selection([
        ('none', 'Sin solicitud'), ('pending', 'Pago pendiente'), ('paid', 'Pagada')],
        string='Pago', compute='_compute_biotex_payment_state', store=True)
    biotex_payment_count = fields.Integer(compute='_compute_biotex_payment_state')

    @api.depends('biotex_payment_request_ids.state', 'biotex_payment_request_ids.amount')
    def _compute_biotex_payment_state(self):
        for po in self:
            reqs = po.biotex_payment_request_ids.filtered(lambda r: r.state != 'cancelled')
            po.biotex_payment_count = len(po.biotex_payment_request_ids)
            if not reqs:
                po.biotex_payment_state = 'none'
            elif all(r.state == 'paid' for r in reqs):
                po.biotex_payment_state = 'paid'
            else:
                po.biotex_payment_state = 'pending'

    def _biotex_needs_payment_request(self):
        """Regla: contado requiere pago previo. Otros módulos pueden excluir casos (venta interna)."""
        self.ensure_one()
        return self.partner_id.biotex_payment_condition == 'cash' and self.biotex_payment_state == 'none'

    def button_confirm(self):
        res = super().button_confirm()
        for po in self:
            if po._biotex_needs_payment_request():
                po.action_biotex_create_payment_request(auto=True)
        return res

    def button_cancel(self):
        res = super().button_cancel()
        self.biotex_payment_request_ids.filtered(lambda r: r.state in ('draft', 'requested', 'approved')).action_cancel('OC cancelada')
        return res

    def action_biotex_create_payment_request(self, auto=False):
        created = self.env['biotex.payment.request']
        for po in self:
            req = self.env['biotex.payment.request'].create({
                'purchase_order_id': po.id,
                'amount': po.amount_total,
                'priority': '1' if po.biotex_request_id.priority == '1' else '0',
                'date_needed': po.date_planned.date() if po.date_planned else False,
            })
            req.action_request()
            created |= req
        if auto or len(created) != 1:
            return created
        return {'type': 'ir.actions.act_window', 'res_model': 'biotex.payment.request', 'res_id': created.id, 'view_mode': 'form'}

    def action_view_payment_requests(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': 'Solicitudes de pago', 'res_model': 'biotex.payment.request',
                'view_mode': 'list,form', 'domain': [('purchase_order_id', '=', self.id)],
                'context': {'default_purchase_order_id': self.id}}
