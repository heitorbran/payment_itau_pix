from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
import logging

_logger = logging.getLogger(__name__)

class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    parcels_ids = fields.Many2many('account.move.line', string='Parcelas', required=False)
    
    is_pix_payment_method = fields.Boolean(
        string='É Método PIX',
        compute='_compute_is_pix_payment_method',
        store=False,
        help='Indica se o método de pagamento selecionado é PIX'
    )
    
    @api.depends('payment_method_line_id', 'payment_method_line_id.name', 'payment_method_line_id.code', 'journal_id')
    def _compute_is_pix_payment_method(self):
        """Verifica se o método de pagamento é PIX
        
        Verifica se o diário configurado é o diário PIX da empresa ou
        se o nome/código do método contém PIX
        """
        for wizard in self:
            if wizard.payment_method_line_id and wizard.journal_id:
                method = wizard.payment_method_line_id
                company = wizard.company_id or wizard.journal_id.company_id
                
                # Verifica se o diário é o diário PIX configurado
                is_pix_journal = (
                    company and 
                    company.pix_journal_id and 
                    wizard.journal_id.id == company.pix_journal_id.id
                )
                
                # Verifica se o nome ou código do método contém PIX
                is_pix_method = (
                    'pix' in (method.name or '').lower() or
                    'pix' in (method.code or '').lower()
                )
                
                wizard.is_pix_payment_method = is_pix_journal or is_pix_method
            else:
                wizard.is_pix_payment_method = False
    
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
            
    def action_generate_pix_installments(self):
        """Gera parcelas PIX a partir das parcelas selecionadas no wizard"""
        self.ensure_one()
        
        if not self.parcels_ids:
            raise UserError(_('É necessário selecionar pelo menos uma parcela.'))
        
        if not self.is_pix_payment_method:
            raise UserError(_('Este método só está disponível para métodos de pagamento PIX.'))
        
        # Agrupa parcelas por invoice
        invoices = {}
        for line in self.parcels_ids:
            invoice = line.move_id
            if invoice.id not in invoices:
                invoices[invoice.id] = {
                    'invoice': invoice,
                    'lines': self.env['account.move.line']
                }
            invoices[invoice.id]['lines'] |= line
        
        installments = self.env['pix.installment']
        
        for invoice_data in invoices.values():
            invoice = invoice_data['invoice']
            
            # Valida se a invoice está postada
            if invoice.state != 'posted':
                raise UserError(
                    _('A fatura %s deve estar postada para gerar parcelas PIX.') % invoice.name
                )
            
            # Valida configuração PIX
            company = invoice.company_id
            if not company.pix_transit_account_id:
                raise UserError(
                    _('É necessário configurar a conta transitória PIX na empresa %s.') %
                    company.name
                )
            
            if not company.pix_journal_id:
                raise UserError(
                    _('É necessário configurar o diário PIX na empresa %s.') %
                    company.name
                )
            
            # Para cada linha selecionada, cria uma parcela PIX
            for line in invoice_data['lines']:
                amount = abs(line.amount_residual)
                if amount <= 0:
                    continue
                
                due_date = line.date_maturity or invoice.invoice_date_due or fields.Date.today()
                
                # Cria o payment
                payment = self.env['account.payment'].create({
                    'payment_type': 'outbound',
                    'partner_type': 'supplier',
                    'partner_id': invoice.partner_id.id,
                    'amount': amount,
                    'currency_id': invoice.currency_id.id,
                    'date': fields.Date.today(),
                    'journal_id': company.pix_journal_id.id,
                    'company_id': company.id,
                    'is_pix': True,
                    'payment_reference': _('Parcela PIX - %s') % invoice.name,
                    'memo': self.communication,
                })
                
                # Vincula invoice ao payment
                payment.invoice_ids = [(4, invoice.id)]
                
                # Posta o payment
                payment.action_post()
                
                # Verifica se foi postado corretamente
                payment.invalidate_recordset(['state', 'move_id'])
                if payment.state not in ('posted', 'in_process'):
                    raise UserError(
                        _('Erro ao postar o pagamento. Estado atual: %s') % payment.state
                    )
                if not payment.move_id or payment.move_id.state != 'posted':
                    raise UserError(
                        _('Erro ao postar o lançamento contábil do pagamento. '
                          'Estado do lançamento: %s') %
                        (payment.move_id.state if payment.move_id else 'N/A')
                    )
                
                # Cria a parcela
                installment = self.env['pix.installment'].create({
                    'invoice_id': invoice.id,
                    'payment_id': payment.id,
                    'amount': amount,
                    'due_date': due_date,
                    'pix_status': 'draft',
                    'company_id': company.id,
                    'currency_id': invoice.currency_id.id,  # Define explicitamente para evitar erro de campo obrigatório
                })
                
                # Vincula installment ao payment
                payment.write({
                    'pix_installment_id': installment.id,
                    'pix_status': 'draft',
                })
                
                installments |= installment
                
                # Reconciliação automática
                if payment.move_id and payment.move_id.state == 'posted':
                    payment_lines = payment.move_id.line_ids.filtered(
                        lambda l: l.account_id.account_type == 'liability_payable'
                                 and not l.reconciled
                                 and l.partner_id == payment.partner_id
                                 and l.parent_state == 'posted'
                    )
                    
                    invoice_lines = invoice.line_ids.filtered(
                        lambda l: l.account_id.account_type == 'liability_payable'
                                 and not l.reconciled
                                 and l.partner_id == payment.partner_id
                                 and l.parent_state == 'posted'
                                 and l.id == line.id  # Apenas a linha selecionada
                    )
                    
                    if payment_lines and invoice_lines:
                        for account in payment_lines.account_id:
                            payment_account_lines = payment_lines.filtered(
                                lambda l: l.account_id == account
                            )
                            invoice_account_lines = invoice_lines.filtered(
                                lambda l: l.account_id == account
                            )
                            
                            if payment_account_lines and invoice_account_lines:
                                to_reconcile = payment_account_lines | invoice_account_lines
                                
                                if to_reconcile:
                                    try:
                                        to_reconcile.filtered(
                                            lambda l: not l.reconciled and l.parent_state == 'posted'
                                        ).reconcile()
                                        
                                        invoice.matched_payment_ids |= payment
                                        
                                    except Exception as e:
                                        _logger.error(
                                            f'Erro ao reconciliar payment {payment.id} com invoice {invoice.id}: {e}',
                                            exc_info=True
                                        )
        
        if not installments:
            raise UserError(_('Não foi possível criar parcelas PIX. Verifique as parcelas selecionadas.'))
        
        # Invalida cache para atualizar residual
        for invoice_data in invoices.values():
            invoice_data['invoice'].invalidate_recordset(['amount_residual', 'payment_state'])
        
        return {
            'type': 'ir.actions.act_window',
            'name': _('Parcelas PIX Geradas'),
            'res_model': 'pix.installment',
            'view_mode': 'list,form',
            'domain': [('id', 'in', installments.ids)],
            'context': {'create': False},
        }
