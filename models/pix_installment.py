# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
import json
import logging

_logger = logging.getLogger(__name__)


class PixInstallment(models.Model):
    _name = 'pix.installment'
    _description = 'Parcela PIX'
    _inherit = 'mail.thread'
    _order = 'due_date, id'
    _check_company_auto = True

    name = fields.Char(
        string='Nome',
        compute='_compute_name',
        store=True,
        readonly=True
    )
    invoice_id = fields.Many2one(
        'account.move',
        string='Fatura',
        required=True,
        ondelete='cascade',
        check_company=True,
        domain=[('move_type', 'in', ('in_invoice', 'in_refund', 'in_receipt'))]
    )
    payment_id = fields.Many2one(
        'account.payment',
        string='Pagamento',
        required=True,
        ondelete='restrict',
        check_company=True,
        readonly=True
    )
    amount = fields.Monetary(
        string='Valor',
        required=True,
        currency_field='currency_id'
    )
    currency_id = fields.Many2one(
        'res.currency',
        string='Moeda',
        compute='_compute_currency_id',
        store=True,
        readonly=True,
        required=True
    )
    
    @api.depends('invoice_id', 'invoice_id.currency_id', 'company_id')
    def _compute_currency_id(self):
        """Computa a moeda da fatura ou usa a moeda da empresa como fallback"""
        for record in self:
            if record.invoice_id and record.invoice_id.currency_id:
                record.currency_id = record.invoice_id.currency_id
            elif record.company_id:
                record.currency_id = record.company_id.currency_id
            else:
                record.currency_id = self.env.company.currency_id
    due_date = fields.Date(
        string='Data de Vencimento',
        required=True
    )
    pix_status = fields.Selection(
        [
            ('draft', 'Rascunho'),
            ('pending', 'Pendente'),
            ('paid', 'Pago'),
            ('failed', 'Falhou'),
        ],
        string='Status PIX',
        default='draft',
        tracking=True,
        required=True
    )
    pix_txid = fields.Char(
        string='TXID PIX',
        copy=False,
        help='Identificador único da transação PIX'
    )
    pix_payload = fields.Text(
        string='Payload PIX',
        help='JSON completo do payload enviado para a API'
    )
    pix_response = fields.Text(
        string='Resposta PIX',
        help='JSON completo da resposta da API'
    )
    last_sync = fields.Datetime(
        string='Última Sincronização',
        copy=False
    )
    pix_paid_date = fields.Datetime(
        string='Data de Confirmação do Pagamento',
        copy=False,
        help='Data e hora em que o PIX foi confirmado como pago pela API'
    )
    company_id = fields.Many2one(
        'res.company',
        string='Empresa',
        required=True,
        default=lambda self: self.env.company
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Fornecedor',
        related='invoice_id.partner_id',
        store=True,
        readonly=True
    )
    
    pix_paid_date_display = fields.Char(
        string='Data de Pagamento',
        compute='_compute_pix_paid_date_display',
        store=False,
        help='Data formatada de quando o PIX foi confirmado como pago'
    )
    
    # Campos relacionados para facilitar visualização
    invoice_name = fields.Char(
        related='invoice_id.name',
        string='Número da Fatura',
        store=True,
        readonly=True
    )
    payment_name = fields.Char(
        related='payment_id.name',
        string='Número do Pagamento',
        store=True,
        readonly=True
    )

    @api.depends('payment_id')
    def _compute_name(self):
        for record in self:
            if record.payment_id:
                record.name = f"PAG_{record.payment_id.id}"
            else:
                record.name = f"PAG_{record.id or ''}"
    
    @api.depends('pix_status', 'pix_paid_date')
    def _compute_pix_paid_date_display(self):
        """Computa a data formatada de pagamento"""
        for record in self:
            if record.pix_status == 'paid' and record.pix_paid_date:
                # Formata a data como "Pago em DD/MM/YYYY"
                paid_date = fields.Datetime.from_string(record.pix_paid_date)
                record.pix_paid_date_display = _('Pago em %s') % paid_date.strftime('%d/%m/%Y')
            else:
                record.pix_paid_date_display = ''

    @api.constrains('pix_status')
    def _check_delete_paid(self):
        """Impede deletar parcelas pagas"""
        for record in self:
            if record.pix_status == 'paid' and self.env.context.get('force_unlink'):
                # Permite apenas se forçado via contexto (para limpeza administrativa)
                continue
            # A proteção real é feita no método unlink

    def unlink(self):
        """Impede deletar parcelas pagas"""
        paid_installments = self.filtered(lambda i: i.pix_status == 'paid')
        if paid_installments and not self.env.context.get('force_unlink'):
            raise ValidationError(
                _('Não é possível deletar parcelas PIX pagas. Parcelas: %s') %
                ', '.join(paid_installments.mapped('name'))
            )
        return super().unlink()

    def action_send_pix(self):
        """Envia o PIX para a API Itaú
        
        Apenas monta payload, chama API, salva JSON completo e muda status para pending.
        Sem qualquer impacto contábil.
        """
        self.ensure_one()
        
        if self.pix_status in ('pending', 'paid'):
            raise UserError(
                _('Esta parcela já foi enviada. Status atual: %s') % self.pix_status
            )
        
        if not self.payment_id:
            raise UserError(_('A parcela deve estar vinculada a um pagamento.'))
        
        payment = self.payment_id
        
        # Validações e garante que o payment está postado
        # Estados válidos: posted, in_process (aguardando reconciliação), paid (já reconciliado)
        if payment.state == 'draft':
            # Tenta postar automaticamente se estiver em draft
            payment.action_post()
        elif payment.state not in ('posted', 'in_process', 'paid'):
            raise UserError(
                _('O pagamento deve estar postado antes de enviar o PIX. Estado atual: %s') % 
                payment.state
            )
        
        # Verifica se o move_id está postado
        if not payment.move_id or payment.move_id.state != 'posted':
            if payment.state in ('posted', 'in_process', 'paid') and payment.move_id and payment.move_id.state != 'posted':
                # Tenta postar o move se o payment estiver postado mas o move não
                payment.move_id._post(soft=False)
            else:
                raise UserError(
                    _('O lançamento contábil do pagamento deve estar postado. '
                      'Estado do pagamento: %s, Estado do lançamento: %s') %
                    (payment.state, payment.move_id.state if payment.move_id else 'N/A')
                )
        
        if not payment.company_id.itau_pix_api_id:
            raise UserError(
                _('É necessário configurar a API Itaú PIX na empresa %s.') % 
                payment.company_id.name
            )
        
        if not payment.partner_bank_id:
            raise UserError(
                _('É necessário configurar uma conta bancária do fornecedor no pagamento.')
            )
        
        try:
            # Monta o payload usando o método existente do account.payment
            payload = payment._build_pix_payload_from_payment()
            
            # Salva o payload antes de enviar
            self.pix_payload = json.dumps(payload, indent=2, ensure_ascii=False)
            
            # Envia via API
            base_payment_api = self.env['base.payment.api']
            pix_data = base_payment_api.send_pix(
                payload,
                payment_id=payment.id,
                move_line_id=None
            )
            
            # Atualiza campos do payment
            payment.write({
                'pix_txid': pix_data.get('txid') or payment.pix_txid,
                'pix_correlation_id': pix_data.get('correlation_id') or payment.pix_correlation_id,
                'pix_raw_response': pix_data.get('json_response_str', ''),
                'pix_status': 'pending',
                'pix_last_sync': fields.Datetime.now(),
            })
            
            # Salva resposta completa no installment
            self.pix_response = pix_data.get('json_response_str', '')
            self.pix_txid = pix_data.get('txid', '')
            self.pix_status = 'pending'
            self.last_sync = fields.Datetime.now()
            
            # Registra no chatter
            self.message_post(
                body=_('PIX enviado com sucesso para o Itaú. TXID: %s') % (self.pix_txid or 'N/A'),
                message_type='notification',
            )
            payment.message_post(
                body=_('PIX enviado via parcela %s. TXID: %s') % (self.name, self.pix_txid or 'N/A'),
                message_type='notification',
            )
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Sucesso'),
                    'message': _('PIX enviado com sucesso. TXID: %s') % (self.pix_txid or 'N/A'),
                    'type': 'success',
                    'sticky': False,
                }
            }
            
        except Exception as e:
            _logger.error(
                f'Erro ao enviar PIX para a parcela {self.id}: {e}',
                exc_info=True
            )
            
            self.pix_status = 'failed'
            self.last_sync = fields.Datetime.now()
            
            error_msg = str(e)
            if isinstance(e, (UserError, ValidationError)):
                error_msg = e.name if hasattr(e, 'name') else str(e)
            
            self.message_post(
                body=_('Erro ao enviar PIX: %s') % error_msg,
                message_type='notification',
            )
            
            if isinstance(e, (UserError, ValidationError)):
                raise
            raise UserError(_('Erro ao enviar o PIX: %s') % error_msg)

    def action_sync_pix_status(self):
        """Sincroniza o status do PIX com a API Itaú
        
        Quando a API retornar "pago":
        - Marca pix_status como paid
        - Cria lançamento contábil: débito conta transitória PIX, crédito banco
        - Vincula lançamento ao payment
        - Registra mensagem no chatter
        - NÃO desfaz reconciliação existente
        """
        self.ensure_one()
        
        if not self.payment_id:
            raise UserError(_('A parcela deve estar vinculada a um pagamento.'))
        
        if not self.payment_id.pix_txid:
            raise UserError(_('Este pagamento não possui um TXID PIX associado.'))
        
        payment = self.payment_id
        
        try:
            # Atualiza status via API usando o TXID
            base_payment_api = self.env['base.payment.api']
            api_return = base_payment_api.update_payment_pix_status(payment.pix_txid)
            api_status = api_return.get('data', {}).get('dados_pagamento', {}).get('status')
            
            if not api_status:
                self.message_post(
                    body=_('Status do PIX não encontrado na resposta da API'),
                    message_type='notification',
                )
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Aviso'),
                        'message': _('Status do PIX não encontrado na resposta da API'),
                        'type': 'warning',
                        'sticky': False,
                    }
                }
            
            status = api_status.lower()
            self.last_sync = fields.Datetime.now()
            self.pix_response = json.dumps(api_return, indent=2, ensure_ascii=False)
            
            # Atualiza apenas o estado PIX, nunca o estado contábil
            if status == 'efetuado':
                if self.pix_status == 'paid':
                    # Já está pago, não faz nada
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': _('Info'),
                            'message': _('PIX já estava marcado como pago'),
                            'type': 'info',
                            'sticky': False,
                        }
                    }
                
                # Marca como pago e registra a data de confirmação
                paid_datetime = fields.Datetime.now()
                self.write({
                    'pix_status': 'paid',
                    'pix_paid_date': paid_datetime,
                })
                payment.write({
                    'pix_status': 'paid',
                    'pix_last_sync': paid_datetime,
                })
                
                # Garante que o pagamento está postado
                if payment.state != 'posted':
                    payment.action_post()
                
                if not payment.move_id or payment.move_id.state != 'posted':
                    raise UserError(
                        _('O lançamento contábil do pagamento deve estar postado para criar a liquidação.')
                    )
                
                # Cria lançamento de liquidação: débito conta transitória PIX, crédito banco
                company = payment.company_id
                if not company.pix_transit_account_id:
                    raise UserError(
                        _('É necessário configurar a conta transitória PIX na empresa %s.') %
                        company.name
                    )
                
                if not payment.journal_id.default_account_id:
                    raise UserError(
                        _('O diário %s não possui conta padrão configurada.') %
                        payment.journal_id.name
                    )
                
                # Cria o lançamento de liquidação
                transit_account = company.pix_transit_account_id
                bank_account = payment.journal_id.default_account_id
                amount = abs(payment.amount)
                
                # Cria move de liquidação
                liquidation_move = self.env['account.move'].create({
                    'move_type': 'entry',
                    'date': fields.Date.today(),
                    'journal_id': payment.journal_id.id,
                    'company_id': company.id,
                    'ref': _('Liquidação PIX - %s') % payment.name,
                    'line_ids': [
                        (0, 0, {
                            'name': _('Liquidação PIX - %s') % payment.name,
                            'account_id': transit_account.id,
                            'debit': amount,
                            'credit': 0.0,
                            'partner_id': payment.partner_id.id,
                            'currency_id': payment.currency_id.id,
                        }),
                        (0, 0, {
                            'name': _('Liquidação PIX - %s') % payment.name,
                            'account_id': bank_account.id,
                            'debit': 0.0,
                            'credit': amount,
                            'partner_id': payment.partner_id.id,
                            'currency_id': payment.currency_id.id,
                        }),
                    ],
                })
                
                liquidation_move._post()
                
                # Vincula o lançamento ao payment (através de referência)
                payment.message_post(
                    body=_(
                        'PIX confirmado como pago pela API. '
                        'Lançamento de liquidação criado: %s'
                    ) % liquidation_move._get_html_link(),
                    message_type='notification',
                )
                
                self.message_post(
                    body=_(
                        'PIX confirmado como pago pela API. '
                        'Lançamento de liquidação: %s'
                    ) % liquidation_move._get_html_link(),
                    message_type='notification',
                )
                
                # NÃO mexe em reconciliação existente - ela já foi feita na criação do payment
                
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Sucesso'),
                        'message': _('PIX confirmado como pago. Lançamento de liquidação criado.'),
                        'type': 'success',
                        'sticky': False,
                    }
                }
                
            elif status == 'não efetuado':
                self.pix_status = 'failed'
                payment.write({
                    'pix_status': 'failed',
                    'pix_last_sync': fields.Datetime.now(),
                })
                self.message_post(
                    body=_('Pagamento PIX não efetuado pela API'),
                    message_type='notification',
                )
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Aviso'),
                        'message': _('Pagamento PIX não efetuado pela API'),
                        'type': 'warning',
                        'sticky': False,
                    }
                }
            else:
                # Status desconhecido, mantém como está
                self.message_post(
                    body=_('Status PIX retornado pela API: %s') % status,
                    message_type='notification',
                )
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Info'),
                        'message': _('Status PIX: %s') % status,
                        'type': 'info',
                        'sticky': False,
                    }
                }
                
        except Exception as e:
            _logger.error(
                f'Erro ao sincronizar status PIX para a parcela {self.id}: {e}',
                exc_info=True
            )
            
            error_msg = str(e)
            if isinstance(e, (UserError, ValidationError)):
                error_msg = e.name if hasattr(e, 'name') else str(e)
            
            self.message_post(
                body=_('Erro ao sincronizar status PIX: %s') % error_msg,
                message_type='notification',
            )
            
            if isinstance(e, (UserError, ValidationError)):
                raise
            raise UserError(_('Erro ao sincronizar status do PIX: %s') % error_msg)

