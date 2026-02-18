# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = 'res.company'

    itau_pix_api_id = fields.Many2one(
        'base.payment.api',
        string='API Itaú PIX',
        domain=[('integracao', '=', 'itau_pix'), ('active', '=', True)],
        help='Configuração da API de integração Itaú PIX para esta empresa'
    )
    pix_transit_account_id = fields.Many2one(
        'account.account',
        string='Conta Transitória PIX',
        domain=[('deprecated', '=', False)],
        check_company=True,
        help='Conta transitória utilizada para pagamentos PIX. '
             'Esta conta recebe o crédito quando o pagamento é gerado e '
             'é debitada quando o PIX é confirmado como pago.'
    )
    pix_journal_id = fields.Many2one(
        'account.journal',
        string='Diário PIX',
        domain=[('type', '=', 'bank')],
        check_company=True,
        help='Diário utilizado para lançamentos contábeis relacionados a PIX'
    )

    @api.constrains('itau_pix_api_id')
    def _check_itau_pix_api(self):
        """Valida que a API selecionada é do tipo Itaú PIX e pertence à mesma empresa"""
        for company in self:
            if company.itau_pix_api_id:
                if company.itau_pix_api_id.integracao != 'itau_pix':
                    raise ValidationError(
                        _('A API selecionada deve ser do tipo "Itaú PIX".')
                    )
                if company.itau_pix_api_id.company_id != company:
                    raise ValidationError(
                        _('A API selecionada deve pertencer à mesma empresa.')
                    )

