from datetime import date

from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError
import logging

_logger = logging.getLogger(__name__)

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    ticket_id = fields.Many2one('project.task', string='Related Ticket', readonly=True, ondelete='set null')
    part_id = fields.Many2one('project.task.part', string="Related Part", ondelete='cascade')
    is_part_quotation = fields.Boolean(string="Is Part Quotation", default=False)


    def write(self, vals):
        res = super().write(vals)

        if 'order_line' in vals or 'amount_total' in vals or 'state' in vals:
            if 'state' in vals and vals['state'] == 'sent':
                for order in self:
                    if order.part_id and order.state not in ('cancel',):
                        # Update part amount with quotation total
                        order.part_id.sudo().write({
                            'amount': order.amount_total
                        })

        # Only run if state changed to 'sent'
        if 'state' in vals and vals['state'] == 'sent':
            for order in self.filtered(lambda o: o.part_id):
                part = order.part_id
                task = part.task_id
                part_name = part.product_id.display_name if part.product_id else (part.description or "Unknown Part")
                notif = self.env['part.customer.approval.notification'].sudo().search([
                    ('part_id', '=', part.id),
                    ('task_id', '=', task.id),
                ], limit=1)

                if notif:
                    notif.sudo().write({
                        'stage': 'pending',
                    })
                else:
                    notif = self.env['part.customer.approval.notification'].sudo().create({
                        'task_id': task.id,
                        'product_id': task.customer_product_id.product_id.id if task.customer_product_id and task.customer_product_id.product_id else False,
                        'part_id': part.id,
                        'part_name': part_name,
                        'coverage': part.coverage,
                        'stage': 'pending',
                    })

                # Update the part status
                part.sudo().write({
                    'status': 'waiting_customer',
                })

                # Post message in the task chatter
                task = part.task_id
                message = f"Customer approval requested for part '{part_name}' (status set to 'waiting_customer')."
                task.message_post(body=message, subtype_xmlid='mail.mt_note')

                # Send portal/customer notification if available
                try:
                    subject = "Customer Approval Request"
                    msg = f"Customer approval requested for part '{part_name}' in ticket {task.name}."
                    url = f"/my/ticket/{task.id}"
                    task._send_customer_notification(
                        partner=task.partner_id,
                        subject=subject,
                        message=msg,
                        url=url
                    )
                except Exception as e:
                    _logger.exception(f">>> Failed to send customer notification: {e}")

        return res

