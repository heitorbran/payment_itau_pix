# No arquivo base_payment_api.py
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
from datetime import datetime, timedelta
import requests
import logging
import json

_logger = logging.getLogger(__name__)

class BasePaymentApi(models.Model):
    _inherit = 'base.payment.api'
    
    integracao = fields.Selection(
        selection_add=[
            ('itau_pix', 'Itau PIX'),
        ],
        string='Integração',
    )
    
    # Campos para cache do token Itau PIX
    itau_pix_current_token = fields.Char(
        string='Token Itau PIX Atual',
        readonly=True,
        help='Token atual do Itau PIX'
    )
    
    itau_pix_token_expires_at = fields.Datetime(
        string='Token Itau PIX Expira em',
        readonly=True,
        help='Data/hora de expiração do token Itau PIX'
    )
    
    itau_pix_token_safety_margin = fields.Integer(
        string='Margem de Segurança Token (segundos)',
        default=60,
        help='Renova o token automaticamente X segundos antes de expirar'
    )
    
    def _get_itau_pix_token(self, base_payment_api):
        """
        Retorna um token Itau PIX válido, renovando-o se necessário.
        """
        
        # Verifica se já existe um token válido
        if base_payment_api.itau_pix_current_token and base_payment_api.itau_pix_token_expires_at:
            now = fields.Datetime.now()
            safety_margin = timedelta(seconds=base_payment_api.itau_pix_token_safety_margin or 60)
            if now < (base_payment_api.itau_pix_token_expires_at - safety_margin):
                _logger.info("Utilizando token Itau PIX existente e válido.")
                return base_payment_api.itau_pix_current_token
        
        _logger.info("Token Itau PIX inexistente ou expirado. Iniciando processo de renovação.")
        
        # Gera novo token
        try:
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'User-Agent': 'Collection-by-itaú-for-developers'
            }
            payload = {
                'grant_type': 'client_credentials',
                'client_id': base_payment_api.client_id,
                'client_secret': base_payment_api.client_secret,
            }
            
            api_url = base_payment_api.base_url.rstrip('/')
            token_url = f'{api_url}/api/oauth/jwt'
            
            start_time = datetime.now()
            response = requests.post(
                url=token_url,
                headers=headers,
                data=payload,
                timeout=base_payment_api.timeout or 30
            )
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            
            response.raise_for_status()
            response_data = response.json()
            _logger.info(f'Response: {response_data}')
            
            if 'access_token' not in response_data:
                error_msg = 'access_token não encontrado na resposta'
                base_payment_api.create_token_log({}, 'failed', error_msg, payload, response_data, duration_ms)
                raise ValidationError(_('Resposta inválida da API Itau PIX: %s') % error_msg)
            
            # Calcula a expiração
            expires_in = response_data.get('expires_in', 3600)  # Default 1 hora
            expires_at = datetime.now() + timedelta(seconds=expires_in)
            
            token_data = {
                'access_token': response_data['access_token'],
                'expires_at': fields.Datetime.to_string(expires_at),
                'token_type': response_data.get('token_type', 'Bearer')
            }
            
            base_payment_api.write({
                'itau_pix_current_token': token_data['access_token'],
                'itau_pix_token_expires_at': token_data['expires_at'],
            })

            base_payment_api.create_token_log(
                token_data,
                'success',
                request_data=payload,
                response_data=response_data,
                duration_ms=duration_ms
            )
            
            _logger.info(f"Token Itau PIX renovado com sucesso.")
            return token_data['access_token']
            
        except requests.exceptions.RequestException as e:
            error_msg = f'Erro na requisição: {str(e)}'
            base_payment_api.create_token_log({}, 'failed', error_msg, payload, str(e), duration_ms if 'duration_ms' in locals() else None)
            raise ValidationError(_('Erro ao gerar o token de autorização Itau PIX: %s') % str(e))
        except Exception as e:
            raise ValidationError(_('Erro ao gerar o token de autorização Itau PIX: %s') % str(e))
  
    def send_pix(self, payload, payment_id=None, move_line_id=None):
        """Envia um PIX para o Itau"""
        # Validação: correlation_id deve sempre estar presente para idempotência
        if not payload.get('correlation_id'):
            raise ValidationError(_('correlation_id é obrigatório no payload para garantir idempotência.'))
        
        try:
            base_payment_api = self.search([
                ('integracao', '=', 'itau_pix'),
                ('company_id', '=', self.env.company.id),
                ('active', '=', True)
            ], limit=1)
            if not base_payment_api:
                raise UserError(_('Não foi encontrada a API de integração do Itau PIX para a empresa %s') % self.env.company.name)
            
            token = self._get_itau_pix_token(base_payment_api)
            headers = {
                'Content-Type': 'application/json',
                'X-API-Key': base_payment_api.client_id,
                'Authorization': f'Bearer {token}',
            }
            
            url = f'{base_payment_api.base_url}/itau-ep9-gtw-sispag-ext/v1/transferencias'
            payload_json = json.dumps(payload, indent=2, ensure_ascii=False)
            response = requests.post(
                url=url,
                json=payload,
                headers=headers,
                timeout=base_payment_api.timeout or 30
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
                _logger.warning(f'HTTP 409 - {error_msg}')
                
                # Busca usando payment_id (mais confiável) ou txid/correlation_id
                search_domain = []
                if payment_id:
                    search_domain.append(('payment_id', '=', payment_id))
                
                txid = payload.get('txid')
                if txid:
                    search_domain.append(('txid', '=', txid))
                
                correlation_id = payload.get('correlation_id')
                if correlation_id:
                    search_domain.append(('correlation_id', '=', correlation_id))
                
                existing_pix = self.env['payment.pix'].search(search_domain, limit=1) if search_domain else self.env['payment.pix']
                
                if existing_pix:
                    _logger.info(f'Pagamento PIX existente encontrado: {existing_pix.id}')
                    return existing_pix
                else:
                    raise UserError(_(error_msg))
            
            # Extrai dados do payload para o registro
            txid = payload.get('txid', '')
            correlation_id = payload.get('correlation_id', '')
            
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
                'pix_state': 'sent',  # Estado inicial após envio
            }
            
            return self.env['payment.pix'].create(payment_pix_vals)
        
        except requests.exceptions.HTTPError as e:
            error_msg = f'Erro de comunicação HTTP ao enviar PIX: {e}'
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                error_msg += f'\nResposta: {e.response.text}'
            _logger.error(error_msg)
            if payment_id:
                payment = self.env['account.payment'].browse(payment_id)
                if payment.exists():
                    payment.message_post(
                        body=_('Erro ao enviar PIX: %s') % str(e),
                        message_type='notification',
                    )
            raise UserError(_('Erro ao enviar o PIX: %s') % str(e))
            
        except Exception as e:
            _logger.error(f'Erro inesperado ao enviar PIX: {e}', exc_info=True)
            if payment_id:
                payment = self.env['account.payment'].browse(payment_id)
                if payment.exists():
                    payment.message_post(
                        body=_('Erro ao enviar PIX: %s') % str(e),
                        message_type='notification',
                    )
            # Converte para UserError para erros de negócio
            if isinstance(e, (UserError, ValidationError)):
                raise
            raise UserError(_('Erro ao enviar o PIX: %s') % str(e))
  
    def update_payment_pix_status(self, payment_pix_id):
        """Atualiza o status de um pagamento PIX enviado para o Itaú"""
        try:
            base_payment_api = self.search([
                ('integracao', '=', 'itau_pix'),
                ('company_id', '=', self.env.company.id),
                ('active', '=', True)
            ], limit=1)
            if not base_payment_api:
                raise ValidationError(_('Não foi encontrada a API de integração do Itau PIX para a empresa %s') % self.env.company.name)
            token = self._get_itau_pix_token(base_payment_api)
            headers = {
                'Content-Type': 'application/json',
                'X-API-Key': base_payment_api.client_id,
                'Authorization': f'Bearer {token}',
            }
            
            url = f'{base_payment_api.base_url}/itau-ep9-gtw-sispag-ext/v1/pagamentos_sispag/{payment_pix_id}'
            response = requests.get(
                url=url,
                headers=headers,
                timeout=base_payment_api.timeout or 30
            )
            response.raise_for_status()
            response_json = response.json()
            return response_json
        except requests.exceptions.HTTPError as e:
            error_msg = f'Erro de comunicação HTTP ao atualizar status do pagamento PIX: {e}'
            if hasattr(e.response, 'text'):
                error_msg += f'\nResposta: {e.response.text}'
            _logger.error(error_msg)
            raise ValidationError(_('Erro ao atualizar status do pagamento PIX: %s') % str(e))
        except Exception as e:
            _logger.error(f'Erro inesperado ao atualizar status do pagamento PIX: {e}', exc_info=True)
            raise ValidationError(_('Erro ao atualizar status do pagamento PIX: %s') % str(e))