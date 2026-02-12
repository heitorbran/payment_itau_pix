from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
import requests
import logging
import json

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
  def send_pix(self, payload, company_id=None, payment_id=None, move_line_id=None):
    """Envia um PIX para o Itau"""
    try:
      base_payment_api = self.get_base_payment_api(company_id)
      params = base_payment_api.get_connection_params()
      token = base_payment_api._get_itau_pix_token()
      headers = {
        'Content-Type': 'application/json',
        'X-API-Key': params['client_id'],
        'Authorization': f'Bearer {token}',
      }
      
      api_url = base_payment_api.get_api_url()
      url = f'{api_url}/itau-ep9-gtw-sispag-ext/v1/transferencias'
      payload_json = json.dumps(payload, indent=2, ensure_ascii=False)
      response = requests.post(
        url=url,
        json=payload,
        headers=headers,
        timeout=params.get('timeout', 30)
      )
      
      try:
        response.raise_for_status()
        response_json = response.json()
        response_str = json.dumps(response_json, indent=2, ensure_ascii=False)
      except:
        response_str = response.text
      
      # Verifica erro de idempotência
      if response.status_code == 409:
        error_msg = 'Pagamento duplicado (idempotência). Verifique se o PIX já foi enviado anteriormente.'
        logger.warning(f'HTTP 409 - {error_msg}')
        
        # Tenta buscar o pagamento PIX existente
        existing_pix = self.env['payment.pix'].search([
          ('txid', '=', payload.get('txid')),
          ('correlation_id', '=', payload.get('correlation_id'))
        ], limit=1)
        
        if existing_pix:
          logger.info(f'Pagamento PIX existente encontrado: {existing_pix.id}')
          return existing_pix
        else:
          raise ValidationError(_(error_msg))
      
      # Extrai dados do payload para o registro
      txid = payload.get('txid', '')
      correlation_id = payload.get('correlation_id', '')
      
      # Gera correlation_id se não foi fornecido
      if not correlation_id:
        import uuid
        correlation_id = str(uuid.uuid4())
      
      # Cria registro do pagamento PIX
      payment_pix_vals = {
        'name': f"PAG_{payload.get('identificacao_comprovante', '')}",
        'description': payload.get('informacoes_entre_usuarios', ''),
        'amount': float(payload.get('valor_pagamento', 0).replace(',', '.')) if isinstance(payload.get('valor_pagamento'), str) else payload.get('valor_pagamento', 0),
        'date': fields.Datetime.now(),
        'status': response_json.get('status_pagamento', ''),
        'type': response_json.get('tipo_pagamento', 'PIX'),
        'pix_id': response_json.get('cod_pagamento', ''),
        'txid': txid,
        'correlation_id': correlation_id,
        'payment_id': payment_id,
        'move_line_id': move_line_id,
        'json_send': payload_json,
        'json_response': response_str,
      }
      
      return self.env['payment.pix'].create(payment_pix_vals)
      
    except requests.exceptions.HTTPError as e:
      error_msg = f'Erro de comunicação HTTP ao enviar PIX: {e}'
      if hasattr(e.response, 'text'):
        error_msg += f'\nResposta: {e.response.text}'
      logger.error(error_msg)
      if payment_id:
        payment = self.env['account.payment'].browse(payment_id)
        if payment.exists():
          payment.message_post(
            body=_('Erro ao enviar PIX: %s') % str(e),
            message_type='notification',
          )
      raise ValidationError(_('Erro ao enviar o PIX: %s') % str(e))
      
    except Exception as e:
      logger.error(f'Erro inesperado ao enviar PIX: {e}', exc_info=True)
      if payment_id:
        payment = self.env['account.payment'].browse(payment_id)
        if payment.exists():
          payment.message_post(
            body=_('Erro ao enviar PIX: %s') % str(e),
            message_type='notification',
          )
      raise ValidationError(_('Erro ao enviar o PIX: %s') % str(e))