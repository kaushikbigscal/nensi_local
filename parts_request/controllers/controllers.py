from odoo import http, _
from odoo.http import request
from odoo.addons.customer_app.controllers.portal import PortalHomePage
from odoo.tools import format_date
import logging

_logger = logging.getLogger(__name__)

class PortalHomeWithPartsRequest(PortalHomePage):
    
    @http.route('/my/parts/request', type='http', auth="user", website=True)
    def portal_my_parts_request(self, sortby='newest', filterby='all', groupby='', search='', **kwargs):
        """Parts Request List View with sorting, filtering and grouping"""
        user = request.env.user
        partner = user.partner_id

        # Base domain
        domain = [('task_id.partner_id', '=', partner.id),
                  ('coverage', '=', 'chargeable')]

        # Search functionality
        if search:
            search_domain = ['|', '|', '|', '|',
                             ('part_name', 'ilike', search),
                             ('product_id.name', 'ilike', search),
                             ('task_id.user_ids.name', 'ilike', search),
                             ('stage', 'ilike', search)]
            domain += search_domain

        # Sorting options
        sortings = {
            'newest': {'label': 'Newest First', 'order': 'create_date desc, id desc'},
            'oldest': {'label': 'Oldest First', 'order': 'create_date asc, id asc'},
            'product': {'label': 'Product Name', 'order': 'product_id'},
            'stage': {'label': 'Stage', 'order': 'stage'},
        }
        order = sortings.get(sortby, sortings['newest'])['order']

        # Filtering options
        filters = {
            'all': {'label': 'All', 'domain': []},
            'pending': {'label': 'Pending', 'domain': [('stage', '=', 'pending')]},
            'approved': {'label': 'Approved', 'domain': [('stage', '=', 'approved')]},
            'rejected': {'label': 'Rejected', 'domain': [('stage', '=', 'rejected')]},
            'partially_paid': {'label': 'Partially Paid', 'domain': [('stage', '=', 'partially_paid')]},
        }

        # Apply filter
        filter_domain = filters.get(filterby, filters['all'])['domain']
        if filter_domain:
            domain += filter_domain

        parts_requests = []
        if 'part.customer.approval.notification' in request.env.registry.models:
            parts_requests = request.env['part.customer.approval.notification'].sudo().search(
                domain,
                order=order
            )

        # --- Grouping ---
        grouped_requests = {}
        if groupby and groupby != 'none':
            if groupby == 'stage':
                for req in parts_requests:
                    stage_name = req.stage or "No Stage"
                    grouped_requests.setdefault(stage_name, []).append(req)

            elif groupby == 'assignee':
                for req in parts_requests:
                    if req.task_id and req.task_id.user_ids:
                        engineer_names = ', '.join(user.name for user in req.task_id.user_ids)
                        grouped_requests.setdefault(engineer_names, []).append(req)
                    else:
                        grouped_requests.setdefault("Unassigned", []).append(req)

            elif groupby == 'product':
                for req in parts_requests:
                    product_name = req.product_id.name if req.product_id else "No Product"
                    grouped_requests.setdefault(product_name, []).append(req)

            elif groupby == 'part':
                for req in parts_requests:
                    part_name = req.part_name or "No Part Name"
                    grouped_requests.setdefault(part_name, []).append(req)

        # --- Combine Filter & GroupBy for Frontend Dropdown ---
        combined_options = {}
        for key, val in filters.items():
            combined_options[f'f_{key}'] = {
                'label': f"{val['label']}",
                'filterby': key,
                'groupby': groupby,
            }

        for key, val in {
            'stage': {'label': 'Stage'},
            'assignee': {'label': 'Assignee'},
            'product': {'label': 'Product Name'},
            'part': {'label': 'Part Name'},
        }.items():
            combined_options[f'g_{key}'] = {
                'label': f"{val['label']}",
                'groupby': key,
                'filterby': filterby,
            }
        values = {
            'parts_requests': parts_requests,
            'page_name': 'parts_request',
            'sortby': sortby,
            'filterby': filterby,
            'groupby': groupby,
            'search': search,
            'search_in': 'name',
            'sortings': sortings,
            'filters': filters,
            'grouped_requests': grouped_requests,
            'searchbar_inputs': [{'input': 'name', 'label': 'Search'}],
            'searchbar_filters': filters,
            'searchbar_groupby': {
                'none': {'input': 'none', 'label': 'None'},
                'stage': {'input': 'stage', 'label': 'Stage'},
                'assignee': {'input': 'assignee', 'label': 'Assignee'},
                'task': {'input': 'task', 'label': 'Task Name'},
                'product': {'input': 'product', 'label': 'Product Name'},
                'part': {'input': 'part', 'label': 'Part Name'},
            },
            'searchbar_combined': combined_options,
            'default_url': '/my/parts/request',
        }
        return request.render("parts_request.parts_request_list_view", values)

    @http.route(['/my/view'], type='http', auth='user', website=True)
    def my_tickets(self, sortby='name', filterby='all', groupby='', search='', **kwargs):

        # Get the original render result
        response = PortalHomePage().my_tickets(sortby, filterby, groupby, search, **kwargs)

        # We can access the rendering context via qcontext if it's a TemplateResponse
        if hasattr(response, 'qcontext'):
            qcontext = response.qcontext

            calls = qcontext.get('calls')
            if calls:
                notification_model = request.env['part.approval.notification'].sudo()
                notifications = notification_model.search([('task_id', 'in', calls.ids)])

                for n in notifications:
                    part_display = 'N/A'
                    if hasattr(n, 'part_id') and n.part_id:
                        part_display = getattr(n.part_id, 'part_name', False) or \
                                       (getattr(n.part_id, 'product_id', False)
                                        and n.part_id.product_id.display_name) or \
                                       'Unknown Part'

                # Create task-notification mapping
                notifications_by_task = {n.task_id.id: n for n in notifications}
                qcontext['notifications_by_task'] = notifications_by_task

            # Finally, return updated response
            return response

        return response

    @http.route(['/my/open/ticket'], type='http', auth='user', website=True)
    def list_open_tickets(self, sortby='recent', filterby='all', groupby='', search='', **kwargs):

        # Get the original render result
        response = PortalHomePage().list_open_tickets(sortby, filterby, groupby, search, **kwargs)

        # We can access the rendering context via qcontext if it's a TemplateResponse
        if hasattr(response, 'qcontext'):
            qcontext = response.qcontext

            calls = qcontext.get('calls')
            if calls:
                notification_model = request.env['part.approval.notification'].sudo()
                notifications = notification_model.search([('task_id', 'in', calls.ids)])

                for n in notifications:
                    part_display = 'N/A'
                    if hasattr(n, 'part_id') and n.part_id:
                        part_display = getattr(n.part_id, 'part_name', False) or \
                                       (getattr(n.part_id, 'product_id', False)
                                        and n.part_id.product_id.display_name) or \
                                       'Unknown Part'

                # Create task-notification mapping
                notifications_by_task = {n.task_id.id: n for n in notifications}
                qcontext['notifications_by_task'] = notifications_by_task

            # Finally, return updated response
            return response

        return response

    @http.route(['/my/ticket/<int:ticket_id>'], type='http', auth='user', website=True)
    def view_ticket(self, ticket_id, **kw):
        # Call the existing controller logic directly
        response = PortalHomePage().view_ticket(ticket_id, **kw)

        # We can access the rendering context via qcontext if it's a TemplateResponse
        if hasattr(response, 'qcontext'):
            qcontext = response.qcontext

            ticket = qcontext.get('ticket')
            if ticket:
                notification_model = request.env['part.approval.notification'].sudo()
                notifications = notification_model.search([('task_id', '=', ticket.id)])
                
                for n in notifications:
                    part_display = 'N/A'
                    if hasattr(n, 'part_id') and n.part_id:
                        part_display = getattr(n.part_id, 'part_name', False) or \
                                       (getattr(n.part_id, 'product_id', False)
                                        and n.part_id.product_id.display_name) or \
                                       'Unknown Part'

                # Create task-notification mapping but don't add receive button logic
                notifications_by_task = {n.task_id.id: n for n in notifications}
                qcontext['notifications_by_task'] = notifications_by_task

            # Finally, return updated response
            return response
        return response

    @http.route('/part/receive/all/<int:notification_id>', type='http', auth='user', website=True)
    def receive_all_parts(self, notification_id, **kw):
        """Handle the Receive button logic for ALL parts in a task"""

        notification = request.env['part.approval.notification'].sudo().browse(notification_id)
        if not notification.exists():
            return request.not_found()

        task = notification.task_id
        if not task:
            return request.not_found()

        # Fetch all related notifications for this task
        all_notifications = request.env['part.approval.notification'].sudo().search([
            ('task_id', '=', task.id)
        ])

        # Mark all related parts + notifications as received
        for n in all_notifications:
            n.status = 'received'
            if n.part_id:
                n.part_id.status = 'received'

        # Post one summary message in task chatter
        task.message_post(
            body=_("Customer received all parts for this ticket.")
        )

        # Notify supervisor (if exists)
        supervisor_user = None
        if task.department_id:
            supervisor_user = task.department_id.manager_id.user_id

        if supervisor_user:
            message_body = _(
                "All parts for ticket %s have been marked as Received by the customer %s."
            ) % (task.name, task.partner_id.name)
            task.message_notify(
                subject=f"Customer Received - {task.name}",
                body=message_body,
                partner_ids=[supervisor_user.partner_id.id],
                subtype_xmlid='mail.mt_note',
            )

        # Redirect back smartly
        referrer = request.httprequest.referrer or ''
        if '/my/ticket/' in referrer:
            return request.redirect(referrer)
        else:
            return request.redirect('/my/view')

    @http.route('/part/receive/form/<int:part_id>', type='http', auth='user', website=True, methods=['POST'])
    def received_parts(self, part_id, **kw):
        """
        When user clicks 'Receive' button for a specific part,
        update its status, post message in related task, and sync notification.
        """

        # Fetch the part from project.task.part
        part = request.env['project.task.part'].sudo().browse(part_id)
        if not part.exists():
            return request.not_found()

        part.sudo().write({'status': 'received'})

        # Find related notification (optional)
        notification = request.env['part.approval.notification'].sudo().search([('part_id', '=', part.id)], limit=1)
        if notification:
            notification.sudo().write({'status': 'received'})

        # Fetch related task
        task = part.task_id
        if task:
            part_name = part.product_id.display_name or _("Unnamed Part")

            partner_ids = []

            # Add all assignees
            if task.user_ids:
                assignee_partners = task.user_ids.mapped('partner_id.id')
                partner_ids.extend(assignee_partners)

            # Add supervisor (if exists)
            supervisor_user = task.department_id.manager_id.user_id if task.department_id and task.department_id.manager_id else None
            if supervisor_user and supervisor_user.partner_id:
                partner_ids.append(supervisor_user.partner_id.id)

            # Remove duplicates
            partner_ids = list(set(partner_ids))

            if partner_ids:
                notification.message_notify(
                    subject=f"Customer Received - {task.name} ({part_name})",
                    body=_("Part %s for ticket %s has been marked as Received by %s.")
                        % (part_name, task.name, task.partner_id.name),
                    partner_ids=partner_ids,
                    subtype_xmlid='mail.mt_note',
                )
                notification.message_post(
                    subject=f"Customer Received - {task.name} ({part_name})",
                    body=_("Part %s for ticket %s has been marked as Received by %s.")
                         % (part_name, task.name, task.partner_id.name),
                    subtype_xmlid='mail.mt_note',
                )

        return request.redirect(request.httprequest.referrer or '/my/view')

    @http.route('/my/parts/request/<int:request_id>/approve', type='http', auth="user", website=True, methods=['POST'],
                csrf=True)
    def parts_request_approve(self, request_id, **kwargs):
        """Approve a parts request"""
        partner = request.env.user.partner_id
        part_request = request.env['part.customer.approval.notification'].sudo().browse(request_id)
        if not part_request.exists():
            return request.redirect('/my/parts/request')
        if part_request.task_id.partner_id != partner:
            return request.redirect('/my/parts/request')
        part_name = part_request.part_name
        if part_name:
            product_template = request.env['product.template'].sudo().search([
                ('name', '=', part_name),
                ('is_part', '=', True)
            ], limit=1)
            task = part_request.task_id
            part = part_request.part_id
            quotation = request.env['sale.order'].sudo().search([
                ('ticket_id', '=', task.id),
                ('part_id','=',part.id)
            ], limit=1)
        if product_template and product_template.is_part:
            if product_template.payment_required_first:
                # Case 1: Payment required first redirect to quotation
                if quotation:
                    return request.redirect(f'/my/orders/{quotation.id}')
                else:
                    # No quotation yet — optional fallback
                    return request.redirect('/my/parts/request')
            else:
                part_request.action_approve()

                # Case 2: No payment required notify supervisor
                supervisor = task.department_id.manager_id if task.department_id else False
                assignees = task.user_ids

                partner_ids = assignees.mapped('partner_id').ids
                if supervisor and supervisor.user_id and supervisor.user_id.partner_id:
                    partner_ids.append(supervisor.user_id.partner_id.id)

                if supervisor:
                    message = _(
                        "Customer %s has approved a parts request for part '%s'."
                    ) % (partner.name, part_name)
                    task.message_post(
                        body=message,
                        subject="Customer Approved",
                        partner_ids=partner_ids,
                        message_type='notification',
                        subtype_xmlid='mail.mt_comment',
                    )

        return request.redirect('/my/parts/request')

    @http.route('/my/parts/request/<int:request_id>/reject', type='http', auth="user", website=True, methods=['POST'],
                csrf=True)
    def parts_request_reject(self, request_id, **kwargs):
        """Reject a parts request"""
        partner = request.env.user.partner_id
        part_request = request.env['part.customer.approval.notification'].sudo().browse(request_id)
        if not part_request.exists():
            return request.redirect('/my/parts/request')
        if part_request.task_id.partner_id != partner:
            return request.redirect('/my/parts/request')

        # Reject the part request
        part_request.action_reject()

        task = part_request.task_id
        part = part_request.part_id
        part_name = part_request.part_name

        # Notify task assignees
        if task and task.user_ids:
            message = _(
                "Customer %s has rejected a parts request for part '%s'."
            ) % (partner.name, part_name)
            task.message_notify(
                body=message,
                subject="Customer Rejected",
                partner_ids=task.user_ids.mapped('partner_id').ids,
                subtype_xmlid='mail.mt_note',
            )
            task.message_post(
                body=message,
                subject="Customer Rejected",
                subtype_xmlid='mail.mt_note',
            )

        # Cancel quotation
        quotation = request.env['sale.order'].sudo().search([
            ('ticket_id', '=', task.id),
            ('part_id','=',part.id)
        ], limit=1)
        if quotation and quotation.state not in ('cancel', 'done'):
            quotation.sudo().action_cancel()
            quotation.sudo().write({'state': 'cancel'})
            if task.task_part_ids:
                target_part = task.task_part_ids.filtered(lambda p: p.id == part.id)
                if target_part:
                    target_part.sudo().write({'has_cancelled_quotation': True})

        return request.redirect('/my/parts/request')

    @http.route('/my/parts/request/<int:request_id>/pay', type='http', auth="user", website=True, methods=['POST'], csrf=True)
    def parts_request_pay(self, request_id, **kwargs):
        """Redirect to Quotation for approved part"""
        part_request = request.env['part.customer.approval.notification'].sudo().browse(request_id)
        if not part_request.exists():
            return request.redirect('/my/parts/request')

        task = part_request.task_id
        part = part_request.part_id
        part_name = part_request.part_name

        if not task:
            return request.redirect('/my/parts/request')

        # Find quotation linked to this task
        quotation = request.env['sale.order'].sudo().search([
            ('ticket_id', '=', task.id),
            ('part_id', '=', part.id)
        ], limit=1)


        # Redirect to quotation if exists
        if quotation:
            return request.redirect(f'/my/orders/{quotation.id}')
        else:
            # Fallback redirect if no quotation found
            return request.redirect('/my/parts/request')

    @http.route('/my/parts/request/<int:request_id>/partial_pay', type='http', auth="user", website=True, methods=['POST'], csrf=True)
    def parts_request_partial_pay(self, request_id, **kwargs):
        """Redirect customer to the correct unpaid invoice for the ticket-part quotation."""
        request_rec = request.env['part.customer.approval.notification'].sudo().browse(request_id)
        if not request_rec.exists():
            return request.redirect('/my/parts/requests')

        # Step 1: Get the sale order linked to the same ticket and part
        sale_order = request.env['sale.order'].sudo().search([
            ('ticket_id', '=', request_rec.task_id.id),
            ('part_id', '=', request_rec.part_id.id)
        ], limit=1)

        if not sale_order:
            return request.redirect('/my/parts/requests')

        # Step 2: Find the posted (open) invoice linked to this sale order
        invoices = sale_order.invoice_ids.filtered(lambda i: i.state == 'posted')

        for inv in invoices:
            inv._compute_amount()

            # Step 3: If invoice has remaining balance, redirect to it
            if inv.amount_residual > 0:
                inv._portal_ensure_token()
                url = f"/my/invoices/{inv.id}?access_token={inv.access_token}"
                return request.redirect(url)

        # Step 4: Fallback — if no open invoice, go to the quotation directly
        order_url = sale_order.get_portal_url() or '/my/orders'
        return request.redirect(order_url)


