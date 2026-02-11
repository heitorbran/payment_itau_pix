# -*- coding: utf-8 -*-

from odoo import models, fields


class ResBank(models.Model):
    _inherit = 'res.bank'

    ispb = fields.Char(
        string='ISPB',
        size=8,
        help='Identificador do Sistema de Pagamentos Brasileiro (ISPB) do banco'
    )

