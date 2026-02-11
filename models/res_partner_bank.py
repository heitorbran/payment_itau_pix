# -*- coding: utf-8 -*-

import re
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class ResPartnerBank(models.Model):
    _inherit = 'res.partner.bank'

    pix_key = fields.Char(
        string='Chave PIX',
        help='Chave PIX (CPF, Email, Telefone ou Aleatória)'
    )
    
    pix_key_type = fields.Selection(
        selection=[
            ('cpf', 'CPF'),
            ('email', 'Email'),
            ('phone', 'Telefone'),
            ('random', 'Aleatória'),
        ],
        string='Tipo da Chave PIX',
        help='Tipo da chave PIX cadastrada'
    )
    
    pix_payment_type = fields.Selection(
        selection=[
            ('chave_pix', 'Chave PIX'),
            ('dados_bancarios', 'Dados Bancários'),
        ],
        string='Tipo de Pagamento PIX',
        default='dados_bancarios',
        help='Define se o pagamento será feito por chave PIX ou dados bancários'
    )
    
    bank_account_type = fields.Selection(
        selection=[
            ('CC', 'Corrente'),
            ('CP', 'Pagamento'),
            ('PP', 'Poupança'),
        ],
        string='Tipo de Conta',
        help='Tipo de identificação da conta bancária'
    )

    @api.constrains('pix_key', 'pix_payment_type', 'pix_key_type')
    def _check_pix_key(self):
        """Valida a chave PIX conforme o tipo"""
        for record in self:
            if record.pix_payment_type == 'chave_pix':
                if not record.pix_key:
                    raise ValidationError(_('A chave PIX é obrigatória quando o tipo de pagamento é "Chave PIX".'))
                if not record.pix_key_type:
                    raise ValidationError(_('O tipo da chave PIX é obrigatório quando o tipo de pagamento é "Chave PIX".'))
                
                # Validação do formato conforme o tipo
                if record.pix_key_type == 'cpf':
                    # CPF: apenas números, 11 dígitos
                    cpf_clean = re.sub(r'\D', '', record.pix_key)
                    if len(cpf_clean) != 11:
                        raise ValidationError(_('CPF deve conter 11 dígitos numéricos.'))
                elif record.pix_key_type == 'email':
                    # Email: formato válido
                    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
                    if not re.match(email_pattern, record.pix_key):
                        raise ValidationError(_('Email inválido.'))
                elif record.pix_key_type == 'phone':
                    # Telefone: apenas números, 10 ou 11 dígitos
                    phone_clean = re.sub(r'\D', '', record.pix_key)
                    if len(phone_clean) not in [10, 11]:
                        raise ValidationError(_('Telefone deve conter 10 ou 11 dígitos numéricos.'))
                # Aleatória não precisa de validação específica

    @api.constrains('bank_id', 'pix_payment_type')
    def _check_ispb_for_bank_data(self):
        """Valida que ISPB está preenchido quando tipo é dados bancários"""
        for record in self:
            if record.pix_payment_type == 'dados_bancarios':
                if not record.bank_id:
                    raise ValidationError(_('O banco é obrigatório quando o tipo de pagamento é "Dados Bancários".'))
                if not record.bank_id.ispb:
                    raise ValidationError(_('O ISPB do banco é obrigatório quando o tipo de pagamento é "Dados Bancários".'))

