/** @odoo-module **/
// Part of Eadu. See LICENSE file for full copyright and licensing details.
//
// Registers the built-in eadu mention types that live in the base eadu module:
//   /partner:   – mention a res.partner and insert an oe link
//   /attachment: – mention an ir.attachment and insert a download link

import { eaduMentionRegistry } from "@eadu/mention/eadu_mention_registry";
import { cleanTerm } from "@mail/utils/common/format";
import { router } from "@web/core/browser/router";

// ── Shared insert helper ──────────────────────────────────────────────────

/**
 * Generic insert for both plain-text and HTML composers.
 * Replaces the typed delimiter+term with a clickable <a> link.
 */
function insertLink(option, { comp, search, composer, isHtml, href, label }) {
    const insertPos = search.position;
    if (isHtml) {
        const link = document.createElement("a");
        link.setAttribute("href", href);
        if (option._oeModel) {
            link.setAttribute("data-oe-model", option._oeModel);
            link.setAttribute("data-oe-id", String(option._oeId));
        }
        link.setAttribute("target", "_blank");
        link.setAttribute("contenteditable", "false");
        link.textContent = label;

        const { startContainer, endContainer, endOffset } =
            comp.editor.shared.selection.getEditableSelection();
        comp.editor.shared.selection.setSelection({
            anchorNode: startContainer,
            anchorOffset: insertPos,
            focusNode: endContainer,
            focusOffset: endOffset,
        });
        comp.editor.shared.dom.insert(link);
        const space = document.createTextNode("\u00A0");
        link.after(space);
        comp.editor.shared.selection.setSelection({ anchorNode: space, anchorOffset: 1 });
        comp.editor.shared.history.addStep();
    } else {
        composer.composerText =
            composer.composerText.substring(0, insertPos) +
            composer.composerText.substring(composer.selection.end);
        if (!composer._eaduPendingLinks) {
            composer._eaduPendingLinks = [];
        }
        composer._eaduPendingLinks.push({ href, label, oeModel: option._oeModel, oeId: option._oeId });
        composer.insertText(`${label} `, insertPos);
    }
}

// ── /partner: ─────────────────────────────────────────────────────────────

eaduMentionRegistry.set("partner:", {
    name: "partner:",
    help: "Insert a partner link",
    delimiter: "/partner:",
    optionTemplate: "eadu.Composer.suggestionPartnerMention",

    async fetch(orm, term) {
        const results = await orm.silent.call("res.partner", "name_search", [], {
            name: term,
            limit: 8,
        });
        return results.map(([id, name]) => ({ id, name }));
    },

    search(cached, term) {
        const cleaned = cleanTerm(term);
        return cached.filter((p) => cleanTerm(p.name).includes(cleaned));
    },

    buildOption(item) {
        return {
            label: item.name,
            _eaduModel: "partner:",
            _oeModel: "res.partner",
            _oeId: item.id,
            classList: "o-mail-Composer-suggestion",
        };
    },

    insert(option, ctx) {
        const href = ctx.router.stateToUrl({ model: "res.partner", resId: option._oeId });
        insertLink(option, { ...ctx, href, label: option.label });
    },
});

// ── /attachment: ──────────────────────────────────────────────────────────

eaduMentionRegistry.set("attachment:", {
    name: "attachment:",
    help: "Insert an attachment link",
    delimiter: "/attachment:",
    optionTemplate: "eadu.Composer.suggestionAttachmentMention",

    async fetch(orm, term) {
        const results = await orm.silent.searchRead(
            "ir.attachment",
            [["name", "ilike", term || ""]],
            ["id", "name", "mimetype"],
            { limit: 8 }
        );
        return results;
    },

    search(cached, term) {
        const cleaned = cleanTerm(term);
        return cached.filter((a) => cleanTerm(a.name).includes(cleaned));
    },

    buildOption(item) {
        return {
            label: item.name,
            mimetype: item.mimetype,
            _eaduModel: "attachment:",
            _oeModel: "ir.attachment",
            _oeId: item.id,
            classList: "o-mail-Composer-suggestion",
        };
    },

    insert(option, ctx) {
        const href = `/web/content/${option._oeId}?download=true`;
        insertLink(option, { ...ctx, href, label: option.label });
    },
});
