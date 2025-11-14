from odoo import models, fields, api,_
from odoo.exceptions import UserError, AccessError
from odoo.osv.expression import expression
import logging

_logger = logging.getLogger(__name__)


class PartApprovalNotification(models.Model):
    _name = 'part.approval.notification'
    _description = 'Part Approval Notification'
    _rec_name = 'task_id'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    task_id = fields.Many2one('project.task', string='Call Name', readonly=True, ondelete='cascade', )
    product_id = fields.Many2one('product.product', string='Product', readonly=True, ondelete='cascade', )
    part_id = fields.Many2one('project.task.part', string='Part', readonly=True)
    user_ids = fields.Many2many('res.users', string='Assignee', readonly=True)
    part_name = fields.Char(string='Part Name', readonly=True)
    supervisor_id = fields.Many2one('hr.employee', string='Supervisor', readonly=True)
    message = fields.Text(string='Notification Message')
    partner_id = fields.Many2one('res.partner', string='Customer', readonly=True)
    coverage = fields.Selection([('foc', 'FOC'), ('chargeable', 'Chargeable')], string='Coverage',readonly=True)
    sequence_fsm = fields.Char(string='Ticket Number', related='task_id.sequence_fsm', store=True)
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)

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

    manager = fields.Many2one('hr.employee', "Manager", domain=[('warehouse_manager', '=', True)])
    manager_user_id = fields.Many2one('res.users', related='manager.user_id', store=True)

    show_pick_up_button = fields.Boolean(compute='_compute_show_pick_up_button')
    show_stock_button = fields.Boolean(compute='_compute_show_stock_button')

    show_request_button = fields.Boolean(
        string="Show Request Button",
        compute="_compute_show_request_button",
        store=False
    )

    @api.depends('coverage', 'status')
    def _compute_show_request_button(self):
        """Compute visibility for 'Request' button based on coverage and approval/payment flow."""
        for rec in self:
            show = False
            # === FOC Flow ===
            if rec.coverage == 'foc':
                if rec.status == 'approved':
                    show = True

            # === CHARGEABLE Flow ===
            elif rec.coverage == 'chargeable':
                if rec.status != 'customer_approved':
                    show = False
                else:
                    show = True
            else:
                show = False
            rec.show_request_button = show

    @api.model_create_multi
    def create(self, vals_list):
        # handle bulk create efficiently and assign warehouse manager where possible
        records = super().create(vals_list)
        for rec in records:
            try:
                rec._auto_assign_manager_from_task()
            except Exception:
                # do not block create on manager assignment failure, but log it
                _logger.exception('Failed to auto assign warehouse manager for part.approval.notification %s', rec.id)
        return records

    def _auto_assign_manager_from_task(self):
        """Try to detect warehouse and assign its manager to the record.
        Safe - non-blocking helper used on create.
        """
        if not self.task_id:
            _logger.debug('No task linked for record %s', self.id)
            return

        product = (self.task_id.customer_product_id.product_id
                   if self.task_id.customer_product_id else self.product_id)
        if not product:
            _logger.debug('No product found for task %s, skipping warehouse detection', self.task_id.id)
            return

        partner = self.task_id.partner_id or self.partner_id
        # search a move line that relates product and partner
        domain = [
            ('product_id', '=', product.id),
        ]
        if partner:
            domain += ['|', ('picking_id.partner_id', '=', partner.id),
                       ('picking_id.partner_id.commercial_partner_id', '=', partner.commercial_partner_id.id)]
        move_line = self.env['stock.move.line'].search(domain, order='id desc', limit=1)

        warehouse = False
        if move_line:
            location = move_line.location_id
            warehouse = self.env['stock.warehouse'].search(['|', ('lot_stock_id', '=', location.id),
                                                            ('view_location_id', '=', location.id)], limit=1)
            # climb up location parents
            parent = location
            while parent and not warehouse:
                parent = parent.location_id
                if parent:
                    warehouse = self.env['stock.warehouse'].search(['|', ('lot_stock_id', '=', parent.id),
                                                                    ('view_location_id', '=', parent.id)], limit=1)
        else:
            warehouse = self.env['stock.warehouse'].search([('company_id', '=', self.env.company.id)], limit=1)

        if not warehouse:
            _logger.debug('No warehouse detected for product %s', product.id)
            return

        if warehouse.manager:
            self.manager = warehouse.manager.id
            # related manager_user_id will be set by relational stored field automatically
            _logger.debug('Assigned manager %s to notification %s', warehouse.manager.id, self.id)

    @api.depends('status', 'company_id.enable_direct_pickup')
    def _compute_show_pick_up_button(self):
        for rec in self:
            rec.show_pick_up_button = (rec.company_id.enable_direct_pickup and rec.status == 'shipment')

    @api.depends('company_id.enable_warehouse')
    def _compute_show_stock_button(self):
        for rec in self:
            rec.show_stock_button = (rec.company_id.enable_warehouse == 'internal_warehouse')

    def _check_supervisor_rights(self, task):
        """Return supervisor employee record and validate current user is supervisor's linked user.
        Raise AccessError if validation fails.
        """
        supervisor = task.department_id.manager_id if task.department_id else False
        if not supervisor or not supervisor.user_id:
            raise AccessError(_("No supervisor is assigned to this task, approval cannot proceed."))
        if self.env.user.id != supervisor.user_id.id:
            raise AccessError(_("You are not allowed to perform this action. Only the Supervisor (%s) can do this.") % supervisor.name)
        return supervisor

    def _get_product_from_task(self, task):
        return task.customer_product_id.product_id if task.customer_product_id else self.product_id

    def action_approve(self):
        for rec in self:
            if rec.company_id.enable_warehouse != 'internal_warehouse':
                _logger.debug('Skipping warehouse logic because company set to external for %s', rec.id)
                continue

            task = rec.task_id or rec
            supervisor = rec._check_supervisor_rights(task)

            rec.status = 'approved'
            if rec.part_id:
                rec.part_id.status = 'approved'

            part_name = rec.part_id.product_id.display_name if rec.part_id and rec.part_id.product_id else rec.part_name or _('Unnamed Part')

            # notify assignees
            assignees = (task.user_ids | rec.user_ids).filtered('partner_id')
            if assignees:
                partner_ids = assignees.mapped('partner_id.id')
                rec.message_notify(
                    body=_("Supervisor %s has approved your request for the part %s.") % (self.env.user.name, part_name),
                    subject=_('Assignee Notification - %s') % (task.name or ''),
                    partner_ids=partner_ids,
                    subtype_xmlid='mail.mt_note',
                )
                _logger.debug('Notified assignees %s for record %s', partner_ids, rec.id)

    def action_reject(self):
        for rec in self:
            if rec.company_id.enable_warehouse != 'internal_warehouse':
                _logger.debug('Skipping warehouse logic because company set to external for %s', rec.id)
                continue

            task = rec.task_id or rec
            rec._check_supervisor_rights(task)

            rec.status = 'rejected'
            if rec.part_id:
                rec.part_id.status = 'rejected'

            part_name = rec.part_id.product_id.display_name if rec.part_id and rec.part_id.product_id else rec.part_name or _('Unnamed Part')

            assignees = (task.user_ids | rec.user_ids).filtered('partner_id')
            if assignees:
                partner_ids = assignees.mapped('partner_id.id')
                rec.message_notify(
                    body=_('Supervisor %s has rejected your request for the part %s.') % (self.env.user.name, part_name),
                    subject=_('Assignee Notification - %s') % (task.name or ''),
                    partner_ids=partner_ids,
                    subtype_xmlid='mail.mt_note',
                )

    def _detect_warehouse_for_task(self, task, product):
        """Return warehouse record or False. Factorised to avoid duplication."""
        if not product:
            return False
        partner = task.partner_id
        domain = [('product_id', '=', product.id)]
        if partner:
            domain += ['|', ('picking_id.partner_id', '=', partner.id), ('picking_id.partner_id.commercial_partner_id', '=', partner.commercial_partner_id.id)]
        move_line = self.env['stock.move.line'].search(domain, order='id desc', limit=1)
        if not move_line:
            return self.env['stock.warehouse'].search([('company_id', '=', self.env.company.id)], limit=1)
        location = move_line.location_id
        warehouse = self.env['stock.warehouse'].search(['|', ('lot_stock_id', '=', location.id), ('view_location_id', '=', location.id)], limit=1)
        parent = location
        while parent and not warehouse:
            parent = parent.location_id
            if parent:
                warehouse = self.env['stock.warehouse'].search(['|', ('lot_stock_id', '=', parent.id), ('view_location_id', '=', parent.id)], limit=1)
        return warehouse

    def action_request_warehouse_manager(self):
        for rec in self:
            if rec.company_id.enable_warehouse != 'internal_warehouse':
                _logger.debug('Skipping warehouse logic because company set to external for %s', rec.id)
                continue

            task = rec.task_id or rec
            product = self._get_product_from_task(task)
            if not product:
                raise UserError(_('No product found for this task.'))
            warehouse = self._detect_warehouse_for_task(task, product)
            if not warehouse:
                raise UserError(_('Warehouse not found for product: %s') % product.display_name)

            # supervisor permission
            rec._check_supervisor_rights(task)

            if rec.part_id:
                rec.part_id.status = 'waiting_warehouse_manager'
            rec.status = 'waiting_warehouse_manager'

            # prepare message and notify manager
            if not warehouse.manager or not warehouse.manager.user_id or not warehouse.manager.user_id.partner_id:
                raise UserError(_('No manager or manager user/partner found for warehouse: %s') % warehouse.name)

            manager_partner_id = warehouse.manager.user_id.partner_id.id
            message_body = _('Supervisor %s has sent an approval request for the part %s.') % (self.env.user.name, rec.part_name or '')

            rec.message_notify(
                body=message_body,
                subject=_('Warehouse Manager Request - %s') % (rec.display_name or ''),
                partner_ids=[manager_partner_id],
                subtype_xmlid='mail.mt_note',
            )

    def action_part_available(self):
        for rec in self:
            if rec.company_id.enable_warehouse != 'internal_warehouse':
                _logger.debug('Skipping warehouse logic because company set to external for %s', rec.id)
                continue

            task = rec.task_id or rec
            product = self._get_product_from_task(task)
            if not product:
                raise UserError(_('No product found for this task.'))

            warehouse = self._detect_warehouse_for_task(task, product)
            if not warehouse:
                raise UserError(_('Warehouse not found for product: %s') % product.display_name)

            manager_employee = warehouse.manager
            manager_user = manager_employee.user_id if manager_employee else False

            if not manager_user or self.env.user.id != manager_user.id:
                raise AccessError(_('Only the Warehouse Manager (%s) can mark this part as available.') % (manager_employee.name if manager_employee else 'Not Assigned'))

            if rec.status != 'waiting_warehouse_manager':
                _logger.debug('Record %s not in waiting_warehouse_manager; current status: %s', rec.id, rec.status)
                continue

            rec.status = 'shipment'
            if rec.part_id:
                rec.part_id.status = 'shipment'

            # notify supervisor and assignees
            notify_users = (rec.supervisor_id.user_id if rec.supervisor_id and rec.supervisor_id.user_id else self.env['res.users']) | rec.user_ids
            partner_ids = notify_users.filtered('partner_id').mapped('partner_id.id')

            part_name = rec.part_id.product_id.display_name if rec.part_id and rec.part_id.product_id else rec.part_name or _('Unnamed Part')

            if partner_ids:
                rec.message_notify(
                    subject=_('Part Available'),
                    body=_('The product %s of the part %s has been marked as available for the task %s by %s.') % (
                        product.display_name, part_name, task.display_name, self.env.user.display_name),
                    partner_ids=partner_ids,
                    subtype_xmlid='mail.mt_note',
                    email_layout_xmlid='mail.mail_notification_light',
                )

    def action_pick_up(self):
        for rec in self:
            if rec.company_id.enable_warehouse != 'internal_warehouse':
                _logger.debug('Skipping warehouse logic because company set to external for %s', rec.id)
                continue

            if not rec.company_id.enable_direct_pickup:
                raise UserError(_('Direct pickup is disabled for this company.'))

            if self.env.user.id not in rec.user_ids.ids:
                raise AccessError(_('Only assigned users can mark this part as picked up.'))

            if rec.status != 'shipment':
                raise UserError(_('You can only mark parts as Pick Up when status is %s.') % _('Shipment'))

            rec.status = 'pick_up'
            if rec.part_id:
                rec.part_id.status = 'pick_up'

            assigned_user_names = ', '.join(rec.user_ids.mapped('name')) or 'Unknown User'
            message_body = _('Part Pick Up by %s. Status moved to Pick Up.') % (assigned_user_names,)
            rec.message_post(body=message_body)
            if rec.task_id:
                rec.task_id.message_post(body=message_body)

            part_name = rec.part_id.product_id.display_name or rec.part_id.display_name or rec.part_name or _("Unnamed Part")

            # notify supervisor
            supervisor_partner = rec.supervisor_id.user_id.partner_id if rec.supervisor_id and rec.supervisor_id.user_id else False
            if supervisor_partner:
                rec.message_notify(
                    subject=_('Part Picked Up'),
                    body=_(
                        f"The part '{rec.display_name}' has been marked as picked up by {self.env.user.name, part_name}."
                    ),
                    partner_ids=[supervisor_partner.id],
                    subtype_xmlid='mail.mt_note',
                )

    def action_redirect_stock(self):
        self.ensure_one()
        if self.env.company.enable_warehouse != 'internal_warehouse':
            return

        if not self.product_id:
            raise UserError(_('No product linked with this record.'))

        # Get the latest move line
        domain = [('product_id', '=', self.product_id.id)]
        if self.partner_id:
            domain.append(('picking_id.partner_id', '=', self.partner_id.id))

        move_line = self.env['stock.move.line'].search(domain, order='id desc', limit=1)

        if move_line:
            location = move_line.location_id
            location_id = location.id

            # Step-1: find warehouse for this location
            warehouse = self.env['stock.warehouse'].search([
                ('lot_stock_id', 'parent_of', location.id)
            ], limit=1)

            manager_employee = warehouse.manager
            manager_user = manager_employee.user_id if manager_employee else False

            if not manager_user or self.env.user.id != manager_user.id:
                raise AccessError(_('Only the Warehouse Manager (%s) can open the stock.') % (manager_employee.name if manager_employee else 'Not Assigned'))

        else:
            # fallback: get main stock location
            warehouse = self.env['stock.warehouse'].search([
                ('company_id', '=', self.env.company.id)
            ], limit=1)

            if warehouse and warehouse.lot_stock_id:
                location = warehouse.lot_stock_id
                location_id = location.id
            else:
                raise UserError(_('No stock locations found for this product'))

        # Return stock.quant window filtered by our final location
        action = {
            'type': 'ir.actions.act_window',
            'name': 'Stock View (Filtered)',
            'res_model': 'stock.quant',
            'view_mode': 'tree,form',
            'target': 'current',
            'domain': [('location_id', '=', location_id)],
            'context': {
                'default_location_ids': location_id,
                'search_default_location_id': location_id,
            }
        }
        return action


