/*!
 * VeriProof AI — i18n engine (vanilla JS, no build step).
 *
 * Single runtime source of truth for the active UI language. Reads the
 * translation dictionary from ``window.__VP_I18N__`` (static/i18n/messages.js)
 * and applies it to the DOM via ``data-i18n*`` attributes, with a ``t()``
 * helper for strings injected by JS (shell.js / workspace.js / …).
 *
 * Responsibilities (clean separation — engine knows nothing about the switcher
 * UI; the switcher depends on the engine):
 *  - ``getLocale()``  active locale ("en" | "ko").
 *  - ``setLocale(lang)``  validate → write cookie → set <html lang> → re-apply
 *    all ``[data-i18n*]`` → dispatch ``vp:language-change`` so dynamic surfaces
 *    re-render. DB persistence is intentionally NOT done here: the workspace
 *    account modal commits to ``UserPreference.language`` via its existing
 *    atomic ``/accounts/preferences/`` POST (sending all three fields avoids
 *    clobbering display_name / creator_wallet). Anonymous users persist via the
 *    cookie alone, which the ``vp_language`` context processor honours for SSR.
 *  - ``t(key, vars)``  resolve key in current locale (fallback en, then the key
 *  itself) with ``{var}`` interpolation.
 *  - ``applyTranslations(root)``  scan ``[data-i18n]`` (textContent),
 *    ``[data-i18n-html]`` (innerHTML), ``[data-i18n-placeholder]``,
 *    ``[data-i18n-aria-label]``, ``[data-i18n-title]``; optional
 *    ``data-i18n-vars='{"var":"value"}'`` JSON supplies interpolation vars.
 *
 * Load order: ``messages.js`` then ``vp-i18n.js`` load synchronously in
 * ``<head>`` (base.html) so ``VP.i18n`` exists before any end-of-body script.
 * ``dashboard.js`` merges into ``window.VP`` (it does NOT reassign), so
 * ``VP.i18n`` survives into shell.js / page scripts.
 */
