# No arquivo base_payment_api.py
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from datetime import datetime, timedelta
import requests
import logging

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
    
    def _get_itau_pix_token(self):
        """
        Retorna um token Itau PIX válido, renovando-o se necessário.
        """
        self.ensure_one()
        
        # Verifica se já existe um token válido
        if self.itau_pix_current_token and self.itau_pix_token_expires_at:
            now = fields.Datetime.now()
            safety_margin = timedelta(seconds=self.itau_pix_token_safety_margin or 60)
            if now < (self.itau_pix_token_expires_at - safety_margin):
                _logger.info("Utilizando token Itau PIX existente e válido.")
                return self.itau_pix_current_token
        
        _logger.info("Token Itau PIX inexistente ou expirado. Iniciando processo de renovação.")
        
        # Gera novo token
        try:
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'User-Agent': 'Collection-by-itaú-for-developers'
            }
            payload = {
                'grant_type': 'client_credentials',
                'client_id': self.client_id,
                'client_secret': self.client_secret,
            }
            
            api_url = self.base_url.rstrip('/')
            token_url = f'{api_url}/api/oauth/jwt'
            
            start_time = datetime.now()
            response = requests.post(
                url=token_url,
                headers=headers,
                data=payload,
                timeout=self.timeout or 30
            )
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            
            response.raise_for_status()
            response_data = response.json()
            _logger.info(f'Response: {response_data}')
            
            if 'access_token' not in response_data:
                error_msg = 'access_token não encontrado na resposta'
                self.create_token_log({}, 'failed', error_msg, payload, response_data, duration_ms)
                raise ValidationError(_('Resposta inválida da API Itau PIX: %s') % error_msg)
            
            # Calcula a expiração
            expires_in = response_data.get('expires_in', 3600)  # Default 1 hora
            expires_at = datetime.now() + timedelta(seconds=expires_in)
            
            token_data = {
                'access_token': response_data['access_token'],
                'expires_at': fields.Datetime.to_string(expires_at),
                'token_type': response_data.get('token_type', 'Bearer')
            }
            
            self.write({
                'itau_pix_current_token': token_data['access_token'],
                'itau_pix_token_expires_at': token_data['expires_at'],
            })

            self.create_token_log(
                token_data,
                'success',
                request_data=payload,
                response_data=response_data,
                duration_ms=duration_ms
            )
            
            _logger.info(
                f"Token Itau PIX renovado com sucesso."
            )
            
            return token_data['access_token']
            
        except requests.exceptions.RequestException as e:
            error_msg = f'Erro na requisição: {str(e)}'
            self.create_token_log({}, 'failed', error_msg, payload, str(e), duration_ms if 'duration_ms' in locals() else None)
            raise ValidationError(_('Erro ao gerar o token de autorização Itau PIX: %s') % str(e))
        except Exception as e:
            raise ValidationError(_('Erro ao gerar o token de autorização Itau PIX: %s') % str(e))
    
    
    #TODO - Verificar depois como é a api para Produção
    def get_api_url(self):
        """Retorna a URL da API baseada na integração"""
        self.ensure_one()
        
        if self.integracao == 'itau_pix':
            base_url = self.base_url.rstrip('/')
            return base_url
        else:
            return self.base_url.rstrip('/') if self.base_url else ''