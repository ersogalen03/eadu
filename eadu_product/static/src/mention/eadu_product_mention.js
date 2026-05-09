/** @odoo-module **/
// Part of Eadu. See LICENSE file for full copyright and licensing details.
//
// Registers the /product: mention type into the eadu composer mention registry.
// All patching is already done by eadu's eadu_mention_core.js; this file only
// adds the entry.

import { eaduMentionRegistry } from "@eadu/mention/eadu_mention_registry";
import { cleanTerm } from "@mail/utils/common/format";
import { router } from "@web/core/browser/router";

eaduMentionRegistry.set("product:", {
    name: "product:",
    help: "Insert a product link",
    delimiter: "/product:",
    optionTemplate: "eadu_product.Composer.suggestionProductMention",

    async fetch(orm, term) {
        const results = await orm.silent.call("product.template", "name_search", [], {
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
            _eaduModel: "product:",
            _oeModel: "product.template",
            _oeId: item.id,
            classList: "o-mail-Composer-suggestion",
        };
    },

    insert(option, { comp, search, composer, isHtml }) {
        const insertPos = search.position;
        const { _oeId: id, label: name } = option;
        if (isHtml) {
            const url = router.stateToUrl({ model: "product.template", resId: id });
            const link = document.createElement("a");
            link.setAttribute("href", url);
            link.setAttribute("data-oe-model", "product.template");
            link.setAttribute("data-oe-id", String(id));
            link.setAttribute("target", "_blank");
            link.setAttribute("contenteditable", "false");
            link.textContent = name;

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
            composer._eaduPendingLinks.push({
                href: router.stateToUrl({ model: "product.template", resId: id }),
                label: name,
                oeModel: "product.template",
                oeId: id,
            });
            composer.insertText(`${name} `, insertPos);
        }
    },
});
