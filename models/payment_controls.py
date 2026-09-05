import math
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.addons.biotex_base.models.integrity import guard_create, guard_write, is_transition, lock_records, require_group, transition


class PaymentRequest(models.Model):
    _inherit = 'biotex.payment.request'

    amount_approved = fields.Monetary(string='Importe aprobado', tracking=True, copy=False)
    amount_executed = fields.Monetary(string='Dinero confirmado', compute='_compute_execution', store=True)
    amount_pending = fields.Monetary(string='Aprobado pendiente', compute='_compute_execution', store=True)
    amount_unapproved = fields.Monetary(string='Por autorizar', compute='_compute_execution', store=True)
    execution_status = fields.Selection([('none', 'Sin dinero confirmado'), ('partial', 'Pago parcial'),
                                         ('paid', 'Cubierto'), ('legacy', 'Antecedente por conciliar')],
                                        compute='_compute_execution', store=True)
    payment_ids = fields.One2many('account.payment', 'biotex_payment_request_id', string='Pagos', copy=False)
    cancel_reason = fields.Char(string='Motivo de cancelación', copy=False, readonly=False)

    @api.depends('amount', 'amount_approved', 'payment_ids.amount', 'payment_ids.state', 'state')
    def _compute_execution(self):
        for rec in self:
            rec.amount_executed = sum(rec.payment_ids.filtered(lambda p: p.state == 'paid').mapped('amount'))
            rec.amount_pending = max(rec.amount_approved - rec.amount_executed, 0)
            rec.amount_unapproved = max(rec.amount - rec.amount_approved, 0)
            rec.execution_status = ('legacy' if rec.state == 'paid' and not rec.payment_ids else
                                    'paid' if rec.amount_executed and rec.currency_id.compare_amounts(rec.amount_executed, rec.amount) >= 0 else
                                    'partial' if rec.amount_executed else 'none')

    @api.constrains('amount', 'amount_approved')
    def _check_amounts(self):
        for rec in self:
            if (not math.isfinite(rec.amount) or not math.isfinite(rec.amount_approved)
                    or rec.amount <= 0 or rec.amount_approved < 0
                    or rec.currency_id.compare_amounts(rec.amount_approved, rec.amount) > 0):
                raise ValidationError('Los importes deben ser finitos; lo aprobado debe estar entre cero y lo solicitado.')

    @api.model_create_multi
    def create(self, vals_list):
        guard_create(vals_list, ('approved_by_id', 'paid_by_id', 'amount_approved', 'payment_ids'))
        return super().create(vals_list)

    def write(self, vals):
        guard_write(self, vals, ('state', 'approved_by_id', 'paid_by_id', 'payment_ids'), ('amount', 'purchase_order_id'))
        if not is_transition(self) and 'amount_approved' in vals:
            require_group(self, 'biotex_base.group_biotex_payments')
            if any(r.state != 'requested' for r in self):
                raise UserError('Defina el importe aprobado antes de confirmar la autorización.')
        if not is_transition(self) and set(vals) & {'bank_account', 'proof', 'payment_reference', 'date_paid'}:
            require_group(self, 'biotex_base.group_biotex_payments')
            if any(r.state == 'paid' for r in self):
                raise UserError('Conserve los datos históricos del pago; adjunte una aclaración.')
        return super().write(vals)

    def unlink(self):
        if any(r.state != 'draft' or r.payment_ids for r in self):
            raise UserError('Conserve el historial de la solicitud de pago.')
        return super().unlink()

    def action_request(self):
        lock_records(self)
        for rec in self:
            if rec.state == 'requested':
                continue
            if rec.state != 'draft' or rec.purchase_order_id.state not in ('purchase', 'done'):
                raise UserError('Se requiere una compra confirmada y una solicitud en borrador.')
            transition(rec, {'state': 'requested', 'requested_by_id': self.env.uid, 'date_requested': fields.Datetime.now()})
        return True

    def action_approve(self):
        require_group(self, 'biotex_base.group_biotex_payments')
        lock_records(self.purchase_order_id)
        lock_records(self)
        for rec in self:
            if rec.state == 'approved':
                continue
            if rec.state != 'requested' or rec.amount_approved <= 0:
                raise UserError('Capture expresamente el importe que autoriza, total o parcial.')
            others = rec.purchase_order_id.biotex_payment_request_ids - rec
            committed = sum(max(p.amount_approved, p.amount_executed, p.amount if p.execution_status == 'legacy' else 0) for p in others if p.state in ('approved', 'paid'))
            if rec.currency_id.compare_amounts(committed + rec.amount_approved, rec.amount_order) > 0:
                raise UserError('Existen otras autorizaciones o pagos: el total excedería la compra.')
            transition(rec, {'state': 'approved', 'approved_by_id': self.env.uid})
        return True

    def action_open_payment(self):
        self.ensure_one()
        require_group(self, 'biotex_base.group_biotex_payments')
        if self.state != 'approved' or self.amount_pending <= 0:
            raise UserError('Se necesita autorización y saldo para registrar un pago.')
        return {'type': 'ir.actions.act_window', 'res_model': 'account.payment', 'view_mode': 'form',
                'context': {'default_biotex_payment_request_id': self.id, 'default_payment_type': 'outbound',
                            'default_partner_type': 'supplier', 'default_partner_id': self.partner_id.id,
                            'default_company_id': self.company_id.id, 'default_currency_id': self.currency_id.id,
                            'default_amount': self.amount_pending, 'default_memo': self.name}}

    def action_pay(self):
        require_group(self, 'biotex_base.group_biotex_payments')
        lock_records(self)
        for rec in self:
            if rec.state == 'paid' and rec.execution_status == 'paid':
                continue
            if rec.state != 'approved' or rec.execution_status != 'paid':
                raise UserError('Confirme el dinero desde Pagos. Un comprobante no sustituye al pago; el parcial permanece abierto.')
            paid = rec.payment_ids.filtered(lambda p: p.state == 'paid')
            transition(rec, {'state': 'paid', 'paid_by_id': self.env.uid, 'date_paid': max(paid.mapped('date'))})
        return True

    def action_cancel(self, reason=None):
        lock_records(self)
        for rec in self:
            text = reason or rec.cancel_reason
            if not text or not text.strip():
                raise UserError('Capture el motivo de cancelación.')
            if rec.payment_ids or rec.state == 'paid':
                raise UserError('Hay pagos relacionados; resuelva cada pago mediante su corrección propia.')
            transition(rec, {'state': 'cancelled', 'cancel_reason': text})

    def action_draft(self):
        lock_records(self)
        if any(r.payment_ids or r.approved_by_id or r.state == 'paid' for r in self):
            raise UserError('Una solicitud con autorización o dinero conserva su historial.')
        transition(self, {'state': 'draft'})


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    biotex_payment_request_id = fields.Many2one('biotex.payment.request', string='Solicitud de pago',
                                               index=True, copy=False, ondelete='restrict')
    biotex_proof = fields.Binary(string='Evidencia del dinero registrado', attachment=True, copy=False)

    @api.constrains('biotex_payment_request_id', 'amount', 'company_id', 'currency_id', 'partner_id', 'payment_type', 'state')
    def _check_biotex_request(self):
        for payment in self.filtered('biotex_payment_request_id'):
            request = payment.biotex_payment_request_id
            if (payment.company_id != request.company_id or payment.currency_id != request.currency_id
                    or payment.partner_id.commercial_partner_id != request.partner_id.commercial_partner_id
                    or payment.payment_type != 'outbound' or payment.partner_type != 'supplier'):
                raise ValidationError('El pago debe corresponder a la empresa, proveedor y moneda autorizados.')
            if payment.state in ('in_process', 'paid'):
                require_group(request, 'biotex_base.group_biotex_payments')
                lock_records(request)
                others = request.payment_ids.filtered(lambda p: p.state in ('in_process', 'paid'))
                if request.state not in ('approved', 'paid') or request.currency_id.compare_amounts(sum(others.mapped('amount')), request.amount_approved) > 0:
                    raise ValidationError('El dinero registrado excede la autorización disponible.')
                if not payment.date or not payment.payment_reference or not (payment.biotex_proof or request.proof):
                    raise ValidationError('Registre fecha, referencia y evidencia del pago real.')

    def write(self, vals):
        linked = self.filtered('biotex_payment_request_id')
        if set(vals) & {'biotex_payment_request_id', 'amount', 'company_id', 'currency_id', 'partner_id', 'partner_bank_id'}:
            if any(p.state != 'draft' for p in linked):
                raise UserError('El pago confirmado conserva importe, beneficiario y origen. Registre un reverso relacionado.')
        return super().write(vals)
