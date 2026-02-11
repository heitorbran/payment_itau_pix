from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
import requests
import logging

logger = logging.getLogger(__name__)

class ItauApiPix(models.Model):
  _name = 'itau.api.pix'
  _description = 'Itau API PIX'
  _inherit = 'mail.thread'
  
  @api.model
  def get_base_payment_api(self, company_id=None):
    """Busca e retorna os dados da API de integração Itau PIX"""
    if not company_id:
        company_id = self.env.company.id
    
    base_payment_api = self.env['base.payment.api'].search([
        ('integracao', '=', 'itau_pix'),
        ('company_id', '=', company_id),
        ('active', '=', True)
    ], limit=1)
    
    if not base_payment_api:
        company_name = self.env['res.company'].browse(company_id).name
        raise ValidationError(
            _('Não foi encontrada a API de integração do Itau PIX para a empresa %s') % company_name
        )
    return base_payment_api
  
  @api.model
  def send_pix(self, payload, company_id=None):
    """Envia um PIX para o Itau"""
    try:
      base_payment_api = self.get_base_payment_api(company_id)
      params = base_payment_api.get_connection_params()
      headers = {
        # 'Authorization': f'Bearer {params['client_secret']}',
        'Content-Type': 'application/json',
        'X-API-Key': params['client_id'],
        'authorization': base_payment_api._get_itau_pix_token(),
      }
      response = requests.post(
        url=f'{params['base_url']}/itau-ep9-gtw-sispag-ext/v1/transferencias', 
        json=payload, 
        headers=headers, 
        timeout=params['timeout']
      )
      response.raise_for_status()
      logger.info(f'PIX enviado com sucesso')
      payment_pix = self.env['payment.pix'].create({
        'name': payload.get('identificacao_comprovante', ''),
        'description': payload.get('informacoes_entre_usuarios', ''),
        'amount': payload.get('valor_pagamento', 0),
        'date': fields.Datetime.now(),
        'status': response.json().get('status_pagamento', ''),
        'type': response.json().get('tipo_pagamento', 'PIX'),
        'pix_id': response.json().get('cod_pagamento', ''),
        'json_send': payload,
        'json_response': response.json()
      })
      return payment_pix
    except Exception as e:
      logger.error(f'Error: {e}')
      raise ValidationError(_('Erro ao enviar o PIX: %s') % e)