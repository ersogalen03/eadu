/** @odoo-module **/
// Part of Eadu. See LICENSE file for full copyright and licensing details.
//
// Central registry for eadu composer mention types.
//
// Any module that wants to add a "/xxx:" delimiter to the composer simply
// adds an entry here:
//
//   import { eaduMentionRegistry } from "@eadu/mention/eadu_mention_registry";
//   eaduMentionRegistry.add("product:", { ... });
//
// The core patching (suggestion_service + UseSuggestion + Composer) in
// eadu_mention_core.js reads this registry automatically — no further
// patching is required in feature modules.
//
// Entry shape:
// {
//   name: string          – displayed in the "/" command list (e.g. "product:")
//   help: string          – one-line description shown next to the name
//   delimiter: string     – full delimiter including leading slash (e.g. "/product:")
//   optionTemplate: string – QWeb template name for a suggestion row
//   async fetch(orm, term) → Array<{id, name, ...}>
//                          – called to load remote suggestions
//   search(cached, term) → Array<{id, name, ...}>
//                          – synchronous filter on the cached list
//   buildOption(item) → Option
//                          – maps a suggestion item to a NavigableList option
//   insert(option, ctx)    – called when the user picks a suggestion;
//                            ctx = { comp, search, composer, isHtml, router }
// }

export const eaduMentionRegistry = new Map();
