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

    /**
     * 언어 코드를 정규화한다. 지원 코드(en/ko)면 그대로 두고, "en-US"/"ko-KR" 같은
     * 지역 태그는 기본 언어로 매핑하며, 어느 쪽도 아니면 null을 반환한다.
     */
    function normalize(lang) {
        if (typeof lang !== "string") { return null; }
        lang = lang.trim().toLowerCase();
        if (isSupported(lang)) { return lang; }
        // Map regional tags (en-US, ko-KR) to the supported base.
        var base = lang.split("-")[0];
        return isSupported(base) ? base : null;
    }

    /**
     * 문서 쿠키에서 지정한 이름의 값을 읽어온다. 값이 없으면 null을 반환한다.
     * @param {string} name - 쿠키 이름.
     * @returns {string|null} - URL 디코딩한 쿠키 값, 또는 null.
     */
    function readCookie(name) {
        var match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
        return match ? decodeURIComponent(match[1]) : null;
    }

    /**
     * 지정한 이름/값/수명(days)으로 쿠키를 기록한다. SameSite=Lax, path=/ 를 적용한다.
     */
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

    /**
     * "{var}" 형태의 자리표시를 vars 객체의 값으로 치환한다.
     * vars에 존재하지 않는 키는 원래 자리표시 문자열 그대로 둔다.
     */
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

    /**
     * 지정한 로케일 테이블에서 번역 키를 찾는다. 키가 없으면 null을 반환한다.
     */
    function lookup(key, locale) {
        var table = dict()[locale];
        return (table && Object.prototype.hasOwnProperty.call(table, key)) ? table[key] : null;
    }

    /**
     * 현재 로케일로 번역 키를 해석하여 {var} 보간까지 마친 문자열을 반환한다.
     * 현재 로케일에 키가 없으면 기본 로케일(en)로 폴백하고, 그래도 없으면 키 자체를 반환한다.
     * @param {string} key - 번역 키.
     * @param {Object} [vars] - {var} 보간에 사용할 변수 객체.
     * @returns {string} - 번역된 문자열.
     */
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

    /**
     * data-i18n-*-vars 속성의 JSON 문자열을 객체로 파싱한다. 빈 값이거나 파싱 실패 시 null.
     */
    function parseVars(raw) {
        if (!raw) { return null; }
        try { return JSON.parse(raw); } catch (e) { return null; }
    }

    /**
     * 단일 data-i18n 계열 속성에 대해 root 하위 노드를 순회하며 번역을 적용한다.
     * textContent/innerHTML은 직접 할당하고, 그 외(placeholder/aria-label/...)는 setAttribute로 반영한다.
     * @param {Element} root - 탐색 루트 요소.
     * @param {string} dataAttr - data-i18n 계열 속성명(예: "data-i18n-placeholder").
     * @param {string} prop - 적용할 DOM 속성/특성명(예: "placeholder").
     */
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

    /**
     * root 하위의 모든 data-i18n* 속성 노드에 현재 로케일 번역을 일괄 적용한다.
     * textContent/innerHTML/placeholder/aria-label/title/alt/content 속성을 각각 처리한다.
     * @param {Element} [root=document] - 탐색 루트. 생략 시 문서 전체.
     */
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

    /**
     * 활성 로케일을 전환한다. 정규화 → 쿠키 저장 → <html lang> 갱신 → 전체 번역 재적용 →
     * vp:language-change 이벤트 발생 순으로 처리한다. 같은 로케일이거나 지원되지 않으면 false.
     * DB 영속화는 여기서 하지 않고 계정 모달의 /accounts/preferences/ POST가 담당한다.
     * @returns {boolean} - 실제로 로케일이 변경되었는지 여부.
     */
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

    /**
     * 초기 로케일을 확정하고 <html lang>을 설정한 뒤 최초 번역 적용과 키 누락 점검을 수행한다.
     */
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
