# -*- coding: utf-8 -*-

import json
import re
import uuid
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = 'account.move'

    pix_txid = fields.Char(
        string='TXID PIX',
        help='Identificador único da transação PIX (gerado automaticamente)'
    )

    def _generate_pix_txid(self):
        """Gera um TXID único para o pagamento PIX"""
        if not self.pix_txid:
            # Gera UUID e remove hífens para criar um TXID alfanumérico
            self.pix_txid = str(uuid.uuid4()).replace('-', '')[:25]
        return self.pix_txid

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
        # Busca o diário da fatura ou usa o primeiro diário bancário
        journal = self.journal_id
        if not journal or not journal.bank_account_id:
            # Tenta buscar um diário bancário da empresa
            journal = self.env['account.journal'].search([
                ('company_id', '=', self.company_id.id),
                ('type', '=', 'bank'),
                ('bank_account_id', '!=', False)
            ], limit=1)
        
        if not journal or not journal.bank_account_id:
            raise UserError(_('É necessário configurar uma conta bancária no diário bancário da empresa.'))
        
        bank_account = journal.bank_account_id
        company = self.company_id
        
        if not company.partner_id:
            raise UserError(_('A empresa não possui um parceiro configurado.'))
        
        # Tipo de conta do pagador
        tipo_conta = bank_account.bank_account_type or 'CC'
        
        # Agência (remover zeros à esquerda)
        agencia = bank_account.bank_agency_number or ''
        if agencia:
            agencia = agencia.lstrip('0') or '0'
        
        # Conta (concatenar número + dígito, remover traços/pontos)
        conta = bank_account.acc_number or ''
        if bank_account.bank_account_digit:
            conta = conta + bank_account.bank_account_digit
        conta = re.sub(r'[^\d]', '', conta)
        
        # Tipo de pessoa
        tipo_pessoa = 'J' if company.partner_id.is_company else 'F'
        
        # Documento (apenas números)
        documento = self._sanitize_document(company.partner_id.vat)
        if not documento:
            raise UserError(_('O CNPJ/CPF da empresa não está configurado.'))
        
        # Módulo SISPAG
        modulo_sispag = journal.sispag_modulo or 'Fornecedores'
        
        return {
            'tipo_conta': tipo_conta,
            'agencia': agencia,
            'conta': conta,
            'tipo_pessoa': tipo_pessoa,
            'documento': documento,
            'modulo_sispag': modulo_sispag,
        }

    def _get_recebedor_data(self):
        """Obtém dados do recebedor (fornecedor)"""
        if not self.partner_bank_id:
            raise UserError(_('É necessário configurar uma conta bancária do fornecedor na fatura.'))
        
        bank_account = self.partner_bank_id
        partner = self.partner_id
        
        if not partner:
            raise UserError(_('É necessário selecionar um parceiro.'))
        
        # Tipo de identificação da conta
        tipo_identificacao_conta = bank_account.bank_account_type or 'CC'
        
        # Agência recebedor (remover zeros à esquerda)
        agencia_recebedor = bank_account.bank_agency_number or ''
        if agencia_recebedor:
            agencia_recebedor = agencia_recebedor.lstrip('0') or '0'
        
        # Conta recebedor (concatenar número + dígito, remover traços/pontos)
        conta_recebedor = bank_account.acc_number or ''
        if bank_account.bank_account_digit:
            conta_recebedor = conta_recebedor + bank_account.bank_account_digit
        conta_recebedor = re.sub(r'[^\d]', '', conta_recebedor)
        
        # Tipo de identificação do recebedor
        tipo_identificacao_recebedor = 'J' if partner.is_company else 'F'
        
        # Identificação do recebedor (apenas números)
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

    def _generate_pix_json(self):
        """Gera JSON para pagamento PIX conforme o tipo selecionado"""
        self.ensure_one()
        
        if self.move_type not in ('in_invoice', 'in_refund'):
            raise UserError(_('Esta funcionalidade é apenas para faturas de fornecedor.'))
        
        if not self.partner_bank_id:
            raise UserError(_('É necessário configurar uma conta bancária do fornecedor na fatura.'))
        
        bank_account = self.partner_bank_id
        
        if bank_account.pix_payment_type == 'chave_pix':
            return self._generate_pix_json_by_key(bank_account)
        elif bank_account.pix_payment_type == 'dados_bancarios':
            return self._generate_pix_json_by_bank_data(bank_account)
        else:
            raise UserError(_('Tipo de pagamento PIX não configurado na conta bancária do fornecedor.'))

    def _generate_pix_json_by_key(self, bank_account):
        """Gera JSON para pagamento por chave PIX"""
        if not bank_account.pix_key:
            raise UserError(_('A chave PIX não está configurada na conta bancária do fornecedor.'))
        
        # Dados básicos
        json_data = {
            'valor_pagamento': self._format_amount(abs(self.amount_residual)),
            'data_pagamento': self.date.strftime('%Y-%m-%d'),
            'chave': bank_account.pix_key,
            'informacoes_entre_usuarios': (self.narration or '')[:140] if self.narration else '',  # Truncar 140 caracteres
            'referencia_empresa': self.name or '',
            'identificacao_comprovante': self.name or '',
            'pagador': self._get_pagador_data(),
        }
        
        return json.dumps(json_data, indent=2, ensure_ascii=False)

    def _generate_pix_json_by_bank_data(self, bank_account):
        """Gera JSON para pagamento por dados bancários"""
        if not bank_account.bank_id or not bank_account.bank_id.ispb:
            raise UserError(_('O ISPB do banco não está configurado na conta bancária do fornecedor.'))
        
        # Gera TXID se não existir
        self._generate_pix_txid()
        
        # Dados do recebedor
        recebedor_data = self._get_recebedor_data()
        
        # Dados básicos
        json_data = {
            'valor_pagamento': self._format_amount(abs(self.amount_residual)),
            'data_pagamento': self.date.strftime('%Y-%m-%d'),
            'ispb': bank_account.bank_id.ispb,
            'tipo_identificacao_conta': recebedor_data['tipo_identificacao_conta'],
            'agencia_recebedor': recebedor_data['agencia_recebedor'],
            'conta_recebedor': recebedor_data['conta_recebedor'],
            'tipo_de_identificacao_do_recebedor': recebedor_data['tipo_de_identificacao_do_recebedor'],
            'identificacao_recebedor': recebedor_data['identificacao_recebedor'],
            'informacoes_entre_usuarios': (self.narration or '')[:140] if self.narration else '',  # Truncar 140 caracteres
            'referencia_empresa': self.name or '',
            'identificacao_comprovante': self.name or '',
            'txid': self.pix_txid,
            'pagador': self._get_pagador_data(),
        }
        
        return json.dumps(json_data, indent=2, ensure_ascii=False)

    def action_generate_pix_payment(self):
        """Gera dados PIX a partir da fatura"""
        self.ensure_one()
        
        if self.move_type not in ('in_invoice', 'in_refund'):
            raise UserError(_('Esta funcionalidade é apenas para faturas de fornecedor.'))
        
        if not self.partner_bank_id:
            raise UserError(_('É necessário configurar uma conta bancária do fornecedor na fatura.'))
        
        # Gera o JSON PIX
        try:
            pix_json = self._generate_pix_json()
        except Exception as e:
            self.message_post(
                body=_('Erro ao gerar dados PIX: %s') % str(e),
                message_type='notification',
            )
            raise UserError(_('Erro ao gerar dados PIX: %s') % str(e))
        
        # Cria o wizard
        wizard = self.env['pix.payment.wizard'].create({
            'move_id': self.id,
            'pix_json': pix_json,
        })
        
        return {
            'name': _('Dados PIX para Pagamento'),
            'type': 'ir.actions.act_window',
            'res_model': 'pix.payment.wizard',
            'view_mode': 'form',
            'res_id': wizard.id,
            'target': 'new',
        }

