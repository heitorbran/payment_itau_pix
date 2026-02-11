# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    sispag_modulo = fields.Selection(
        selection=[
            ('Fornecedores', 'Fornecedores'),
            ('Diversos', 'Diversos'),
        ],
        string='Módulo SISPAG',
        help='Módulo SISPAG utilizado para pagamentos PIX'
    )

