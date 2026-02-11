# -*- coding: utf-8 -*-
{
    'name': 'Payment Itaú PIX',
    'version': '1.0.0',
    'category': 'Accounting',
    'company': 'ILIOS SISTEMAS LTDA',
    'author': 'ILIOS SISTEMAS LTDA',
    'license': 'Other proprietary',
    'website': 'iliossistemas.com.br',
    'summary': 'Módulo para geração de dados PIX para pagamento via API Itaú',
    'description': """
Payment Itaú PIX
================

Este módulo adiciona funcionalidades para geração de dados PIX para pagamentos via API Itaú.

Funcionalidades:
* Campos PIX em contas bancárias (chave PIX ou dados bancários)
* Campo ISPB em bancos
* Geração de JSON para pagamento PIX (por chave ou por dados bancários)
* Botão na tela de pagamento para gerar dados PIX
* Wizard para exibir e copiar JSON formatado
* Validações de campos obrigatórios
    """,
    'depends': [
        'base',
        'account',
        'base_payment_api',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/res_bank_views.xml',
        'views/res_partner_bank_views.xml',
        'views/account_journal_views.xml',
        'views/account_move_views.xml',
        'views/base_payment_api.xml',
        'views/payment_pix.xml',
        'wizard/pix_payment_wizard_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}

