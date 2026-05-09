/** @odoo-module **/
// Part of Eadu. See LICENSE file for full copyright and licensing details.
//
// Patches Composer.processMessage to convert pending eadu plain-text link
// markers into real <a> tags when the message is submitted.
// All suggestion/detection/insertion logic has moved to:
//   eadu/static/src/mention/eadu_mention_core.js    (core patches)
//   eadu/static/src/mention/eadu_mention_builtin.js (partner + attachment)
//   eadu_product/static/src/mention/eadu_product_mention.js (product)

import { Composer } from "@mail/core/common/composer";
import { patch } from "@web/core/utils/patch";
import { htmlReplace } from "@web/core/utils/html";
import { htmlEscape, markup } from "@odoo/owl";

patch(Composer.prototype, {
    /**
     * Before posting the message, replace every entry in
     * composer._eaduPendingLinks with a proper <a data-oe-*> element in the
     * HTML body (plain-text composer path only).
     */
    async processMessage(cb) {
        const composer = this.props.composer;
        const pending = composer._eaduPendingLinks ? [...composer._eaduPendingLinks] : [];
        return super.processMessage(async (body) => {
            for (const { href, label, oeModel, oeId } of pending) {
                const safeHref = htmlEscape(href);
                const safeLabel = htmlEscape(label);
                const modelAttr = oeModel
                    ? ` data-oe-model="${htmlEscape(oeModel)}" data-oe-id="${oeId}"`
                    : "";
                const link = markup(
                    `<a href="${safeHref}"${modelAttr} target="_blank">${safeLabel}</a>`
                );
                body = htmlReplace(body, safeLabel, link);
            }
            return cb(body);
        });
    },
});