class ProjectTask(models.Model):
    _inherit = 'project.task'


    part_ids = fields.One2many(
        'project.task.part',
        'task_id',
        string="Parts"
    )

    @api.model
    def _check_part_status_before_stage_change(self, new_stage):

        restricted_stages = ['resolved', 'done']

        for task in self:

            # Check if ANY linked part has approval requested = True
            has_requested_parts = any(task.part_ids.filtered(lambda p: p.approval_requested))
            # Skip validation if no part has approval_requested
            if not has_requested_parts:
                continue

            # Proceed only for restricted stages
            if new_stage and new_stage.lower() in restricted_stages:
                pending_parts = task.part_ids.filtered(lambda p: p.status not in ('pick_up', 'received'))

                if pending_parts:
                    raise UserError(_(
                        "Please ensure all parts are picked up or received before marking the task as done or resolved."
                    ))

    def write(self, vals):
        """Override write to validate when stage_id is changed."""
        if 'stage_id' in vals:
            new_stage = self.env['project.task.type'].browse(vals['stage_id'])
            self._check_part_status_before_stage_change(new_stage.name)
        return super(ProjectTask, self).write(vals)


    def unlink(self):
        for task in self:
            self.env['part.approval.notification'].search([('task_id', '=', task.id)]).unlink()
            self.env['part.customer.approval.notification'].search([('task_id', '=', task.id)]).unlink()
            quotations = self.env['sale.order'].sudo().search([
                ('ticket_id', '=', task.id),
            ])
            if quotations:
                quotations.sudo().unlink()
        return super(ProjectTask, self).unlink()


    quotation_count = fields.Integer(
        string='Quotation Count',
        compute='_compute_quotation_count',
        store=False
    )

    @api.depends('part_ids')
    def _compute_quotation_count(self):
        for task in self:
            if not task.part_ids:
                task.quotation_count = 0
                continue
            quotations = self.env['sale.order'].sudo().search([
                ('part_id', 'in', task.part_ids.ids)
            ])
            task.quotation_count = len(quotations)

    fsm_invoice_count = fields.Integer(
        string='Invoice Count',
        compute='_compute_invoice_count',
        store=False
    )

    @api.depends('part_ids', 'part_ids.sale_order_ids', 'part_ids.sale_order_ids.invoice_ids')
    def _compute_invoice_count(self):
        for task in self:
            quotations = self.env['sale.order'].sudo().search([
                ('part_id', 'in', task.part_ids.ids)
            ])
            invoices = self.env['account.move'].sudo().search([
                ('invoice_origin', 'in', quotations.mapped('name')),
                ('move_type', '=', 'out_invoice')
            ])
            task.fsm_invoice_count = len(invoices)

    def action_open_quotation(self):
        """Open all quotations linked to this task's parts"""
        self.ensure_one()
        quotations = self.env['sale.order'].sudo().search([
            ('part_id', 'in', self.part_ids.ids)
        ])
        return {
            'name': _('Task Quotations'),
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'view_mode': 'tree,form',
            'domain': [('id', 'in', quotations.ids)],
            'target': 'current',
            'context': {'create': False},
        }

    def action_open_invoice(self):
        """Open all invoices linked to this task's parts"""
        self.ensure_one()

        quotations = self.env['sale.order'].sudo().search([
            ('part_id', 'in', self.part_ids.ids)
        ])
        invoices = self.env['account.move'].search([
            ('invoice_origin', 'in', quotations.mapped('name')),
            ('move_type', '=', 'out_invoice')
        ])

        return {
            'name': _('Task Invoices'),
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'tree,form',
            'domain': [('id', 'in', invoices.ids)],
            'target': 'current',
            'context': {'create': False},
        }

