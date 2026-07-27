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

    /**
     * 페이지에 삽입된 #library-config JSON 설정을 읽어온다. 파싱 실패 시 오프라인 기본값을 반환한다.
     */
    function readConfig() {
        var node = document.getElementById("library-config");
        if (node) {
            try { return JSON.parse(node.textContent || "{}"); } catch (e) { /* fall through */ }
        }
        return { firestore_enabled: false, events_url: "/api/v1/events", assets_api_url: "/api/v1/assets" };
    }

    /**
     * 라이브러리 페이지를 초기화한다. 미리보기 토글·인증서 모달·자산 설정 모달·
     * 거래 타임라인·판매 조건 저장·실시간 상태 갱신을 순서대로 바인딩한다.
     */
    function init() {
        wirePreviewToggle();
        wireCertificateModal();
        wireAssetSettingsModal();
        wireTransactions();
        wireAssetTerms();
        wireLiveUpdates();
    }

    /**
     * 자산 설정 폼 제출을 바인딩한다. 서버로 판매 조건(title/description/tags/가격/공개여부)을
     * 저장하고, 성공 시 카드의 가격·공개여부·제목 등을 새로고침 없이 갱신한다.
     */
    function wireAssetTerms() {
        document.querySelectorAll("#asset-settings-form").forEach(function (form) {
            form.addEventListener("submit", function (event) {
                event.preventDefault();
                var status = form.querySelector(".asset-card__terms-status");
                fetch("/api/v1/ip/" + encodeURIComponent(form.dataset.assetId) + "/terms", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: form.elements.title.value, description: form.elements.description.value, tags: parseTags(form.elements.tags.value), min_price_usdc: form.elements.min_price_usdc.value, target_price_usdc: form.elements.target_price_usdc.value, visibility: form.elements.visibility.value }) }).then(function (response) { return response.json().then(function (body) { return { ok: response.ok, body: body }; }); }).then(function (result) {
                    status.textContent = result.ok ? t("library.terms.saved") : (result.body.detail || t("library.terms.failed"));
                    if (!result.ok) { return; }
                    // 저장 직후 그리드의 핵심 정보도 갱신하여 새로고침 전의 불일치를 막는다.
                    var card = document.getElementById("asset-" + form.dataset.assetId);
                    if (!card) { return; }
                    card.dataset.minPrice = form.elements.min_price_usdc.value;
                    card.dataset.targetPrice = form.elements.target_price_usdc.value;
                    card.dataset.visibility = form.elements.visibility.value;
                    updateManageData(card, result.body);
                    var title = card.querySelector("h3");
                    if (title) { title.textContent = result.body.title || ""; }
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
        var deleteButton = modal.querySelector("[data-asset-delete]");
        function close() {
            modal.hidden = true;
            document.body.style.overflow = "";
            resetDeleteButton();
            if (lastTrigger) { lastTrigger.focus(); lastTrigger = null; }
        }
        function resetDeleteButton() {
            if (!deleteButton) { return; }
            deleteButton.dataset.confirming = "0";
            deleteButton.classList.remove("is-confirming");
            deleteButton.textContent = t("library.terms.delete");
            deleteButton.disabled = false;
        }
        modal.addEventListener("click", function (event) {
            var target = event.target && event.target.closest ? event.target.closest("[data-asset-settings-close]") : null;
            if (target) { event.preventDefault(); close(); }
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
                var data = manageData(card);
                form.elements.title.value = data.title || "";
                form.elements.description.value = data.description || "";
                form.elements.tags.value = (data.tags || []).join(", ");
                resetDeleteButton();
                var status = form.querySelector(".asset-card__terms-status");
                if (status) { status.textContent = ""; }
                document.getElementById("asset-settings-name").textContent = data.title || card.querySelector("h3").textContent;
                renderRegistration(data);
                renderSales(data.sales_summary || {}, data.sales || []);
                var explorer = document.getElementById("asset-settings-explorer");
                explorer.hidden = !card.dataset.explorerUrl;
                explorer.href = card.dataset.explorerUrl || "#";
                modal.hidden = false;
                document.body.style.overflow = "hidden";
                form.elements.min_price_usdc.focus();
            });
        });
        if (deleteButton) {
            deleteButton.addEventListener("click", function () {
                var status = form.querySelector(".asset-card__terms-status");
                if (deleteButton.dataset.confirming !== "1") {
                    deleteButton.dataset.confirming = "1";
                    deleteButton.classList.add("is-confirming");
                    deleteButton.textContent = t("library.terms.delete_confirm");
                    if (status) { status.textContent = t("library.terms.delete_hint"); }
                    return;
                }
                deleteButton.disabled = true;
                if (status) { status.textContent = t("library.terms.deleting"); }
                fetch("/api/v1/ip/" + encodeURIComponent(form.dataset.assetId) + "/delete", { method: "DELETE" }).then(function (response) {
                    return response.text().then(function (text) {
                        var body = {};
                        if (text) {
                            try { body = JSON.parse(text); } catch (e) { body = {}; }
                        }
                        return { ok: response.ok, body: body };
                    });
                }).then(function (result) {
                    if (!result.ok) {
                        resetDeleteButton();
                        if (status) { status.textContent = result.body.detail || t("library.terms.delete_failed"); }
                        return;
                    }
                    var card = document.getElementById("asset-" + form.dataset.assetId);
                    if (card) { card.remove(); }
                    close();
                    updateEmptyLibraryState();
                }).catch(function () {
                    resetDeleteButton();
                    if (status) { status.textContent = t("library.terms.delete_network"); }
                });
            });
        }
    }

    function updateEmptyLibraryState() {
        var grid = document.querySelector(".asset-grid");
        var state = document.getElementById("library-state");
        if (!grid || !state || grid.querySelector(".asset-card")) { return; }
        grid.remove();
        var empty = document.createElement("p");
        empty.className = "no-assets";
        empty.textContent = t("library.empty.noassets");
        state.appendChild(empty);
    }

    /**
     * 카드의 data-manage JSON에서 등록 메타데이터를 읽어온다. 파싱 실패 시 빈 객체.
     */
    function manageData(card) {
        try { return JSON.parse(card.dataset.manage || "{}"); } catch (e) { return {}; }
    }
    /**
     * 카드의 data-manage에서 title/description/tags만 부분 갱신하여 다시 직렬화한다.
     */
    function updateManageData(card, values) {
        var data = manageData(card);
        ["title", "description", "tags"].forEach(function (key) { data[key] = values[key]; });
        card.dataset.manage = JSON.stringify(data);
    }
    /**
     * 쉼표로 구분된 태그 문자열을 trim·빈 값 제거한 태그 배열로 변환한다.
     */
    function parseTags(value) {
        return value.split(",").map(function (tag) { return tag.trim(); }).filter(Boolean);
    }
    /**
     * 자산 설정 모달에 등록 정보(유형/카테고리/등록시각/지문)를 필드 행으로 채운다.
     */
    function renderRegistration(data) {
        var box = document.getElementById("asset-settings-registration");
        if (!box) { return; }
        box.innerHTML = fieldRow(t("library.terms.type"), data.asset_type) + fieldRow(t("library.terms.category"), data.category) + fieldRow(t("library.terms.registered_at"), formatTs(data.created_at)) + fieldRow(t("library.terms.fingerprint"), data.image_sha256);
    }
    /**
     * 자산 설정 모달에 판매 요약(건수/총액)과 판매 내역 리스트를 렌더링한다.
     */
    function renderSales(summary, sales) {
        var summaryBox = document.getElementById("asset-settings-sales-summary");
        var list = document.getElementById("asset-settings-sales-list");
        if (summaryBox) { summaryBox.innerHTML = '<span><strong>' + escapeHtml(summary.sale_count || 0) + '</strong>' + escapeHtml(t("library.sales.count")) + '</span><span class="asset-settings-modal__sales-summary--gross"><strong>' + escapeHtml(summary.gross_usdc || "0") + ' USDC</strong>' + escapeHtml(t("library.sales.gross")) + '</span>'; }
        if (!list) { return; }
        if (!sales.length) { list.innerHTML = "<li>" + escapeHtml(t("library.sales.empty")) + "</li>"; return; }
        list.innerHTML = sales.map(function (sale) { return "<li><div><strong>" + escapeHtml(sale.price_usdc) + " USDC</strong><span>" + escapeHtml(sale.usage_type) + " · " + escapeHtml(shortWallet(sale.buyer_wallet)) + "</span></div><time>" + escapeHtml(formatTs(sale.granted_at)) + "</time></li>"; }).join("");
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

        modal.addEventListener("click", function (event) {
            var closeControl = event.target && event.target.closest ? event.target.closest("[data-modal-close]") : null;
            if (closeControl) { event.preventDefault(); hideModal(); }
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
                showModal(payload, btn.getAttribute("data-work-title"), btn.getAttribute("data-registered-at"));
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
        /**
         * 인증서 모달을 연다. QR과 증명 필드를 채우고 포커스 트랩을 걸며 닫기 버튼으로 포커스를 옮긴다.
         */
        function showModal(payload, workTitle, registeredAt) {
            lastTrigger = document.activeElement;
            if (qrBox) { VP.renderQr(qrBox, payload); }
            if (fieldsBox) {
                fieldsBox.innerHTML = fieldRow(t("library.field.asset_id"), payload.asset_id) +
                    fieldRow(t("library.field.fingerprint"), payload.image_sha256) +
                    fieldRow(t("library.field.anchor_tx_sig"), payload.anchor_tx_sig) +
                    fieldRow(t("library.field.certificate_tx_sig"), payload.certificate_tx_sig) +
                    fieldRow(t("library.field.creator_wallet"), payload.creator_wallet) +
                    fieldRow(t("library.field.explorer_url"), payload.explorer_url);
            }
            document.getElementById("certificate-work-title").textContent = workTitle || payload.asset_id;
            document.getElementById("certificate-registered-at").textContent = formatTs(registeredAt);
            var download = document.getElementById("certificate-download");
            if (download) {
                download.hidden = !payload.certificate_tx_sig;
                download.href = "/library/" + encodeURIComponent(payload.asset_id) + "/certificate.pdf";
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
        /**
         * 인증서 모달을 닫고 포커스 트랩을 해제하며 이전 트리거로 포커스를 되돌린다.
         */
        function hideModal() {
            modal.hidden = true;
            document.body.style.overflow = "";
            modal.removeEventListener("keydown", trapFocus);
            if (lastTrigger && typeof lastTrigger.focus === "function") { lastTrigger.focus(); lastTrigger = null; }
        }
    }

    /**
     * <dt><dd> 필드 행 HTML을 생성한다. 값이 null/빈 문자열이면 "—"로 대체하고 값은 HTML 이스케이프한다.
     */
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

    /**
     * 거래 타임라인에 이벤트/라이선스 항목을 렌더링한다. 비어 있으면 빈 상태 문구를 표시한다.
     */
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

    /**
     * 등록된 모든 폴링 타이머를 정지한다.
     */
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

    /**
     * Firestore 상태 문서를 해당 자산 카드의 상태 배지에 반영한다.
     */
    function applyStatus(assetId, data) {
        var card = document.getElementById("asset-" + assetId);
        var badge = card && card.querySelector(".asset-card__status");
        if (badge && data && data.status) { badge.textContent = data.status; }
    }

    // --- utils --------------------------------------------------------------

    /**
     * 타임스탬프를 로케일에 맞는 문자열로 변환한다. 잘못된 값은 원본을 그대로 반환한다.
     */
    function formatTs(ts) {
        if (!ts) { return ""; }
        try { return new Date(ts).toLocaleString(); } catch (e) { return ts; }
    }
    function shortWallet(w) { return w ? w.slice(0, 6) + "…" + w.slice(-4) : "—"; }
    /**
     * 문자열을 HTML에 안전하게 이스케이프한다.
     */
    function escapeHtml(s) {
        return String(s == null ? "" : s)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else { init(); }
})();
