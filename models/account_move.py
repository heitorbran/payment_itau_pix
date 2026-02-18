# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare
import logging

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = 'account.move'

    pix_installment_ids = fields.One2many(
        'pix.installment',
        'invoice_id',
        string='Parcelas PIX',
        help='Parcelas PIX relacionadas a esta fatura'
    )
    pix_installments_count = fields.Integer(
        string='Número de Parcelas PIX',
        compute='_compute_pix_installments_count',
        store=False
    )

    @api.depends('pix_installment_ids')
    def _compute_pix_installments_count(self):
        for record in self:
            record.pix_installments_count = len(record.pix_installment_ids)

    @api.depends('amount_residual', 'move_type', 'state', 'company_id', 'matched_payment_ids.state', 'pix_installment_ids.pix_status')
    def _compute_payment_state(self):
        """Override para impedir que faturas com parcelas PIX não confirmadas sejam marcadas como 'paid'
        
        A fatura só deve ser marcada como 'paid' quando todas as parcelas PIX estiverem confirmadas como pagas.
        Isso evita que a fatura seja marcada como paga apenas pela reconciliação, antes do PIX ser confirmado.
        """
        # Chama o método original do Odoo
        super()._compute_payment_state()
        
        # Para faturas com parcelas PIX, verifica se todas estão confirmadas
        for invoice in self.filtered(lambda m: m.pix_installment_ids):
            # Se a fatura foi marcada como 'paid' pelo método original
            if invoice.payment_state == 'paid':
                # Verifica se todas as parcelas PIX estão confirmadas como pagas
                installments = invoice.pix_installment_ids
                if installments:
                    # Se há parcelas PIX, verifica se todas estão pagas
                    all_paid = all(inst.pix_status == 'paid' for inst in installments)
                    if not all_paid:
                        # Se nem todas estão pagas, mantém como 'partial' ou 'in_payment'
                        # dependendo do residual
                        if invoice.amount_residual == 0:
                            # Se o residual é zero mas PIX não confirmado, marca como 'in_payment'
                            invoice.payment_state = 'in_payment'
                        else:
                            # Se ainda tem residual, mantém como 'partial'
                            invoice.payment_state = 'partial'

    def action_generate_pix_installments(self):
        """Gera parcelas PIX para a fatura postada
        
        Valida invoice postada, cria parcelas baseadas nas linhas da invoice,
        cria payments com is_pix=True, posta payments e executa reconciliação automática.
        """
        self.ensure_one()
        
        # Validações
        if not self.is_invoice():
            raise UserError(_('Esta funcionalidade é apenas para faturas.'))
        
        if self.move_type not in ('in_invoice', 'in_refund', 'in_receipt'):
            raise UserError(_('Esta funcionalidade é apenas para faturas de fornecedor.'))
        
        if self.state != 'posted':
            raise UserError(_('A fatura deve estar postada para gerar parcelas PIX.'))
        
        if self.pix_installment_ids:
            raise UserError(_('Esta fatura já possui parcelas PIX geradas.'))
        
        company = self.company_id
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
        
        if not company.itau_pix_api_id:
            raise UserError(
                _('É necessário configurar a API Itaú PIX na empresa %s.') %
                company.name
            )
        
        # Busca linhas da fatura que são contas a pagar (payable)
        payable_lines = self.line_ids.filtered(
            lambda l: l.account_id.account_type == 'liability_payable'
                     and not l.reconciled
                     and l.partner_id == self.partner_id
        )
        
        if not payable_lines:
            raise UserError(_('Não foram encontradas linhas de contas a pagar não reconciliadas nesta fatura.'))
        
        # Cria parcelas baseadas nas linhas payable
        # Por padrão, cria uma parcela por linha, mas pode ser customizado
        installments = self.env['pix.installment']
        payments = self.env['account.payment']
        
        for line in payable_lines:
            amount = abs(line.amount_residual)
            if amount <= 0:
                continue
            
            due_date = line.date_maturity or self.invoice_date_due or fields.Date.today()
            
            # Cria o payment
            payment = self.env['account.payment'].create({
                'payment_type': 'outbound',
                'partner_type': 'supplier',
                'partner_id': self.partner_id.id,
                'amount': amount,
                'currency_id': self.currency_id.id,
                'date': fields.Date.today(),
                'journal_id': company.pix_journal_id.id,
                'company_id': company.id,
                'is_pix': True,
                'payment_reference': _('Parcela PIX - %s') % self.name,
                'memo': self.communication,
            })
            
            # Vincula invoice ao payment
            payment.invoice_ids = [(4, self.id)]
            
            # Posta o payment
            payment.action_post()
            
            # Verifica se foi postado corretamente
            # in_process é um estado válido (payment postado mas aguardando reconciliação)
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
                'invoice_id': self.id,
                'payment_id': payment.id,
                'amount': amount,
                'due_date': due_date,
                'pix_status': 'draft',
                'company_id': company.id,
                'currency_id': self.currency_id.id,  # Define explicitamente para evitar erro de campo obrigatório
            })
            
            # Vincula installment ao payment
            payment.write({
                'pix_installment_id': installment.id,
                'pix_status': 'draft',
            })
            
            installments |= installment
            payments |= payment
        
        if not installments:
            raise UserError(_('Não foi possível criar parcelas PIX. Verifique as linhas da fatura.'))
        
        # Reconciliação automática
        # Busca as linhas do payment que devem ser reconciliadas
        for payment in payments:
            if not payment.move_id or payment.move_id.state != 'posted':
                continue
            
            # Linha do payment (destination - contas a pagar)
            # O payment cria: Débito Contas a Pagar, Crédito Conta Transitória PIX
            payment_lines = payment.move_id.line_ids.filtered(
                lambda l: l.account_id.account_type == 'liability_payable'
                         and not l.reconciled
                         and l.partner_id == payment.partner_id
                         and l.parent_state == 'posted'
            )
            
            # Linhas da invoice (payable)
            invoice_lines = self.line_ids.filtered(
                lambda l: l.account_id.account_type == 'liability_payable'
                         and not l.reconciled
                         and l.partner_id == payment.partner_id
                         and l.parent_state == 'posted'
            )
            
            # Reconcilia usando o método padrão do Odoo
            if payment_lines and invoice_lines:
                # Filtra linhas por conta e partner
                for account in payment_lines.account_id:
                    payment_account_lines = payment_lines.filtered(
                        lambda l: l.account_id == account
                    )
                    invoice_account_lines = invoice_lines.filtered(
                        lambda l: l.account_id == account
                    )
                    
                    if payment_account_lines and invoice_account_lines:
                        # Reconcilia as linhas correspondentes
                        to_reconcile = payment_account_lines | invoice_account_lines
                        
                        if to_reconcile:
                            try:
                                to_reconcile.filtered(
                                    lambda l: not l.reconciled and l.parent_state == 'posted'
                                ).reconcile()
                                
                                # Vincula payment à invoice
                                self.matched_payment_ids |= payment
                                
                            except Exception as e:
                                _logger.error(
                                    f'Erro ao reconciliar payment {payment.id} com invoice {self.id}: {e}',
                                    exc_info=True
                                )
                                # Não falha completamente, apenas loga o erro
                                self.message_post(
                                    body=_('Aviso: Erro ao reconciliar automaticamente o pagamento %s: %s') %
                                    (payment.name, str(e)),
                                    message_type='notification',
                                )
        
        # Invalida cache para atualizar residual
        self.invalidate_recordset(['amount_residual', 'payment_state'])
        
        # Mensagem de sucesso
        self.message_post(
            body=_('Parcelas PIX geradas com sucesso: %d parcela(s) criada(s).') % len(installments),
            message_type='notification',
        )
        
        return {
            'type': 'ir.actions.act_window',
            'name': _('Parcelas PIX Geradas'),
            'res_model': 'pix.installment',
            'view_mode': 'list,form',
            'domain': [('id', 'in', installments.ids)],
            'context': {'default_invoice_id': self.id},
        }