class PartServiceWizard(models.TransientModel):
    _inherit = 'part.service.wizard'

    coverage = fields.Selection([
        ('foc', 'FOC'),
        ('chargeable', 'Chargeable')
    ], string='Coverage', store=True)

    amount = fields.Float(
        string="Amount",
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        part_id = self._context.get('active_id')
        if part_id:
            part = self.env['project.task.part'].browse(part_id)
            if 'coverage' in fields_list:
                res['coverage'] = part.coverage or 'chargeable'  # fallback if blank
            if 'part_service_type' in fields_list:
                res['part_service_type'] = part.part_service_type
            if 'amount' in fields_list:
                res['amount'] = part.amount
        return res

    def apply_service_update(self):
        self.ensure_one()
        self.part_id.write({
            'part_service_type': self.part_service_type,
            'serial_number_id': self.serial_number_id.id,
            'previous_serial_number_id': self.previous_serial_number_ids.id,
            'description': self.description,
            'coverage': self.coverage,
            'amount': self.amount,
        })


class ProjectTaskPart(models.Model):
    _inherit = 'project.task.part'

    coverage = fields.Selection([
        ('foc', 'FOC'),
        ('chargeable', 'Chargeable')
    ], string='Coverage', compute='_compute_coverage', store=True, readonly=False)

    amount = fields.Float(
        string="Amount",
        compute='_compute_amount',
        store=True,
        readonly=False
    )
    sale_order_ids = fields.One2many('sale.order', 'part_id', string="Sale Orders")

    status = fields.Selection([
        ('draft', 'Draft'),
        ('approved', 'Approved'),
        ('waiting_customer', 'Waiting Customer'),
        ('customer_approved', 'Customer Approved'),
        ('waiting_warehouse_manager', 'Waiting Warehouse Manager'),
        ('shipment', 'Shipment'),
        ('pick_up', 'Pick up'),
        ('received', 'Received'),
        ('rejected', 'Rejected'),
    ], string='Status', default='draft', tracking=True)

    approval_requested = fields.Boolean(
        string='Approval Requested',
        default=False,
        help='Indicates if customer approval has been requested'
    )

    @api.model
    def write(self, vals):
        res = super().write(vals)
        for part in self:
            if 'status' in vals:
                notif = self.env['part.approval.notification'].sudo().search([('part_id', '=', part.id)], limit=1)
                if notif:
                    notif.status = vals['status']
        return res

    @api.depends('product_id', 'coverage')
    def _compute_amount(self):
        for rec in self:

            # Check if there's a quotation for this part
            quotation = self.env['sale.order'].sudo().search([
                ('part_id', '=', rec.id),
                ('state', '!=', 'cancel')
            ], limit=1)

            if quotation:
                # Use quotation total if it exists
                rec.amount = quotation.amount_total
                continue

            if rec.coverage == 'foc':
                rec.amount = 0.0
                continue
            if not rec.product_id:
                rec.amount = 0.0
                continue
            # Since product_id is product.template, we can use it directly
            product_tmpl = rec.product_id

            # Base price from product.template
            base_price = product_tmpl.list_price or 0.0

            # Fetch taxes from template (use supplier_taxes_id if relevant)
            taxes = product_tmpl.taxes_id.filtered(lambda t: t.company_id == rec.env.company)

            if taxes:
                # Compute all taxes on the list price
                tax_data = taxes.compute_all(
                    base_price,
                    currency=rec.env.company.currency_id,
                    quantity=1.0,
                    product=False,
                    partner=False
                )
                total_price = tax_data['total_included']

            else:
                total_price = base_price

            rec.amount = total_price

    @api.depends('mapping_id','mapping_id.contract_id','mapping_id.contract_id.contract_type.with_parts')
    def _compute_coverage(self):
        for rec in self:
            coverage = 'chargeable'
            mapping = rec.mapping_id

            if mapping:
                contract = mapping.contract_id
                if contract:
                    contract_type = contract.contract_type
                    end_date = contract.end_date

                    if end_date and end_date >= date.today():
                        if contract_type and contract_type.with_parts:
                            coverage = 'foc'
                        else:
                            coverage = 'chargeable'
                    else:
                        coverage = 'chargeable'

                elif mapping.status != 'chargeable':
                        coverage = 'foc'

            rec.coverage = coverage

    def unlink(self):
        for part in self:
            self.env['part.approval.notification'].search([('part_id', '=', part.id)]).unlink()
            self.env['part.customer.approval.notification'].search([('part_id', '=', part.id)]).unlink()
        quotations = self.env['sale.order'].sudo().search([
            ('part_id', '=', part.id)
        ])
        if quotations:
            quotations.sudo().unlink()

        return super(ProjectTaskPart, self).unlink()

    def action_parts_request(self):
        """Send notification only to the department manager using message_post"""
        for part in self:
            if not part.part_service_type:
                raise UserError(_("Please select the Part Service Type before requesting."))
            part.approval_requested = True

            task = part.task_id
            if not task:
                continue

            # Get the department supervisor (manager)
            supervisor = task.department_id.manager_id if task.department_id else False
            if not supervisor or not supervisor.user_id:
                continue

            # Prepare message
            part_name = part.product_id.display_name if part.product_id else 'Unknown Part'
            customer = task.partner_id
            product_name = (
                task.customer_product_id.product_id.display_name
                if task.customer_product_id and task.customer_product_id.product_id
                else 'No Product'
            )

            if supervisor.company_id != task.company_id:
                raise AccessError(_(f"You Can not send request because supervisor ({supervisor.company_id.name}) and task ({task.company_id.name}) belong to different companies."))

            # Create notification record in part.approval.notification
            notification = self.env['part.approval.notification'].create({
                'task_id': task.id,
                'part_id': part.id,
                'part_name': part_name,
                'supervisor_id': supervisor.id,
                'user_ids': task.user_ids,
                'partner_id': customer.id,
                'product_id': task.customer_product_id.product_id.id if task.customer_product_id and task.customer_product_id.product_id else False,
                'coverage': part.coverage,
                'status': 'draft',
                'company_id': task.company_id.id,
            })

            # Post message to task chatter and notify only the supervisor
            notification.message_notify(
                body=f"The part '{part_name}' of product '{product_name}' is send approval for Task '{task.name}'.",
                subject=_("Part Approval Request"),
                partner_ids=[supervisor.user_id.partner_id.id],
                subtype_xmlid='mail.mt_note',
            )
            task.message_post(
                body=f"The part '{part_name}' of product '{product_name}' is send approval for Task '{task.name}'.",
                subtype_xmlid = 'mail.mt_note',
            )

        return True

    has_cancelled_quotation = fields.Boolean(string="Cancelled Quotation", default=False)

    @api.depends('task_id.sale_order_ids.state')
    def _compute_has_cancelled_quotation(self):
        for part in self:
            quotation = self.env['sale.order'].sudo().search([
                ('ticket_id', '=', part.task_id.id)
            ], limit=1)
            part.has_cancelled_quotation = quotation.state == 'cancel' if quotation else False


    def action_create_quotation(self):
        for part in self:
            part.approval_requested = True
            # Only allow if chargeable
            if part.coverage != 'chargeable':
                continue
            task = part.task_id
            if not task or not task.partner_id:
                continue
            customer = task.partner_id
            ticket_no = task.sequence_fsm or task.name
            part_name = part.product_id.display_name if part.product_id else 'Unknown Part'
            product_name = (
                task.customer_product_id.product_id.display_name
                if task.customer_product_id and task.customer_product_id.product_id
                else 'No Product'
            )

            # Find any existing quotation for this task
            quotation = self.env['sale.order'].sudo().search([
                ('ticket_id', '=', task.id),
                ('part_id', '=', part.id),
            ], limit=1)

            # If quotation exists (even canceled), just open it
            if quotation:
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Quotation',
                    'res_model': 'sale.order',
                    'view_mode': 'form',
                    'res_id': quotation.id,
                    'target': 'current',
                }

            # Create Ticket Quotation Automatically ---
            quotation = self._create_ticket_quotation(task, part)
            quotation.is_part_quotation = True
            print("is_part_quotation true")

        return {
            'type': 'ir.actions.act_window',
            'name': 'Quotation',
            'res_model': 'sale.order',
            'view_mode': 'form',
            'res_id': quotation.id,
            'target': 'current',
        }

    def action_open_canceled_quotation(self):
        for part in self:
            part.approval_requested = True
            # Only allow if chargeable
            if part.coverage != 'chargeable':
                continue
            task = part.task_id
            if not task or not task.partner_id:
                continue
            customer = task.partner_id
            ticket_no = task.sequence_fsm or task.name
            part_name = part.product_id.display_name if part.product_id else 'Unknown Part'
            product_name = (
                task.customer_product_id.product_id.display_name
                if task.customer_product_id and task.customer_product_id.product_id
                else 'No Product'
            )
            
            # Find any existing quotation for this task
            quotation = self.env['sale.order'].sudo().search([
                ('ticket_id', '=', task.id),
                ('part_id', '=', part.id),
            ], limit=1)

            # If quotation exists (even canceled), just open it
            if quotation:
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Quotation',
                    'res_model': 'sale.order',
                    'view_mode': 'form',
                    'res_id': quotation.id,
                    'target': 'current',
                }
        return {
            'type': 'ir.actions.act_window',
            'name': 'Quotation',
            'res_model': 'sale.order',
            'view_mode': 'form',
            'res_id': quotation.id,
            'target': 'current',
        }

    def _create_ticket_quotation(self, task, part):
        """Create sale.order quotation from task parts (chargeable only)."""
        task.ensure_one()

        # Create quotation only for the selected part
        if not part or part.coverage != 'chargeable':
            raise UserError(_("Selected part is not chargeable or missing."))

        chargeable_parts = [part]

        order_lines = []
        for part in chargeable_parts:
            if not part.product_id:
                raise UserError(_("Missing product in ticket part."))

            variant = self.env['product.product'].search(
                [('product_tmpl_id', '=', part.product_id.id)],
                limit=1
            )
            if not variant:
                raise UserError(_("No product variant found for part %s") % part.product_id.display_name)

            order_lines.append((0, 0, {
                'product_id': variant.id,
                'product_uom_qty': 1.0,
                'price_unit': variant.list_price or 0.0,
                'name': part.description or variant.name,
                'unit_status': 'chargeable',
            }))

        quotation = self.env['sale.order'].sudo().create({
            'partner_id': task.partner_id.id,
            'origin': task.name,
            'ticket_id': task.id,
            'order_line': order_lines,
            'part_id': part.id,
        })

        return quotation