class PartCustomerApprovalNotification(models.Model):
    _name = 'part.customer.approval.notification'
    _description = 'Customer Part Approval Notification'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    task_id = fields.Many2one('project.task', string='Call Name', readonly=True, store=True)
    product_id = fields.Many2one('product.product', string='Product', readonly=True, store=True)
    part_id = fields.Many2one('project.task.part', string='Part', readonly=True, store=True)
    part_name = fields.Char(string='Part Name', readonly=True, store=True)
    coverage = fields.Selection([
        ('foc', 'FOC'),
        ('chargeable', 'Chargeable')
    ], string='Coverage', readonly=True, store=True)
    message = fields.Text(string='Notification Message', store=True)
    sequence_fsm = fields.Char(string='Ticket Number', related='task_id.sequence_fsm', store=True)
    user_ids = fields.Many2many('res.users',string="Assignee")
    stage = fields.Selection([
        ('pending', 'Pending'),
        ('partially_paid','Partially Paid'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], default='pending', string='Stage', tracking=True, readonly=True, store=True)

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

    def action_approve(self):
        for rec in self:
            rec.stage = 'approved'

            # Update the related part's stage to 'approved'
            if rec.part_id:
                rec.part_id.status = 'customer_approved'

    def action_reject(self):
        for rec in self:
            rec.stage = 'rejected'

            # Update the related part's stage back to 'draft' when rejected
            if rec.part_id:
                rec.part_id.status = 'rejected'

    is_fully_paid = fields.Boolean(string='Fully Paid')

class PaymentTransactions(models.Model):
    _inherit = 'payment.transaction'

    def _create_invoice_from_payment(self, tx):
        """Extend parent logic to also update part/customer approval and notify ticket assignees."""

        super(PaymentTransactions, self)._create_invoice_from_payment(tx)

        for order in tx.sale_order_ids:
            ticket = getattr(order, 'ticket_id', False)
            part = getattr(order, 'part_id', False)

            if not ticket or not part:
                continue

            # Find related customer approval notification
            notification = self.env['part.customer.approval.notification'].sudo().search([
                ('task_id', '=', ticket.id),
                ('part_id', '=', part.id)
            ], limit=1)
            parts_notification = self.env['part.approval.notification'].sudo().search([
                ('task_id', '=', ticket.id),
                ('part_id', '=', part.id)
            ], limit=1)

            part_name = (
                part.product_id.display_name
                if part and part.product_id
                else part.part_name or _('Unnamed Part')
            )

            invoices = order.invoice_ids.filtered(lambda i: i.state == 'posted')
            for inv in invoices:
                inv._compute_amount()

                assignees = ticket.user_ids
                dept_manager_partner = False
                if getattr(ticket, 'department_id', False) and ticket.department_id.manager_id:
                    dept_manager_user = ticket.department_id.manager_id.user_id
                    if dept_manager_user and dept_manager_user.partner_id:
                        dept_manager_partner = dept_manager_user.partner_id

                # Combine all partners for notification
                notify_partners = assignees.mapped('partner_id')
                if dept_manager_partner:
                    notify_partners |= dept_manager_partner

                # === CASE 1: Partial Payment ===
                if inv.amount_residual > 0:
                    if notification:
                        notification.stage = 'partially_paid'
                        # don't update part.status yet

                        message = _(
                            "Customer has made a partial payment for ticket %s "
                            "related to part '%s'."
                        ) % (ticket.name, part_name)

                        # Notify assignees
                        if assignees:
                            try:
                                ticket.message_notify(
                                    body=message,
                                    subject=_("Partial Payment"),
                                    partner_ids=assignees.mapped('partner_id').ids,
                                    subtype_xmlid='mail.mt_note',
                                )
                                ticket.message_post(
                                    body=message,
                                    subject=_("Partial Payment"),
                                    subtype_xmlid='mail.mt_note',
                                )
                            except Exception as e:
                                _logger.exception(">>> Failed to send partial payment notification for ticket %s: %s" % (ticket.name, e))

                    continue

                # === CASE 2: Fully Paid ===
                if inv.amount_residual == 0:
                    if notification:
                        notification.stage = 'approved'
                        notification.is_fully_paid = True
                        part.status = 'customer_approved'

                        if assignees:
                            message = _(
                                "Customer has fully paid for ticket %s. "
                                "Part '%s' is now approved."
                            ) % (ticket.name, part_name)

                            try:
                                if parts_notification:
                                    parts_notification.message_notify(
                                        body=message,
                                        subject=_("Customer Payment Completed"),
                                        partner_ids=notify_partners.ids,
                                        subtype_xmlid='mail.mt_note',
                                    )
                                    ticket.message_post(
                                        body=message,
                                        subject=_("Customer Payment Completed"),
                                        subtype_xmlid='mail.mt_note',
                                    )
                                    parts_notification.message_post(
                                        body=message,
                                        subject=_("Customer Payment Completed"),
                                        subtype_xmlid='mail.mt_note',
                                    )
                            except Exception as e:
                                _logger.exception(">>> Failed to send full payment notification for ticket %s: %s" % (ticket.name, e))