class PaymentRedirectController(http.Controller):

    @http.route(['/payment/status'], type='http', auth='public', website=True, csrf=False)
    def payment_status_redirect(self, **post):

        tx = request.env['payment.transaction'].sudo().search([], order='id desc', limit=1)
        if not tx:
            return request.redirect('/my')

        # ---finalize post-processing (this reconciles the payment) ---
        try:
            tx._finalize_post_processing()
        except Exception as e:
            _logger.exception(f">>> [ERROR] Finalizing transaction failed: {e}")

        # --- CASE 1: Sale Order ---
        if tx.sale_order_ids:
            for order in tx.sale_order_ids:
                invoices = order.invoice_ids
                remaining = invoices.filtered(lambda inv: inv.amount_residual > 0)
                if remaining:
                    inv = remaining[0]
                    return request.redirect(f"/my/invoices/{inv.id}?access_token={inv.access_token}")

                return request.redirect(f"/my/orders/{order.id}?access_token={order.access_token}")

        # --- CASE 2: Direct Invoice ---
        elif tx.invoice_ids:

            for inv in tx.invoice_ids:

                # Ensure DB changes from payment are committed
                request.env.cr.commit()

                # Reload invoice and recompute
                inv = request.env['account.move'].sudo().browse(inv.id)
                inv._compute_amount()

                try:
                    self._handle_invoice_payment(inv)
                except Exception as e:
                    _logger.exception(f">>> [ERROR] Custom post-payment logic failed: {e}")

                if inv.amount_residual > 0:
                    return request.redirect(f"/my/invoices/{inv.id}?access_token={inv.access_token}")
                else:
                    return request.redirect(f"/my/invoices/{inv.id}?access_token={inv.access_token}")

        return request.redirect('/my')

    def _handle_invoice_payment(self, invoice):
        """Handles logic only if invoice came from quotation linked to a task."""

        sale_order = request.env['sale.order'].sudo().search([
            ('name', '=', invoice.invoice_origin)
        ], limit=1)

        if not sale_order:
            return

        task = getattr(sale_order, 'ticket_id', False)
        part = getattr(sale_order, 'part_id', False)
        parts_notification = request.env['part.approval.notification'].sudo().search([
            ('task_id', '=', task.id),
            ('part_id', '=', part.id)
        ], limit=1)

        part_name = (
            part.product_id.display_name
            if part and part.product_id
            else part.part_name or _('Unnamed Part')
        )

        if not task:
            return


        # Notify payment info
        paid_amount = invoice.amount_total - invoice.amount_residual
        task.message_post(
            # body=f"Customer paid {paid_amount} / {invoice.amount_total} for {invoice.name}.",
            body=f"Customer has fully paid for ticket {task.name}. Part {part_name} is now approved.",
            subject="Customer Payment Update",
            subtype_xmlid='mail.mt_note',
        )

        # Handle full or partial
        if invoice.amount_residual == 0:
            notif = request.env['part.customer.approval.notification'].sudo().search([
                ('task_id', '=', task.id),
                ('part_id', '=', part.id)
            ], limit=1)

            if notif:
                notif.stage = 'approved'
                # sale_order.part_id.status = 'customer_approved'
                notif.is_fully_paid = True
                part.status = 'customer_approved'

            # Notify users
            partner_ids = task.user_ids.mapped('partner_id').ids
            if task.department_id.manager_id and task.department_id.manager_id.user_id:
                partner_ids.append(task.department_id.manager_id.user_id.partner_id.id)

            parts_notification.message_notify(
                body=f"Customer has fully paid for ticket {task.name}. Part {part_name} is now approved.",
                subject="Full Payment Completed",
                partner_ids=partner_ids,
                subtype_xmlid='mail.mt_note',
            )
            parts_notification.message_post(
                body=f"Customer has fully paid for ticket {task.name}. Part {part_name} is now approved.",
                subject="Full Payment Completed",
                subtype_xmlid='mail.mt_note',
            )
        else:
            task.message_post(
                body=f"Partial payment received for {invoice.name}. Remaining {invoice.amount_residual}.",
                subject="Partial Payment",
                subtype_xmlid='mail.mt_note',
            )
