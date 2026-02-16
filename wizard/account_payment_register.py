from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
import logging

_logger = logging.getLogger(__name__)

class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    parcels_ids = fields.Many2many('account.move.line', string='Parcelas', required=True)
    
    max_amount = fields.Monetary(
        string='Valor Máximo',
        currency_field='currency_id',
        compute='_compute_max_amount',
        readonly=True,
        help='Valor total das parcelas selecionadas'
    )
    
    @api.depends('parcels_ids', 'parcels_ids.amount_residual', 'currency_id')
    def _compute_max_amount(self):
        """Calcula o valor máximo baseado nas parcelas selecionadas"""
        for wizard in self:
            if wizard.parcels_ids:
                total = sum(abs(line.amount_residual) for line in wizard.parcels_ids)
                wizard.max_amount = total
            else:
                wizard.max_amount = 0.0
    
    @api.constrains('amount', 'parcels_ids')
    def _check_amount_limit(self):
        """Valida que o valor não excede o total das parcelas selecionadas"""
        for wizard in self:
            if wizard.parcels_ids and wizard.amount:
                max_amount = sum(abs(line.amount_residual) for line in wizard.parcels_ids)
                if wizard.amount > max_amount:
                    raise ValidationError(
                        _('O valor do pagamento (%.2f) não pode ser maior que o total das parcelas selecionadas (%.2f).') 
                        % (wizard.amount, max_amount)
                    )
    
    @api.depends('parcels_ids', 'parcels_ids.amount_residual')
    def _compute_amount(self):
        """Override: Calcula o amount baseado nas parcelas selecionadas"""
        super()._compute_amount()
        for wizard in self:
            if wizard.parcels_ids:
                total = sum(abs(line.amount_residual) for line in wizard.parcels_ids)
                wizard.amount = total
    
    @api.onchange('parcels_ids')
    def _onchange_parcels_ids(self):
        """Atualiza o valor do pagamento quando as parcelas são alteradas"""
        if self.parcels_ids:
            total = sum(abs(line.amount_residual) for line in self.parcels_ids)
            self.amount = total
                
    def action_create_payments(self):
        """Override para validar que pelo menos uma parcela foi selecionada e o valor está correto"""
        self.ensure_one()
        
        # Validação apenas se parcels_ids estiver sendo usado (contexto customizado)
        if hasattr(self, 'parcels_ids') and self.parcels_ids:
            max_amount = sum(abs(line.amount_residual) for line in self.parcels_ids)
            if self.amount > max_amount:
                raise UserError(
                    _('O valor do pagamento (%.2f) não pode ser maior que o total das parcelas selecionadas (%.2f).') 
                    % (self.amount, max_amount)
                )
        
        return super().action_create_payments()
    
    def action_generate_parcels_payments(self):
        self.ensure_one()

        if not self.parcels_ids:
            raise UserError(_('É necessário selecionar pelo menos uma parcela.'))

        payments = self.env['account.payment']

        for parcel_line in self.parcels_ids:
            move = parcel_line.move_id

            ctx = dict(self.env.context)
            ctx.update({
                'active_model': 'account.move',
                'active_ids': [move.id],
                'active_id': move.id,
            })

            wizard = self.env['account.payment.register'].with_context(ctx).create({
                'journal_id': self.journal_id.id,
                'payment_date': parcel_line.date_maturity or fields.Date.today(),
                'amount': abs(parcel_line.amount_residual),
                'communication': self.communication,
                'payment_method_line_id': self.payment_method_line_id.id,
            })

            result = wizard.action_create_payments()

            new_payments = self.env['account.payment'].search([
                ('create_uid', '=', self.env.uid),
                ('partner_id', '=', move.partner_id.id),
                ('amount', '=', abs(parcel_line.amount_residual)),
            ], order='id desc', limit=1)

            payments |= new_payments

        return {
            'name': _('Pagamentos Gerados'),
            'type': 'ir.actions.act_window',
            'res_model': 'account.payment',
            'view_mode': 'list,form',
            'domain': [('id', 'in', payments.ids)],
            'context': {'create': False},
        }
