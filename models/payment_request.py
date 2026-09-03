from odoo import api, fields, models
from odoo.exceptions import UserError


class BiotexPaymentRequest(models.Model):
    """Solicitud de pago ligada a la OC (R18). Sustituye el archivo aparte; cola para administración."""
    _name = 'biotex.payment.request'
    _description = 'Solicitud de pago'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'priority desc, date_needed asc, id desc'

    name = fields.Char(string='Folio', default='Nuevo', readonly=True, copy=False)
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('requested', 'Solicitada'),
        ('approved', 'Aprobada'),
        ('paid', 'Pagada'),
        ('cancelled', 'Cancelada'),
    ], default='draft', tracking=True, index=True)
    priority = fields.Selection([('0', 'Normal'), ('1', 'Urgente')], default='0', tracking=True)
    purchase_order_id = fields.Many2one('purchase.order', string='Orden de compra', required=True, index=True,
                                        domain=[('state', 'in', ('draft', 'sent', 'purchase', 'done'))])
    partner_id = fields.Many2one('res.partner', related='purchase_order_id.partner_id', store=True, string='Proveedor')
    company_id = fields.Many2one('res.company', related='purchase_order_id.company_id', store=True, string='Razón social que paga')
    currency_id = fields.Many2one(related='purchase_order_id.currency_id')
    warehouse_id = fields.Many2one(related='purchase_order_id.biotex_warehouse_id', store=True, string='Delegación destino')
    request_id = fields.Many2one(related='purchase_order_id.biotex_request_id', store=True, string='Solicitud de compra')
    contract_id = fields.Many2one(related='purchase_order_id.biotex_contract_id', store=True, string='Contrato al que se carga')
    amount = fields.Monetary(string='Monto a pagar', required=True)
    amount_order = fields.Monetary(related='purchase_order_id.amount_total', string='Total de la OC')
    payment_condition = fields.Selection(related='partner_id.biotex_payment_condition')
    requested_by_id = fields.Many2one('res.users', string='Solicitó', default=lambda self: self.env.user, readonly=True)
    date_requested = fields.Datetime(default=fields.Datetime.now, readonly=True)
    date_needed = fields.Date(string='Pagar antes de', help='Fecha límite para que el proveedor surta a tiempo.')
    approved_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    paid_by_id = fields.Many2one('res.users', string='Pagó', readonly=True, copy=False)
    date_paid = fields.Date(string='Fecha de pago', copy=False)
    payment_reference = fields.Char(string='Referencia / folio de transferencia', copy=False)
    bank_account = fields.Char(string='Cuenta / CLABE del proveedor')
    proof = fields.Binary(string='Comprobante', attachment=True, copy=False)
    proof_filename = fields.Char()
    notes = fields.Text()
    cancel_reason = fields.Char(readonly=True, copy=False)
    days_waiting = fields.Integer(compute='_compute_days_waiting', string='Días en espera')

    @api.depends('date_requested', 'state', 'date_paid')
    def _compute_days_waiting(self):
        now = fields.Datetime.now()
        for r in self:
            if r.state in ('paid', 'cancelled') or not r.date_requested:
                r.days_waiting = 0
            else:
                r.days_waiting = (now - r.date_requested).days

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'Nuevo') == 'Nuevo':
                vals['name'] = self.env['ir.sequence'].next_by_code('biotex.payment.request') or 'Nuevo'
            if not vals.get('amount') and vals.get('purchase_order_id'):
                vals['amount'] = self.env['purchase.order'].browse(vals['purchase_order_id']).amount_total
        return super().create(vals_list)

    @api.onchange('purchase_order_id')
    def _onchange_order(self):
        for r in self:
            r.amount = r.purchase_order_id.amount_total
            bank = r.purchase_order_id.partner_id.bank_ids[:1]
            r.bank_account = bank.acc_number if bank else False

    def action_request(self):
        for r in self:
            r.state = 'requested'
            group = self.env.ref('biotex_base.group_biotex_payments')
            partners = group.user_ids.mapped('partner_id')
            r.message_post(body='Solicitud de pago %s por %s %.2f a %s (OC %s, destino %s).' % (
                r.name, r.currency_id.symbol, r.amount, r.partner_id.name, r.purchase_order_id.name, r.warehouse_id.name or '-'),
                partner_ids=partners.ids, message_type='comment', subtype_xmlid='mail.mt_comment')
            r.purchase_order_id.message_post(body='Solicitud de pago %s enviada a administración.' % r.name)
        return True

    def action_approve(self):
        if not self.env.user.has_group('biotex_base.group_biotex_payments'):
            raise UserError('Solo Administración / Pagos aprueba solicitudes.')
        self.write({'state': 'approved', 'approved_by_id': self.env.uid})

    def action_pay(self):
        if not self.env.user.has_group('biotex_base.group_biotex_payments'):
            raise UserError('Solo Administración / Pagos registra el pago.')
        for r in self:
            if not r.proof and not r.payment_reference:
                raise UserError('Adjunte el comprobante o capture la referencia de la transferencia.')
            r.write({'state': 'paid', 'paid_by_id': self.env.uid, 'date_paid': r.date_paid or fields.Date.context_today(self)})
            r.purchase_order_id.message_post(
                body='PAGADO: %s %.2f el %s. Ref: %s. Envíe el comprobante al proveedor para que surta.' % (
                    r.currency_id.symbol, r.amount, r.date_paid, r.payment_reference or 'adjunto'),
                partner_ids=(r.requested_by_id.partner_id | r.purchase_order_id.user_id.partner_id).ids,
                message_type='comment', subtype_xmlid='mail.mt_comment')
            if r.request_id and not r.purchase_order_id.biotex_payment_request_ids.filtered(lambda p: p.state in ('draft', 'requested', 'approved')):
                r.request_id.is_paid = True
                r.request_id._biotex_sync_state()
        return True

    def action_cancel(self, reason='Cancelada'):
        for r in self:
            if r.state == 'paid':
                raise UserError('Una solicitud pagada no se cancela; registre una nota de crédito.')
            r.write({'state': 'cancelled', 'cancel_reason': reason})
            r.activity_ids.unlink()

    def action_draft(self):
        self.write({'state': 'draft', 'cancel_reason': False})

    def action_open_proof(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url', 'target': 'new',
                'url': '/web/content/%s/%s/proof/%s?download=true' % (self._name, self.id, self.proof_filename or 'comprobante')}
