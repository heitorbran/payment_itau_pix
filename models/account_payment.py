# -*- coding: utf-8 -*-

import json
import re
import uuid
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
import logging

_logger = logging.getLogger(__name__)

class AccountPayment(models.Model):
    _inherit = 'account.payment'

    pix_txid = fields.Char(
        string='TXID PIX',
        copy=False,
        help='Identificador único da transação PIX (gerado automaticamente)'
    )
    pix_correlation_id = fields.Char(
        string='Correlation ID',
        copy=False,
        help='ID de correlação para rastreabilidade'
    )
    payment_pix_id = fields.Many2one(
        'payment.pix',
        string='Pagamento PIX',
        readonly=True,
        copy=False,
        help='Registro do pagamento PIX gerado'
    )

    def _generate_pix_txid(self):
        """Gera um TXID único para o pagamento PIX"""
        if not self.pix_txid:
            self.pix_txid = str(uuid.uuid4()).replace('-', '')[:25]
        return self.pix_txid

    def _generate_correlation_id(self):
        """Gera um Correlation ID único para o pagamento PIX"""
        if not self.pix_correlation_id:
            self.pix_correlation_id = str(uuid.uuid4())
        return self.pix_correlation_id

    def _sanitize_document(self, document):
        """Remove pontos, traços e barras de documentos (CPF/CNPJ)"""
        if not document:
            return ''
        return re.sub(r'[^\d]', '', document)

    def _format_amount(self, amount):
        """Formata valor como string com 2 casas decimais"""
        return f"{amount:.2f}"

    def _get_pagador_data(self):
        """Obtém dados do pagador (empresa)"""
        company = self.company_id
        if not company:
            raise UserError(_('É necessário selecionar uma empresa no pagamento.'))

        # Busca o diário do pagamento ou usa o primeiro diário bancário
        journal = self.journal_id
        if not journal or not journal.bank_account_id:
            journal = self.env['account.journal'].search([
                ('company_id', '=', company.id),
                ('type', '=', 'bank'),
                ('bank_account_id', '!=', False)
            ], limit=1)

        if not journal or not journal.bank_account_id:
            raise UserError(_('É necessário configurar uma conta bancária no diário bancário da empresa.'))

        bank_account = journal.bank_account_id
        company_partner = company.partner_id

        if not company_partner:
            raise UserError(_('A empresa não possui um parceiro configurado.'))

        tipo_conta = bank_account.bank_account_type or 'CC'

        agencia = bank_account.bank_agency_number or ''
        if agencia:
            agencia = agencia.lstrip('0') or '0'

        conta = bank_account.acc_number or ''
        if bank_account.bank_account_digit:
            conta = conta + bank_account.bank_account_digit
        conta = re.sub(r'[^\d]', '', conta)

        tipo_pessoa = 'J' if company_partner.is_company else 'F'

        documento = self._sanitize_document(company_partner.vat)
        if not documento:
            raise UserError(_('O CNPJ/CPF da empresa não está configurado.'))
        
        modulo_sispag = journal.sispag_modulo or 'Fornecedores'

        return {
            'tipo_conta': tipo_conta,
            'agencia': agencia,
            'conta': conta,
            'tipo_pessoa': tipo_pessoa,
            'documento': documento,
            'modulo_sispag': modulo_sispag,
        }

    def _get_recebedor_data(self, partner_bank_id):
        """Obtém dados do recebedor (fornecedor)"""
        if not partner_bank_id:
            raise UserError(_('É necessário configurar uma conta bancária do fornecedor no pagamento.'))

        bank_account = partner_bank_id
        partner = self.partner_id

        if not partner:
            raise UserError(_('É necessário selecionar um parceiro no pagamento.'))

        tipo_identificacao_conta = bank_account.bank_account_type or 'CC'

        agencia_recebedor = bank_account.bank_agency_number or ''
        if agencia_recebedor:
            agencia_recebedor = agencia_recebedor.lstrip('0') or '0'

        conta_recebedor = bank_account.acc_number or ''
        if bank_account.bank_account_digit:
            conta_recebedor = conta_recebedor + bank_account.bank_account_digit
        conta_recebedor = re.sub(r'[^\d]', '', conta_recebedor)
        
        tipo_identificacao_recebedor = 'J' if partner.is_company else 'F'

        identificacao_recebedor = self._sanitize_document(partner.vat)
        if not identificacao_recebedor:
            raise UserError(_('O CNPJ/CPF do fornecedor não está configurado.'))

        return {
            'tipo_identificacao_conta': tipo_identificacao_conta,
            'agencia_recebedor': agencia_recebedor,
            'conta_recebedor': conta_recebedor,
            'tipo_de_identificacao_do_recebedor': tipo_identificacao_recebedor,
            'identificacao_recebedor': identificacao_recebedor,
        }

    def _build_pix_payload_from_payment(self):
        """Constrói o payload PIX a partir do pagamento"""
        self.ensure_one()

        valor_pagamento = abs(self.amount)
        bank_account = self.partner_bank_id
        pagador_data = self._get_pagador_data()

        # Gera TXID e Correlation ID se não existirem
        self._generate_pix_txid()
        self._generate_correlation_id()

        data_pagamento = self.date.strftime('%Y-%m-%d') if self.date else fields.Date.today().strftime('%Y-%m-%d')
        informacoes_entre_usuarios = (self.memo or '')[:140] if self.memo else ''
        referencia_empresa = self.payment_reference or ''
        identificacao_comprovante = self.name or ''

        if bank_account.pix_payment_type == 'chave_pix':
            if not bank_account.pix_key:
                raise UserError(_('A chave PIX não está configurada na conta bancária do fornecedor.'))

            payload = {
                'valor_pagamento': self._format_amount(valor_pagamento),
                'data_pagamento': data_pagamento,
                'chave': bank_account.pix_key,
                'informacoes_entre_usuarios': informacoes_entre_usuarios,
                'referencia_empresa': referencia_empresa,
                'identificacao_comprovante': identificacao_comprovante,
                'pagador': pagador_data,
            }
        elif bank_account.pix_payment_type == 'dados_bancarios':
            if not bank_account.bank_id or not bank_account.bank_id.ispb:
                raise UserError(_('O ISPB do banco não está configurado na conta bancária do fornecedor.'))

            recebedor_data = self._get_recebedor_data(bank_account)

            payload = {
                'valor_pagamento': self._format_amount(valor_pagamento),
                'data_pagamento': data_pagamento,
                'ispb': bank_account.bank_id.ispb,
                'tipo_identificacao_conta': recebedor_data['tipo_identificacao_conta'],
                'agencia_recebedor': recebedor_data['agencia_recebedor'],
                'conta_recebedor': recebedor_data['conta_recebedor'],
                'tipo_de_identificacao_do_recebedor': recebedor_data['tipo_de_identificacao_do_recebedor'],
                'identificacao_recebedor': recebedor_data['identificacao_recebedor'],
                'informacoes_entre_usuarios': informacoes_entre_usuarios,
                'referencia_empresa': referencia_empresa,
                'identificacao_comprovante': identificacao_comprovante,
                'txid': self.pix_txid,
                'pagador': pagador_data,
            }
        else:
            raise UserError(_('Tipo de pagamento PIX não configurado na conta bancária do fornecedor.'))

        return payload

    def _send_pix_payment(self):
        """Envia o pagamento PIX via API Itaú"""
        self.ensure_one()

        if not self.company_id.itau_pix_api_id:
            raise UserError(
                _('É necessário configurar a API Itaú PIX na empresa %s.') % self.company_id.name
            )

        # Constrói o payload
        payload = self._build_pix_payload_from_payment()

        # Obtém a linha da fatura relacionada
        move_line = self.env['account.move.line'].search([('move_id', '=', self.move_id.id)], limit=1)
        move_line_id = move_line.id if move_line else None

        # Envia via API
        itau_api_pix = self.env['itau.api.pix']
        payment_pix = itau_api_pix.send_pix(
            payload,
            company_id=self.company_id.id,
            payment_id=self.id,
            move_line_id=move_line_id
        )

        # Vincula o pagamento PIX ao account.payment
        self.write({
            'pix_txid': payment_pix.txid or self.pix_txid,
            'pix_correlation_id': payment_pix.correlation_id or self.pix_correlation_id,
            'payment_pix_id': payment_pix.id,
        })

        return payment_pix

    def action_send_pix_itau(self):
        """Ação do botão para enviar PIX Itaú"""
        self.ensure_one()

        if self.payment_type != 'outbound':
            raise UserError(_('Esta funcionalidade é apenas para pagamentos de saída.'))

        if self.partner_type != 'supplier':
            raise UserError(_('Esta funcionalidade é apenas para pagamentos a fornecedores.'))

        if self.state != 'in_process':
            raise UserError(_('O pagamento deve estar em (Em processamento) para enviar o PIX.'))
        
        if not self.partner_bank_id:
            raise UserError(_('É necessário configurar uma conta bancária do fornecedor no pagamento.'))

        if self.payment_pix_id:
            raise UserError(
                _('Este pagamento já possui um PIX enviado. Verifique o registro de pagamento PIX relacionado.')
            )
        
        try:
            payment_pix = self._send_pix_payment()
            self.message_post(
                body=_('PIX enviado com sucesso para o Itaú. Registro PIX: %s') % payment_pix.name,
                message_type='notification',
            )
            self.state = 'in_process'
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('PIX enviado com sucesso para o Itaú'),
                    'message': _('Registro PIX: %s') % payment_pix.name,
                    'type': 'success',
                    'sticky': False,
                    'target': 'current',
                },
            }
        except Exception as e:
            _logger.error(
                f'Erro ao enviar PIX para o pagamento {self.id}: {e}',
                exc_info=True
            )
            self.state = 'draft'
            self.message_post(
                body=_('Erro ao enviar PIX: %s') % str(e),
                message_type='notification',
            )
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Erro ao enviar PIX'),
                    'message': _('Erro ao enviar PIX: %s') % str(e),
                    'type': 'danger',
                    'sticky': True,
                    'target': 'current',
                },
            }