/*!
 * VeriProof AI — SPEC-005 library page (Page 2).
 *
 * Vanilla JS. Wires:
 * - R6 / AC-5: preview toggle (watermark <-> thumbnail, never original).
 * - R7 / AC-6: Explorer button (server-rendered href, opened in a new tab).
 * - R8 / AC-7: Certificate button -> QR modal (proof data only).
 * - R9 / AC-8: Transactions button -> lazy timeline fetch.
 * - R10 / AC-9: live status via Firestore onSnapshot when enabled + SDK present,
 *   otherwise /api/v1/events polling every 2s (the offline default).
 *
 * Uses the pure helpers in dashboard.js (window.VP). No build step, no deps.
 */
(function () {
    "use strict";

    var VP = window.VP;
    if (!VP) { return; }
    // i18n helper — VP.i18n loads in <head> and survives dashboard.js's merge.
    function t(k, v) { return (VP.i18n && VP.i18n.t) ? VP.i18n.t(k, v) : k; }

    var POLL_INTERVAL_MS = 2000;
    var config = readConfig();

    function readConfig() {
        var node = document.getElementById("library-config");
        if (node) {
            try { return JSON.parse(node.textContent || "{}"); } catch (e) { /* fall through */ }
        }
        return { firestore_enabled: false, events_url: "/api/v1/events", assets_api_url: "/api/v1/assets" };
    }

    function init() {
        autoResolveWallet();
        // UX-001: when the wallet is changed from the shared sidebar, reload so
        // the server re-renders that creator's assets.
        window.addEventListener("vp:wallet-changed", function () { window.location.reload(); });
        wirePreviewToggle();
        wireCertificateModal();
        wireAssetSettingsModal();
        wireTransactions();
        wireAssetTerms();
        wireLiveUpdates();
    }

    function wireAssetTerms() {
        document.querySelectorAll("#asset-settings-form").forEach(function (form) {
            form.addEventListener("submit", function (event) {
                event.preventDefault();
                var wallet = VP.getWallet ? VP.getWallet() : "";
                var status = form.querySelector(".asset-card__terms-status");
                if (!wallet) { status.textContent = t("library.terms.wallet_needed"); return; }
                fetch("/api/v1/ip/" + encodeURIComponent(form.dataset.assetId) + "/terms", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ creator_wallet: wallet, min_price_usdc: form.elements.min_price_usdc.value, target_price_usdc: form.elements.target_price_usdc.value, visibility: form.elements.visibility.value }) }).then(function (response) { return response.json().then(function (body) { return { ok: response.ok, body: body }; }); }).then(function (result) {
                    status.textContent = result.ok ? t("library.terms.saved") : (result.body.detail || t("library.terms.failed"));
                    if (!result.ok) { return; }
                    // 저장 직후 그리드의 핵심 정보도 갱신하여 새로고침 전의 불일치를 막는다.
                    var card = document.getElementById("asset-" + form.dataset.assetId);
                    if (!card) { return; }
                    card.dataset.minPrice = form.elements.min_price_usdc.value;
                    card.dataset.targetPrice = form.elements.target_price_usdc.value;
                    card.dataset.visibility = form.elements.visibility.value;
                    var price = card.querySelector(".asset-card__price strong");
                    if (price) { price.textContent = form.elements.min_price_usdc.value + " USDC"; }
                }).catch(function () { status.textContent = t("library.terms.network"); });
            });
        });
    }

    // 카드에는 작품 탐색에 필요한 정보만 유지하고, 판매 조건 편집은 별도 모달에서 처리한다.
    function wireAssetSettingsModal() {
        var modal = document.getElementById("asset-settings-modal");
        var form = document.getElementById("asset-settings-form");
        if (!modal || !form) { return; }
        var lastTrigger = null;
        function close() {
            modal.hidden = true;
            if (lastTrigger) { lastTrigger.focus(); lastTrigger = null; }
        }
        modal.querySelectorAll("[data-asset-settings-close]").forEach(function (element) {
            element.addEventListener("click", close);
        });
        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && !modal.hidden) { close(); }
        });
        document.querySelectorAll(".btn--asset-settings").forEach(function (button) {
            button.addEventListener("click", function () {
                var card = button.closest(".asset-card");
                if (!card) { return; }
                lastTrigger = button;
                form.dataset.assetId = card.dataset.assetId;
                form.elements.min_price_usdc.value = card.dataset.minPrice;
                form.elements.target_price_usdc.value = card.dataset.targetPrice;
                form.elements.visibility.value = card.dataset.visibility;
                document.getElementById("asset-settings-name").textContent = card.querySelector("h3").textContent;
                var explorer = document.getElementById("asset-settings-explorer");
                explorer.hidden = !card.dataset.explorerUrl;
                explorer.href = card.dataset.explorerUrl || "#";
                modal.hidden = false;
                form.elements.min_price_usdc.focus();
            });
        });
    }

    // 계정 설정 지갑은 공유 셸의 단일 접근점에서 읽는다. 과거 localStorage 값은
    // 신뢰하지 않아 다른 브라우저 사용자 자산을 잘못 표시하지 않는다.
    function autoResolveWallet() {
        var state = document.getElementById("library-state");
        var current = state && state.getAttribute("data-wallet");
        if (current) { return; }
        var saved = VP.getWallet ? VP.getWallet() : "";
        if (!saved) { return; }
        var url = new URL(window.location.href);
        url.searchParams.set("creator", saved);
        window.location.replace(url.toString());
    }

    // --- R6 / AC-5: preview toggle -----------------------------------------

    function wirePreviewToggle() {
        var toggles = document.querySelectorAll(".asset-card__toggle");
        toggles.forEach(function (btn) {
            btn.addEventListener("click", function () {
                var card = btn.closest(".asset-card");
                var img = card && card.querySelector(".asset-card__img");
                if (!img) { return; }
                var wm = img.getAttribute("data-watermark-url");
                var th = img.getAttribute("data-thumbnail-url");
                var showingWatermark = img.getAttribute("src") === wm;
                // Pure helper guarantees only wm/th can be selected (never original).
                img.setAttribute("src", VP.previewSrc(wm, th, !showingWatermark));
                btn.setAttribute("aria-pressed", String(showingWatermark));
                btn.textContent = t(showingWatermark ? "library.asset.show_protected_preview" : "library.asset.show_owner_preview");
            });
        });
    }

    // --- R8 / AC-7: certificate QR modal -----------------------------------

    function wireCertificateModal() {
        var modal = document.getElementById("certificate-modal");
        var qrBox = document.getElementById("certificate-qr");
        var fieldsBox = document.getElementById("certificate-fields");
        if (!modal) { return; }

        modal.addEventListener("click", function (e) {
            if (e.target && e.target.hasAttribute("data-modal-close")) { hideModal(); }
        });
        document.addEventListener("keydown", function (e) {
            if (e.key === "Escape") { hideModal(); }
        });

        document.querySelectorAll(".btn--certificate").forEach(function (btn) {
            btn.addEventListener("click", function () {
                // Reconstruct the proof payload from per-field data attributes —
                // by construction no original bytes/url is present in the DOM.
                var asset = {
                    asset_id: btn.getAttribute("data-asset-id"),
                    image_sha256: btn.getAttribute("data-image-sha256"),
                    anchor_tx_sig: btn.getAttribute("data-anchor-tx-sig") || null,
                    creator_wallet: btn.getAttribute("data-creator-wallet")
                };
                var certTx = btn.getAttribute("data-certificate-tx-sig") || null;
                var payload = VP.buildCertificatePayload(asset, certTx);
                showModal(payload);
            });
        });

        var lastTrigger = null;
        function focusableIn(modalEl) {
            return modalEl.querySelectorAll('a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])');
        }
        function trapFocus(event) {
            if (event.key !== "Tab") { return; }
            var items = focusableIn(modal);
            if (!items.length) { event.preventDefault(); return; }
            var first = items[0], last = items[items.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
        function showModal(payload) {
            lastTrigger = document.activeElement;
            if (qrBox) { VP.renderQr(qrBox, payload); }
            if (fieldsBox) {
                fieldsBox.innerHTML = fieldRow(t("library.field.asset_id"), payload.asset_id) +
                    fieldRow(t("library.field.anchor_tx_sig"), payload.anchor_tx_sig) +
                    fieldRow(t("library.field.certificate_tx_sig"), payload.certificate_tx_sig) +
                    fieldRow(t("library.field.creator_wallet"), payload.creator_wallet) +
                    fieldRow(t("library.field.explorer_url"), payload.explorer_url);
            }
            modal.hidden = false;
            document.body.style.overflow = "hidden";
            // Idempotent: remove first so repeated opens never stack handlers.
            modal.removeEventListener("keydown", trapFocus);
            modal.addEventListener("keydown", trapFocus);
            // Focus the real Close <button> — NOT the backdrop div (it has no
            // tabindex and is not focusable, so .focus() would be a no-op).
            var closeBtn = modal.querySelector("button[data-modal-close]");
            if (closeBtn) { closeBtn.focus(); } else { modal.setAttribute("tabindex", "-1"); modal.focus(); }
        }
        function hideModal() {
            modal.hidden = true;
            document.body.style.overflow = "";
            modal.removeEventListener("keydown", trapFocus);
            if (lastTrigger && typeof lastTrigger.focus === "function") { lastTrigger.focus(); lastTrigger = null; }
        }
    }

    function fieldRow(label, value) {
        var v = value == null || value === "" ? "—" : String(value);
        return "<dt>" + label + "</dt><dd>" + escapeHtml(v) + "</dd>";
    }

    // --- R9 / AC-8: transactions timeline ----------------------------------

    function wireTransactions() {
        document.querySelectorAll(".btn--timeline").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var url = btn.getAttribute("data-transactions-url");
                var card = btn.closest(".asset-card");
                var timeline = card && card.querySelector(".asset-card__timeline");
                if (!url || !timeline) { return; }
                if (timeline.hidden === false && timeline.dataset.loaded === "1") {
                    timeline.hidden = true; return; // toggle off
                }
                timeline.hidden = false;
                timeline.innerHTML = "<li>" + t("library.timeline.loading") + "</li>";
                fetch(url).then(function (r) { return r.json(); }).then(function (body) {
                    renderTimeline(timeline, body.items || []);
                }).catch(function () {
                    timeline.innerHTML = "<li>" + t("library.timeline.failed") + "</li>";
                });
            });
        });
    }

    function renderTimeline(timeline, items) {
        timeline.dataset.loaded = "1";
        if (!items.length) {
            timeline.innerHTML = "<li>" + t("library.timeline.empty") + "</li>";
            return;
        }
        timeline.innerHTML = items.map(function (it) {
            var ts = formatTs(it.timestamp);
            if (it.kind === "license") {
                return '<li><strong>' + t("library.timeline.license") + '</strong> · ' + t("library.timeline.buyer") + ' ' + escapeHtml(shortWallet(it.buyer_wallet)) +
                    " · " + escapeHtml(it.price_usdc) + " USDC (" + escapeHtml(it.usage_type) + ")" +
                    ' <span class="ts">' + ts + "</span></li>";
            }
            return "<li><strong>" + escapeHtml(it.type || "event") + "</strong>" +
                ' <span class="ts">' + ts + "</span></li>";
        }).join("");
    }

    // --- R10 / AC-9: live status (Firestore onSnapshot OR polling) ---------

    function wireLiveUpdates() {
        var firestoreEnabled = !!config.firestore_enabled;
        var firebaseSdkPresent = typeof window.firebase !== "undefined" && !!window.firebase.firestore;
        // Pure decision helper mirrors the Python SSOT.
        if (!VP.shouldPollEvents({ firestoreEnabled: firestoreEnabled, firebaseSdkPresent: firebaseSdkPresent })) {
            subscribeFirestore();
        } else {
            startPolling();
        }
    }

    function subscribeFirestore() {
        // Firebase JS SDK is OPTIONAL and import-guarded. If absent at runtime
        // (the offline default), fall back to polling rather than crashing.
        try {
            var db = window.firebase.firestore();
            db.collection("asset_status").onSnapshot(function (snap) {
                snap.docChanges().forEach(function (change) {
                    applyStatus(change.doc.id, change.doc.data());
                });
            });
        } catch (e) {
            startPolling();
        }
    }

    // Incremental polling: /api/v1/events?asset_id=&since=<last ts>. Shared
    // with SPEC-006 sandbox.
    var pollTimers = [];
    function startPolling() {
        var cards = document.querySelectorAll(".asset-card");
        cards.forEach(function (card) {
            var assetId = card.getAttribute("id") && card.getAttribute("id").replace(/^asset-/, "");
            if (!assetId) { return; }
            var since = null;
            function tick() {
                var url = config.events_url + "?asset_id=" + encodeURIComponent(assetId);
                if (since) { url += "&since=" + encodeURIComponent(since); }
                fetch(url).then(function (r) { return r.json(); }).then(function (body) {
                    var items = body.items || [];
                    items.forEach(function (ev) {
                        applyEvent(card, ev);
                        if (ev.timestamp) { since = ev.timestamp; }
                    });
                }).catch(function () { /* silent retry on next tick */ });
            }
            tick();
            pollTimers.push(setInterval(tick, POLL_INTERVAL_MS));
        });
    }

    function stopPolling() {
        pollTimers.forEach(function (t) { clearInterval(t); });
        pollTimers = [];
    }
    // Pause polling while the tab is hidden (no wasted requests) and resume on
    // focus so live status keeps updating once the creator returns.
    document.addEventListener("visibilitychange", function () {
        if (document.hidden) { stopPolling(); }
        else if (!pollTimers.length) { startPolling(); }
    });

    function applyEvent(card, ev) {
        var badge = card.querySelector(".asset-card__status");
        // Surface CERT_ISSUED / ACCEPT as a lightweight live hint on the card.
        if (badge && (ev.type === "CERT_ISSUED" || ev.type === "ACCEPT")) {
            badge.textContent = ev.type === "CERT_ISSUED" ? t("library.badge.licensed") : badge.textContent;
        }
    }

    function applyStatus(assetId, data) {
        var card = document.getElementById("asset-" + assetId);
        var badge = card && card.querySelector(".asset-card__status");
        if (badge && data && data.status) { badge.textContent = data.status; }
    }

    // --- utils --------------------------------------------------------------

    function formatTs(ts) {
        if (!ts) { return ""; }
        try { return new Date(ts).toLocaleString(); } catch (e) { return ts; }
    }
    function shortWallet(w) { return w ? w.slice(0, 6) + "…" + w.slice(-4) : "—"; }
    function escapeHtml(s) {
        return String(s == null ? "" : s)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else { init(); }
})();
