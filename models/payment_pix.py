from odoo import models, fields, api

class PaymentPix(models.Model):
  _name = 'payment.pix'
  _description = 'Pagamentos PIX'
  
  name = fields.Char(string='Nome')
  description = fields.Text(string='Descrição')
  amount = fields.Float(string='Valor')
  date = fields.Datetime(string='Data')
  status = fields.Char(string='Status')
  type = fields.Char(string='Tipo')
  pix_id = fields.Char(string='PIX ID')
  json_send = fields.Text(string='JSON Enviado')
  json_response = fields.Text(string='JSON Resposta')