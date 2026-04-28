# Part of Eadu. See LICENSE file for full copyright and licensing details.

import uuid
from datetime import datetime, timezone

from odoo import api, fields, models

from odoo.addons.eadu.exceptions import EaduConnectionError


class EaduPartnerAny(models.Model):
    _name = "eadu.partner.any"
    _description = (
        "Link between Eadu Partners and other objects in order to know which object "
        "has which ID in which db.  Users in this db are however partners in the other "
        "db (a partner_id away)"
    )

    partner_id = fields.Many2one(
        'res.partner', 'Eadu Contact', index=True,
        help="The Eadu contact that represents the other DB",
    )
    res_model = fields.Char('Resource Model', index=True)
    res_id = fields.Integer('Resource ID', index=True)
    eadu_ident = fields.Integer('Eadu Identification')
    to_sync = fields.Boolean('To Sync')
    master_status = fields.Selection(
        [('partner', 'Partner Is Master'), ('me', 'I Am Master')],
        string='Master Status',
    )
    # Queue of outgoing RPC calls that could not be sent due to connection issues.
    # Each entry is a dict:
    # {
    #   "call_id": str (uuid4),
    #   "model": str,
    #   "method": str,
    #   "params": dict  (None values for keys that are in ident_placeholders),
    #   "queued_at": str (ISO-8601 UTC),
    #   "ident_placeholders": {
    #       "param_key": eadu_partner_any_id,          # resolves to a single int
    #       "param_key": [eadu_partner_any_id, ...],   # resolves to a list of ints
    #   },
    #   "result_key": str | null,
    #   "partner_master": bool,
    #   "post_action": str | null   ("remove_eadu_any" → unlink this record on success)
    # }
    pending_calls = fields.Json(
        'Pending Calls',
        help="Queue of outgoing RPC calls waiting to be sent to the remote partner.",
    )

    def _get_record(self):
        self.ensure_one()
        return self.env[self.res_model].browse(self.res_id)

    def _search_for_eadu_partner(self, eadu_partner, res_model, res_id):
        return self.search([
            ('partner_id', '=', eadu_partner.id),
            ('res_model', '=', res_model),
            ('res_id', '=', res_id),
        ])

    def _search_create_for_eadu_partner(self, eadu_partner, res_model, res_id, eadu_ident, partner_master=False):
        record = self._search_for_eadu_partner(eadu_partner, res_model, res_id)
        if not record:
            record = self.create({
                'partner_id': eadu_partner.id,
                'res_model': res_model,
                'res_id': res_id,
                'eadu_ident': eadu_ident,
                'master_status': 'partner' if partner_master else 'me',
            })
        else:
            record.master_status = 'partner' if partner_master else 'me'
            record.eadu_ident = eadu_ident
        return record

    # ── Queue / send helpers ─────────────────────────────────────────────────

    def _is_partner_blocked(self, eadu_partner):
        """Return True when *eadu_partner* has at least one unprocessed queued call."""
        return bool(self.search([
            ('partner_id', '=', eadu_partner.id),
            ('pending_calls', '!=', False),
        ], limit=1))

    def _send_or_queue(
        self,
        eadu_partner,
        model,
        method,
        params,
        *,
        local_model=None,
        local_res_id=None,
        eadu_any_ref=None,
        result_key=None,
        partner_master=False,
        ident_placeholders=None,
        post_action=None,
    ):
        """Send an eadu RPC call immediately, or queue it for later retry.

        When the partner's connection is blocked (a prior call failed), or when a
        dependency placeholder is still unresolved, the call is appended to the
        ``pending_calls`` list on the associated ``eadu.partner.any`` record.  A
        placeholder record with ``eadu_ident=0`` is pre-created automatically when
        *local_model* / *local_res_id* are given and no mapping exists yet.

        Parameters
        ----------
        eadu_partner : res.partner
            The eadu contact whose remote instance should be called.
        model, method : str
            The remote model and method to invoke.
        params : dict
            Call parameters.  Keys listed in *ident_placeholders* may be ``None``;
            their values are filled in from the referenced records during retry.
        local_model, local_res_id : str, int
            When provided, the ``eadu.partner.any`` mapping for this local object is
            looked up (or pre-created with ``eadu_ident=0``) and returned.
        eadu_any_ref : eadu.partner.any
            Alternative to *local_model*/*local_res_id*: attach the queued call to
            this existing record (used for update / remove calls).
        result_key : str
            Key in the response dict whose value becomes ``eadu_ident`` on success.
        partner_master : bool
            Forwarded to ``_search_create_for_eadu_partner``.
        ident_placeholders : dict
            ``{param_key: eadu_partner_any_id}`` or
            ``{param_key: [eadu_partner_any_id, ...]}``.
            Before the call is sent (either immediately or during retry), each
            placeholder is resolved to the ``eadu_ident`` of the referenced record.
        post_action : str | None
            ``"remove_eadu_any"`` → unlink the associated ``eadu.partner.any``
            record after the call succeeds (used for reaction removals).

        Returns
        -------
        eadu.partner.any | dict | None
            The ``eadu.partner.any`` record when *local_model*/*local_res_id* or
            *eadu_any_ref* is given; the raw RPC result otherwise; or ``None`` when
            ``post_action='remove_eadu_any'`` after the record is unlinked.
        """
        ident_placeholders = ident_placeholders or {}

        # Resolve the eadu.partner.any record used for queue storage.
        eadu_any = eadu_any_ref
        if not eadu_any and local_model and local_res_id:
            eadu_any = self._search_for_eadu_partner(eadu_partner, local_model, local_res_id)

        is_blocked = self._is_partner_blocked(eadu_partner)

        # Force-queue when a placeholder dependency is still unresolved, even if
        # the connection is not currently blocked.
        has_unresolved_deps = False
        if not is_blocked and ident_placeholders:
            for placeholder in ident_placeholders.values():
                ids = placeholder if isinstance(placeholder, list) else [placeholder]
                if any(not self.browse(i).eadu_ident for i in ids):
                    has_unresolved_deps = True
                    break

        if not is_blocked and not has_unresolved_deps:
            try:
                result = eadu_partner._eadu_call(model, method, params)

                # ── Success path ─────────────────────────────────────────────
                if local_model and local_res_id:
                    remote_id = result.get(result_key) if (result and result_key) else None
                    if remote_id:
                        eadu_any = self._search_create_for_eadu_partner(
                            eadu_partner, local_model, local_res_id,
                            remote_id, partner_master=partner_master,
                        )

                if post_action == 'remove_eadu_any':
                    target = eadu_any or eadu_any_ref
                    if target:
                        target.unlink()
                    return None

                return eadu_any if (local_model or local_res_id or eadu_any_ref) else result

            except EaduConnectionError:
                pass  # fall through to queue logic

        # ── Queue path ───────────────────────────────────────────────────────
        # Pre-create a placeholder eadu.partner.any when needed.
        if not eadu_any and local_model and local_res_id:
            eadu_any = self.create({
                'partner_id': eadu_partner.id,
                'res_model': local_model,
                'res_id': local_res_id,
                'eadu_ident': 0,
                'master_status': 'partner' if partner_master else 'me',
            })

        call_entry = {
            'call_id': str(uuid.uuid4()),
            'model': model,
            'method': method,
            'params': params,
            'queued_at': datetime.now(timezone.utc).isoformat(),
            'ident_placeholders': ident_placeholders,
            'result_key': result_key,
            'partner_master': partner_master,
            'post_action': post_action,
        }

        if eadu_any:
            existing = list(eadu_any.pending_calls or [])
            eadu_any.pending_calls = existing + [call_entry]

        return eadu_any

    def _retry_queued_calls(self, eadu_partner):
        """Try to resend all queued calls for *eadu_partner* in chronological order.

        Calls are processed in the order they were originally queued.  Each call's
        ``ident_placeholders`` are resolved against the current ``eadu_ident`` values
        of the referenced records before sending.  Processing stops on the first
        connection failure, leaving the remainder of the queue intact.

        Returns ``True`` when the queue is empty or fully processed, ``False``
        when the partner is still unreachable.
        """
        pending_records = self.search([
            ('partner_id', '=', eadu_partner.id),
            ('pending_calls', '!=', False),
        ])
        if not pending_records:
            return True

        # Build a globally-sorted list of (record, call_entry) tuples.
        all_calls = []
        for record in pending_records:
            for call_entry in (record.pending_calls or []):
                all_calls.append((record, call_entry))
        all_calls.sort(key=lambda x: x[1].get('queued_at', ''))

        for record, call_entry in all_calls:
            # Resolve ident_placeholders into concrete eadu_ident values.
            resolved_params = dict(call_entry.get('params') or {})
            can_resolve = True

            for param_key, placeholder in (call_entry.get('ident_placeholders') or {}).items():
                if isinstance(placeholder, list):
                    resolved_list = []
                    for any_id in placeholder:
                        dep = self.browse(any_id)
                        if not dep.eadu_ident:
                            can_resolve = False
                            break
                        resolved_list.append(dep.eadu_ident)
                    if not can_resolve:
                        break
                    resolved_params[param_key] = resolved_list
                else:
                    dep = self.browse(placeholder)
                    if not dep.eadu_ident:
                        can_resolve = False
                        break
                    resolved_params[param_key] = dep.eadu_ident

            if not can_resolve:
                # A dependency is still unresolved; stop to preserve ordering.
                return False

            try:
                result = eadu_partner._eadu_call(
                    call_entry['model'], call_entry['method'], resolved_params,
                )
            except EaduConnectionError:
                return False  # Still unreachable; try again later.

            # Update eadu_ident on success when applicable.
            result_key_name = call_entry.get('result_key')
            if result and result_key_name and result.get(result_key_name):
                record.eadu_ident = result[result_key_name]
                record.master_status = 'partner' if call_entry.get('partner_master') else 'me'

            # Remove this specific call from the queue.
            call_id = call_entry.get('call_id')
            remaining = [
                c for c in (record.pending_calls or [])
                if c.get('call_id') != call_id
            ]
            record.pending_calls = remaining or False

            # Handle post-action once the record's queue is fully drained.
            if call_entry.get('post_action') == 'remove_eadu_any' and not record.pending_calls:
                record.unlink()

        return True

    def _retry_all_queued_calls(self):
        """Retry queued calls for every blocked eadu partner.  Called by the cron."""
        blocked_partners = self.search([
            ('pending_calls', '!=', False),
        ]).mapped('partner_id')
        for eadu_partner in blocked_partners:
            self._retry_queued_calls(eadu_partner)

    def _model_fields_mapping(self):
        return {
            'res.partner': ['name', 'email', 'phone', 'function', 'street', 'street2', 'zip', 'city', 'state_id', 'country_id', 'company_id'],
            # Include attachment_ids so attachment writes mark record for sync
            'mail.message': ['body', 'attachment_ids'],
            'ir.attachment': ['name', 'mimetype', 'datas', 'res_model', 'res_id'],
            'mail.message.reaction': ['content'],
        }
                
    def _sync_with_others(self, res_model, res_id, vals):
        eadu_anys = self.search([('res_model', '=', res_model), ('res_id', '=', res_id)]) 
        if eadu_anys and vals.keys() & set(self._model_fields_mapping().get(res_model, [])):
            eadu_anys.to_sync = True    
        # Trigger cron
        self.env.ref('eadu.ir_cron_process_eadu_synchronize').sudo()._trigger()
         

    def _synchronize(self):
        """Called by the cron to push pending field changes to remote instances."""
        to_sync = self.search([('to_sync', '=', True)])
        for eadu_any in to_sync:
            # Skip records that have never been successfully sent to the remote.
            if not eadu_any.eadu_ident:
                eadu_any.to_sync = False
                continue

            record = eadu_any._get_record()
            if not record:
                continue
            vals = {}
            for field in self._model_fields_mapping().get(eadu_any.res_model, []):
                vals[field] = record[field]
                if self.env[eadu_any.res_model]._fields[field].type == 'many2one':
                    vals[field] = vals[field].id if vals[field] else False
                if field == "datas":
                    vals[field] = vals[field].decode() if isinstance(vals[field], bytes) else vals[field]

            self._send_or_queue(
                eadu_any.partner_id,
                'eadu.partner.any',
                'remote_sync',
                {
                    'res_model': eadu_any.res_model,
                    'res_id': eadu_any.eadu_ident,
                    'vals': vals,
                },
                eadu_any_ref=eadu_any,
            )
            eadu_any.to_sync = False
        
    def remote_sync(self, res_model, res_id, vals):
        """ Called from remote to update the record """
        eadu_contact = self.env.user.partner_id
        eadu_any = self._search_for_eadu_partner(eadu_contact, res_model, res_id)
        if eadu_any:
            record = eadu_any._get_record()
            if record:
                record.sudo().with_context(eadu_message=True).write(vals) # need to think security here