(function (global) {
    "use strict";

    var SUPPORTED = ["en", "ko"];
    var DEFAULT = "en";
    var LANG_COOKIE = "veriproof_lang";
    var EVENT = "vp:language-change";

    var currentLocale = DEFAULT;

    function dict() { return global.__VP_I18N__ || {}; }

    function isSupported(lang) { return SUPPORTED.indexOf(lang) !== -1; }

    function normalize(lang) {
        if (typeof lang !== "string") { return null; }
        lang = lang.trim().toLowerCase();
        if (isSupported(lang)) { return lang; }
        // Map regional tags (en-US, ko-KR) to the supported base.
        var base = lang.split("-")[0];
        return isSupported(base) ? base : null;
    }

    function readCookie(name) {
        var match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
        return match ? decodeURIComponent(match[1]) : null;
    }

    function writeCookie(name, value, days) {
        var maxAge = days * 86400;
        var expires = "";
        // Guard Date for very old engines (never true here, but keeps the
        // dependency surface explicit).
        if (typeof Date !== "undefined") {
            var d = new Date();
            d.setTime(d.getTime() + maxAge * 1000);
            expires = "; expires=" + d.toUTCString();
        }
        document.cookie = name + "=" + encodeURIComponent(value) +
            expires + "; max-age=" + maxAge + "; path=/; SameSite=Lax";
    }

    // Resolve the initial locale: SSR-injected var (authoritative, from the
    // vp_language context processor) → cookie → browser → default.
    function resolveLocale() {
        var ssr = normalize(global.__VP_LANG__);
        if (ssr) { return ssr; }
        var cookie = normalize(readCookie(LANG_COOKIE));
        if (cookie) { return cookie; }
        var nav = normalize(global.navigator && global.navigator.language);
        if (nav) { return nav; }
        return DEFAULT;
    }

    function interpolate(str, vars) {
        if (!vars) { return str; }
        return str.replace(/\{(\w+)\}/g, function (whole, key) {
            return Object.prototype.hasOwnProperty.call(vars, key) ? String(vars[key]) : whole;
        });
    }

    function warnMissing(key, locale) {
        // Quiet in production-like builds would be ideal; warn for now so gaps
        // surface during development without blocking render.
        if (global.console && console.warn) {
            console.warn("[i18n] missing key \"" + key + "\" for locale \"" + locale + "\"");
        }
    }

    function lookup(key, locale) {
        var table = dict()[locale];
        return (table && Object.prototype.hasOwnProperty.call(table, key)) ? table[key] : null;
    }

    function t(key, vars) {
        var val = lookup(key, currentLocale);
        if (val == null) {
            // Fall back to the default locale, but still flag the gap.
            val = lookup(key, DEFAULT);
            warnMissing(key, currentLocale);
            if (val == null) { return key; }
        }
        return interpolate(val, vars);
    }

    function parseVars(raw) {
        if (!raw) { return null; }
        try { return JSON.parse(raw); } catch (e) { return null; }
    }

    function applyOne(root, dataAttr, prop) {
        var nodes = root.querySelectorAll("[" + dataAttr + "]");
        for (var i = 0; i < nodes.length; i++) {
            var el = nodes[i];
            var key = el.getAttribute(dataAttr);
            if (!key) { continue; }
            var vars = parseVars(el.getAttribute(dataAttr + "-vars"));
            var text = t(key, vars);
            if (prop === "textContent" || prop === "innerHTML") {
                el[prop] = text;
            } else {
                el.setAttribute(prop, text);
            }
        }
    }

    function applyTranslations(root) {
        root = root || document;
        applyOne(root, "data-i18n", "textContent");
        applyOne(root, "data-i18n-html", "innerHTML");
        applyOne(root, "data-i18n-placeholder", "placeholder");
        applyOne(root, "data-i18n-aria-label", "aria-label");
        applyOne(root, "data-i18n-title", "title");
        applyOne(root, "data-i18n-alt", "alt");
        applyOne(root, "data-i18n-content", "content");
    }

    function setLocale(lang) {
        var next = normalize(lang);
        if (!next) { return false; }
        if (next === currentLocale) { return false; }
        currentLocale = next;
        writeCookie(LANG_COOKIE, next, 365);
        document.documentElement.lang = next;
        applyTranslations(document);
        if (typeof CustomEvent === "function") {
            global.dispatchEvent(new CustomEvent(EVENT, { detail: { lang: next } }));
        }
        return true;
    }

    function getLocale() { return currentLocale; }

    // Dev safety net: log any key present in the default locale but missing in
    // another supported locale, so incomplete translations are visible without a
    // dedicated JS test runner.
    function parityCheck() {
        var messages = dict();
        var base = messages[DEFAULT] || {};
        Object.keys(SUPPORTED).forEach(function (idx) {
            var loc = SUPPORTED[idx];
            if (loc === DEFAULT) { return; }
            var table = messages[loc] || {};
            var missing = Object.keys(base).filter(function (key) {
                return !Object.prototype.hasOwnProperty.call(table, key);
            });
            if (missing.length && global.console && console.warn) {
                console.warn("[i18n] " + missing.length + " key(s) missing for \"" + loc + "\": " + missing.join(", "));
            }
        });
    }

    function init() {
        currentLocale = resolveLocale();
        document.documentElement.lang = currentLocale;
        applyTranslations(document);
        parityCheck();
    }

    // Merge into the existing window.VP namespace (dashboard.js also merges, so
    // order between them does not matter — neither reassigns VP).
    global.VP = global.VP || {};
    global.VP.i18n = {
        SUPPORTED: SUPPORTED,
        DEFAULT: DEFAULT,
        EVENT: EVENT,
        getLocale: getLocale,
        setLocale: setLocale,
        t: t,
        applyTranslations: applyTranslations,
        init: init
    };

    // Apply ASAP: if the DOM is already parsed (script at end of body), run now;
    // otherwise wait. Translations are idempotent so a later vp:language-change
    // re-run is safe.
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})(typeof window !== "undefined" ? window : this);
