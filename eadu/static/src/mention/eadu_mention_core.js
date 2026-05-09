/** @odoo-module **/
// Part of Eadu. See LICENSE file for full copyright and licensing details.
//
// Patches SuggestionService, Composer, and UseSuggestion to support every
// mention type registered in eaduMentionRegistry.  Individual feature
// modules (eadu, eadu_product, …) only need to add entries to the registry;
// they do NOT need to re-patch anything.

import { eaduMentionRegistry } from "@eadu/mention/eadu_mention_registry";
import { SuggestionService } from "@mail/core/common/suggestion_service";
import { UseSuggestion } from "@mail/core/common/suggestion_hook";
import { Composer } from "@mail/core/common/composer";
import { patch } from "@web/core/utils/patch";
import { router } from "@web/core/browser/router";

// Template used for virtual entries inside the "/" command palette.
const EADU_SLASH_CMD_TEMPLATE = "eadu.Composer.suggestionEaduCommand";

// ── SuggestionService patch ───────────────────────────────────────────────

patch(SuggestionService.prototype, {
    getSupportedDelimiters(thread, env) {
        const result = super.getSupportedDelimiters(thread, env);
        // Insert each registered delimiter immediately *before* "/" so the
        // longer/more-specific prefix wins (see note in suggestion_hook.js:
        // the "longer wins" comparison is broken because it compares a number
        // to a string, so ordering is the only reliable strategy).
        const slashIdx = result.findIndex(([d]) => d === "/");
        for (const entry of eaduMentionRegistry.values()) {
            const item = [entry.delimiter, undefined, 0];
            if (slashIdx >= 0) {
                result.splice(slashIdx, 0, item);
            } else {
                result.push(item);
            }
        }
        return result;
    },

    async fetchSuggestions({ delimiter, term }, options) {
        const entry = this._eaduEntryForDelimiter(delimiter);
        if (!entry) {
            return super.fetchSuggestions(...arguments);
        }
        const items = await entry.fetch(this.orm, term);
        this._eaduCache = this._eaduCache || {};
        this._eaduCache[delimiter] = items;
    },

    searchSuggestions({ delimiter, term }, options) {
        const entry = this._eaduEntryForDelimiter(delimiter);
        if (entry) {
            const cached = (this._eaduCache || {})[delimiter] || [];
            return {
                type: `eadu:${delimiter}`,
                suggestions: entry.search(cached, term),
            };
        }
        const result = super.searchSuggestions(...arguments);
        // Append virtual eadu commands to the "/" channel-command list.
        if (delimiter === "/" && result?.type === "ChannelCommand") {
            const matching = [...eaduMentionRegistry.values()].filter((e) =>
                e.name.toLowerCase().includes(term.toLowerCase())
            );
            if (matching.length) {
                result.suggestions = [
                    ...result.suggestions,
                    ...matching.map((e) => ({ _eadu: e, name: e.name, help: e.help })),
                ];
            }
        }
        return result;
    },

    /** @private */
    _eaduEntryForDelimiter(delimiter) {
        for (const entry of eaduMentionRegistry.values()) {
            if (entry.delimiter === delimiter) {
                return entry;
            }
        }
        return null;
    },
});

// ── Composer patch ────────────────────────────────────────────────────────

patch(Composer.prototype, {
    get navigableListProps() {
        const base = super.navigableListProps;
        const items = this.suggestion?.state.items;
        if (!items) {
            return base;
        }

        // Active eadu delimiter → show its suggestion list.
        if (typeof items.type === "string" && items.type.startsWith("eadu:")) {
            const delimiter = items.type.slice(5); // strip "eadu:"
            const entry = [...eaduMentionRegistry.values()].find(
                (e) => e.delimiter === delimiter
            );
            if (entry) {
                return {
                    ...base,
                    optionTemplate: entry.optionTemplate,
                    options: items.suggestions.map((item) => entry.buildOption(item)),
                };
            }
        }

        // "/" command list: rebuild options so that eadu virtual commands
        // carry their _eadu reference and use the dedicated template, while
        // regular channel commands keep the stock template.
        if (
            items.type === "ChannelCommand" &&
            items.suggestions.some((s) => s._eadu)
        ) {
            return {
                ...base,
                options: items.suggestions.map((s) => ({
                    label: s.name,
                    help: s.help,
                    _eadu: s._eadu, // undefined for regular channel commands
                    classList: "o-mail-Composer-suggestion",
                    optionTemplate: s._eadu ? EADU_SLASH_CMD_TEMPLATE : "mail.Composer.suggestionChannelCommand",
                })),
            };
        }

        return base;
    },
});

// ── UseSuggestion patch ───────────────────────────────────────────────────

patch(UseSuggestion.prototype, {
    insert(option) {
        // Virtual "/" command: expand to the full delimiter so detect()
        // immediately activates the secondary suggestion list.
        if (option._eadu) {
            const expandTo = option._eadu.delimiter;
            const pos = this.search.position;
            if (this.comp.composerService.htmlEnabled) {
                const { startContainer, endContainer, endOffset } =
                    this.comp.editor.shared.selection.getEditableSelection();
                this.comp.editor.shared.selection.setSelection({
                    anchorNode: startContainer,
                    anchorOffset: pos,
                    focusNode: endContainer,
                    focusOffset: endOffset,
                });
                const textNode = document.createTextNode(expandTo);
                this.comp.editor.shared.dom.insert(textNode);
                this.comp.editor.shared.selection.setSelection({
                    anchorNode: textNode,
                    anchorOffset: textNode.length,
                });
                this.comp.editor.shared.history.addStep();
            } else {
                this.composer.composerText =
                    this.composer.composerText.substring(0, pos) +
                    this.composer.composerText.substring(this.composer.selection.end);
                this.clearSearch();
                this.composer.insertText(expandTo, pos);
            }
            return;
        }

        // Delegate to the registered entry's insert handler if applicable.
        if (option._eaduModel) {
            const entry = eaduMentionRegistry.get(option._eaduModel);
            if (entry) {
                entry.insert(option, {
                    comp: this.comp,
                    search: this.search,
                    composer: this.composer,
                    isHtml: this.comp.composerService.htmlEnabled,
                    router,
                });
                this.clearSearch();
                return;
            }
        }

        return super.insert(...arguments);
    },
});
