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
            
            if hasattr(self, 'line_ids') and self.line_ids:
                self.line_ids.filtered(
                    lambda l: l.display_type == 'payment_term' 
                    and l.account_id.account_type == 'liability_payable'
                    and not l.reconciled
                )
                
    def action_create_payments(self):
        """Override para validar que pelo menos uma parcela foi selecionada e o valor está correto"""
        self.ensure_one()
        
        if not self.parcels_ids:
            raise UserError(_('É necessário selecionar pelo menos uma parcela para criar o pagamento.'))
        
        max_amount = sum(abs(line.amount_residual) for line in self.parcels_ids)
        if self.amount > max_amount:
            raise UserError(
                _('O valor do pagamento (%.2f) não pode ser maior que o total das parcelas selecionadas (%.2f).') 
                % (self.amount, max_amount)
            )
        
        return super().action_create_payments()
    
    def action_generate_parcels_payments(self):
        """Gera pagamentos separados para cada parcela selecionada"""
        self.ensure_one()
        
        if not self.parcels_ids:
            raise UserError(_('É necessário selecionar pelo menos uma parcela para gerar os pagamentos.'))
        
        if not self.journal_id:
            raise UserError(_('É necessário selecionar um diário para criar os pagamentos.'))
        
        if not self.payment_date:
            raise UserError(_('É necessário informar a data do pagamento.'))
        
        payments = self.env['account.payment']
        
        for idx, parcel_line in enumerate(self.parcels_ids, 1):
            parcel_amount = abs(parcel_line.amount_residual)
            move = parcel_line.move_id
            
            payment_vals = {
                'payment_type': 'outbound',
                'partner_type': 'supplier',
                'partner_id': move.partner_id.id if move.partner_id else False,
                'amount': parcel_amount,
                'currency_id': self.currency_id.id,
                'journal_id': self.journal_id.id,
                'company_id': self.company_id.id,
                'date': parcel_line.date_maturity,
                'payment_reference': parcel_line.move_id.name or '',
                'memo': self.communication,
                'state': 'in_process',
            }
            
            if move.partner_bank_id:
                payment_vals['partner_bank_id'] = move.partner_bank_id.id
            
            if self.payment_method_line_id:
                payment_vals['payment_method_line_id'] = self.payment_method_line_id.id
            
            payment = self.env['account.payment'].create(payment_vals)
            payment.action_post()
            
            if parcel_line:
                payment_lines = payment.move_id.line_ids.filtered(
                    lambda l: l.account_id.account_type == 'liability_payable'
                )
                if payment_lines and parcel_line:
                    (payment_lines | parcel_line).reconcile()
                    payment.state = 'in_process'
                    
                    move = parcel_line.move_id
                    if move and payment not in move.matched_payment_ids:
                        move.matched_payment_ids = [(4, payment.id)]
            payments |= payment
            
        if payments:
            return {
                'name': _('Pagamentos Gerados'),
                'type': 'ir.actions.act_window',
                'res_model': 'account.payment',
                'view_mode': 'list,form',
                'domain': [('id', 'in', payments.ids)],
                'context': {'create': False},
            }
        else:
            return {'type': 'ir.actions.act_window_close'}