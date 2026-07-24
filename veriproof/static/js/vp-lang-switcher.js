/*!
 * VeriProof AI — language switcher (globe dropdown, vanilla JS).
 *
 * A small, accessible, dependency-free dropdown that lets the visitor pick the
 * UI language. Mounted automatically into every ``[data-vp-lang-switch]``
 * container (the marketplace header on Discover). The workspace surface does
 * NOT use this — it reuses the existing Language ``<select>`` inside the
 * account modal (wired by shell.js) — so the two entry points stay consistent
 * through the shared ``VP.i18n`` engine rather than duplicate UI.
 *
 * Contract:
 *  - Depends on ``VP.i18n`` (vp-i18n.js) for ``getLocale`` / ``setLocale`` /
 *    the ``vp:language-change`` event. The engine knows nothing about this
 *    component (one-way dependency: switcher → engine).
 *  - On select → ``VP.i18n.setLocale(lang)`` (instant visual switch + cookie).
 *  - Refreshes its own label + active option whenever the locale changes, even
 *    if the change originated elsewhere (e.g. the workspace modal).
 *
 * Accessibility: the button exposes ``aria-haspopup`` / ``aria-expanded``; the
 * menu uses ``role="menu"`` with ``menuitemradio`` items; keyboard supports
 * Esc (close), ArrowUp/Down (move), Enter/Space (select); outside-click closes.
 */
(function (global) {
    "use strict";

    var MOUNT_ATTR = "data-vp-lang-switch";
    // The two options shown in the menu, in display order. Codes match
    // UserPreference.LANGUAGE_CHOICES and the engine's SUPPORTED list.
    var OPTIONS = [
        { code: "en", nameKey: "lang.en", shortKey: "lang.short.en" },
        { code: "ko", nameKey: "lang.ko", shortKey: "lang.short.ko" }
    ];
    var GLOBE_SVG =
        '<svg class="vp-lang-switch__globe" viewBox="0 0 24 24" width="16" height="16" ' +
        'aria-hidden="true" focusable="false">' +
        '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.5"/>' +
        '<path d="M3 12h18M12 3c2.5 2.5 2.5 15 0 18M12 3c-2.5 2.5-2.5 15 0 18" ' +
        'fill="none" stroke="currentColor" stroke-width="1.2"/></svg>';

    function el(tag, cls, html) {
        var n = document.createElement(tag);
        if (cls) { n.className = cls; }
        if (html != null) { n.innerHTML = html; }
        return n;
    }

    function shortLabel(code) {
        var i18n = global.VP && global.VP.i18n;
        var opt = OPTIONS.filter(function (o) { return o.code === code; })[0] || OPTIONS[0];
        // Prefer the dictionary's short label, fall back to uppercased code.
        return (i18n && i18n.t) ? i18n.t(opt.shortKey) : code.toUpperCase();
    }

    function buildSwitcher(root) {
        var i18n = global.VP && global.VP.i18n;
        if (!i18n) { return; }

        var widget = el("div", "vp-lang-switch");
        var btn = el("button", "vp-lang-switch__btn");
        btn.type = "button";
        btn.setAttribute("aria-haspopup", "true");
        btn.setAttribute("aria-expanded", "false");
        btn.setAttribute("aria-label", i18n.t("lang.switch.aria"));
        var label = el("span", "vp-lang-switch__label", shortLabel(i18n.getLocale()));
        var caret = el("span", "vp-lang-switch__caret", "▾");
        caret.setAttribute("aria-hidden", "true");
        // Button children: globe icon + current-locale label + caret.
        var iconWrap = el("span", "vp-lang-switch__icon");
        iconWrap.innerHTML = GLOBE_SVG;
        btn.appendChild(iconWrap);
        btn.appendChild(label);
        btn.appendChild(caret);

        var menu = el("ul", "vp-lang-switch__menu");
        menu.setAttribute("role", "menu");
        menu.hidden = true;
        var items = OPTIONS.map(function (opt) {
            var li = el("li");
            li.setAttribute("role", "none");
            var b = el("button", "vp-lang-switch__item");
            b.type = "button";
            b.setAttribute("role", "menuitemradio");
            b.dataset.lang = opt.code;
            b.textContent = i18n.t(opt.nameKey);
            b.addEventListener("click", function () {
                i18n.setLocale(opt.code);
                close(true);
            });
            li.appendChild(b);
            menu.appendChild(li);
            return b;
        });

        widget.appendChild(btn);
        widget.appendChild(menu);
        root.appendChild(widget);

        function syncActive() {
            var current = i18n.getLocale();
            label.textContent = shortLabel(current);
            items.forEach(function (b) {
                var active = b.dataset.lang === current;
                b.setAttribute("aria-checked", active ? "true" : "false");
                b.classList.toggle("is-active", active);
            });
        }

        function open() {
            menu.hidden = false;
            btn.setAttribute("aria-expanded", "true");
            widget.classList.add("is-open");
            var active = items.filter(function (b) { return b.dataset.lang === i18n.getLocale(); })[0];
            (active || items[0]).focus();
        }
        function close(returnFocus) {
            menu.hidden = true;
            btn.setAttribute("aria-expanded", "false");
            widget.classList.remove("is-open");
            if (returnFocus) { btn.focus(); }
        }
        function toggle() { menu.hidden ? open() : close(); }

        btn.addEventListener("click", function (e) {
            e.stopPropagation();
            toggle();
        });
        // Keyboard nav inside the menu.
        menu.addEventListener("keydown", function (e) {
            var idx = items.indexOf(document.activeElement);
            if (e.key === "Escape") { e.preventDefault(); close(true); return; }
            if (e.key === "ArrowDown") { e.preventDefault(); items[(idx + 1) % items.length].focus(); return; }
            if (e.key === "ArrowUp") { e.preventDefault(); items[(idx - 1 + items.length) % items.length].focus(); return; }
            if (e.key === "Home") { e.preventDefault(); items[0].focus(); return; }
            if (e.key === "End") { e.preventDefault(); items[items.length - 1].focus(); return; }
        });
        // Outside click closes.
        document.addEventListener("click", function (e) {
            if (!menu.hidden && !widget.contains(e.target)) { close(false); }
        });
        // Stay in sync if the locale changes from anywhere.
        global.addEventListener(i18n.EVENT, syncActive);

        syncActive();
    }

    function mountAll() {
        if (!(global.VP && global.VP.i18n)) { return; }
        var mounts = document.querySelectorAll("[" + MOUNT_ATTR + "]");
        for (var i = 0; i < mounts.length; i++) {
            // Avoid double-mounting if init runs twice.
            if (!mounts[i].childElementCount) { buildSwitcher(mounts[i]); }
        }
    }

    global.VP = global.VP || {};
    global.VP.langSwitcher = { mountAll: mountAll, build: buildSwitcher };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", mountAll);
    } else {
        mountAll();
    }
})(typeof window !== "undefined" ? window : this);
