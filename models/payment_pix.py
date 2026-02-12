from odoo import models, fields, api

class PaymentPix(models.Model):
  _name = 'payment.pix'
  _description = 'Registros de Pagamentos PIX'
  _inherit = 'mail.thread'
  
  name = fields.Char(string='Nome')
  description = fields.Text(string='Descrição')
  amount = fields.Float(string='Valor')
  date = fields.Datetime(string='Data')
  status = fields.Char(string='Status')
  type = fields.Char(string='Tipo')
  pix_id = fields.Char(string='PIX ID')
  txid = fields.Char(string='TXID PIX', help='Identificador único da transação PIX')
  correlation_id = fields.Char(string='Correlation ID', help='ID de correlação para rastreabilidade')
  payment_id = fields.Many2one('account.payment', string='Pagamento Odoo', help='Pagamento Odoo relacionado')
  move_line_id = fields.Many2one('account.move.line', string='Linha da Fatura', help='Linha da fatura relacionada ao pagamento PIX')
  json_send = fields.Text(string='JSON Enviado')
  json_response = fields.Text(string='JSON Resposta')
  
  # Campos relacionados para facilitar a visualização
  move_id = fields.Many2one(related='move_line_id.move_id', string='Fatura', store=True)
  partner_id = fields.Many2one(related='move_line_id.partner_id', string='Parceiro', store=True)
  company_id = fields.Many2one(related='move_id.company_id', string='Empresa', store=True)