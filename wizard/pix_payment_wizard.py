# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError
import json
import logging

logger = logging.getLogger(__name__)

class PixPaymentWizard(models.TransientModel):
    _name = 'pix.payment.wizard'
    _description = 'Wizard para Exibir Dados PIX'

    move_id = fields.Many2one(
        'account.move',
        string='Fatura',
        required=True,
        readonly=True,
        help='Fatura para geração do PIX'
    )
    
    pix_json = fields.Text(
        string='JSON PIX',
        readonly=True,
        help='JSON formatado para pagamento PIX'
    )
    
    def action_send_pix(self):
        self.ensure_one()
        try:
            # Converte a string JSON para dicionário
            pix_data = json.loads(self.pix_json) if isinstance(self.pix_json, str) else self.pix_json
            
            itau_api_pix = self.env['itau.api.pix']
            result = itau_api_pix.send_pix(pix_data, self.move_id.company_id.id)
            # Posta a mensagem no chatter da fatura (account.move)
            self.move_id.message_post(
                body=_('PIX enviado com sucesso para o Itau.'),
                message_type='notification',
            )
            logger.info(f'Mensagem postada no chatter da fatura {self.move_id.id}')
            
            return {'type': 'ir.actions.act_window_close'}
        except json.JSONDecodeError as e:
            raise UserError(_('Erro ao processar JSON: %s') % str(e))
        except Exception as e:
            raise UserError(_('Erro ao enviar PIX: %s') % str(e))