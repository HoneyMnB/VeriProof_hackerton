/*!
 * VeriProof AI — SPEC-006 sandbox live stream (vanilla JS).
 *
 * Browser-side event-to-pane routing. This is the only runtime implementation;
 * a duplicate Python mirror would not exercise the browser code. Extends the
 * ``window.VP`` namespace from dashboard.js (SPEC-005) with sandbox helpers.
 *
 * Coverage:
 * - R4/R5/R6: eventPane(type) -> "seller" | "buyer" | "inspector"
 * - R6/R8:    inspectorEvents(events) -> ordered inspector stream + explorerUrl
 * - R7/AC-6:  shouldPollEvents({firestoreEnabled, firebaseSdkPresent}) -> bool
 *
 * Wiring: on "시뮬레이션 시작", POST /api/v1/sandbox/run, then subscribe to the
 * Firestore ``sandbox_feed`` collection via onSnapshot when (FIRESTORE_ENABLED
 * AND the Firebase JS SDK is present); otherwise poll /api/v1/events?since=
 * every 2s (R7 polling fallback). No build step, no external JS dependencies.
 */
(function (global) {
    "use strict";

    var VP = global.VP = global.VP || {};
    // i18n helper — VP.i18n loads in <head> and survives dashboard.js's merge.
    function t(k, v) { return (VP.i18n && VP.i18n.t) ? VP.i18n.t(k, v) : k; }

    // --- Pane routing (R4 seller / R5 buyer / R6 inspector) -----------------
    var PANE_SELLER = "seller";
    var PANE_BUYER = "buyer";
    var PANE_INSPECTOR = "inspector";
    var INSPECTOR_TYPES = { HTTP_402: 1, PAYMENT_VERIFIED: 1, CERT_ISSUED: 1, SIMULATION_FAILED: 1 };
    var BUYER_TYPES = { OFFER: 1, ACCEPT: 1 };
    var SELLER_TYPES = { COUNTER: 1 };

    /**
     * 이벤트 타입을 스트림 pane(seller/buyer/inspector)으로 라우팅한다.
     * 알 수 없는 타입은 inspector로 보내 표시를 유지한다(R4/R5/R6).
     * @param {string} type - 이벤트 타입.
     * @returns {string} - "seller" | "buyer" | "inspector".
     */
    function eventPane(type) {
        if (INSPECTOR_TYPES[type]) { return PANE_INSPECTOR; }
        if (BUYER_TYPES[type]) { return PANE_BUYER; }
        if (SELLER_TYPES[type]) { return PANE_SELLER; }
        return PANE_INSPECTOR; // unknown -> inspector (stay visible)
    }

    /**
     * 이벤트 목록에서 inspector pane에 해당하는 이벤트만 순서대로 추출한다.
     */
    function inspectorEvents(events) {
        var out = [];
        for (var i = 0; i < events.length; i++) {
            if (eventPane(events[i].type) === PANE_INSPECTOR) { out.push(events[i]); }
        }
        return out;
    }

    // R7/AC-6: poll unless Firestore AND the JS SDK are both live.
    function shouldPollEvents(opts) {
        var fs = !!(opts && opts.firestoreEnabled);
        var sdk = !!(opts && opts.firebaseSdkPresent);
        return !(fs && sdk);
    }

    VP.eventPane = eventPane;
    VP.inspectorEvents = inspectorEvents;
    VP.shouldPollEventsSandbox = shouldPollEvents;

    // --- Rendering -----------------------------------------------------------

    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) { n.className = cls; }
        if (text != null) { n.textContent = text; }
        return n;
    }

    function explorerUrl(sig) {
        // Reuse the SPEC-005 SSOT mirrored in dashboard.js (null when no sig).
        return VP.explorerUrl ? VP.explorerUrl(sig) : null;
    }

    /**
     * 단일 이벤트를 해당 pane의 스트림 리스트에 렌더링한다.
     * inspector 이벤트는 tx 시그니처·상태·Explorer 링크·소요시간·수수료를 함께 표시한다(R6/R8).
     */
    function renderEvent(ev) {
        var pane = eventPane(ev.type);
        var list = document.querySelector('.stream[data-pane="' + pane + '"]');
        if (!list) { return; }
        var li = el("li", "event event-" + ev.type.toLowerCase());
        li.appendChild(el("span", "event-type", ev.type));
        // Inspector: surface tx + explorer link + duration/fee (R6/R8).
        if (pane === PANE_INSPECTOR && ev.payload) {
            var p = ev.payload;
            if (p.tx_signature) { li.appendChild(el("span", "event-tx", p.tx_signature)); }
            if (p.status) { li.appendChild(el("span", "event-status", String(p.status))); }
            if (p.certificate_tx) {
                var url = explorerUrl(p.certificate_tx);
                if (url) {
                    var a = el("a", "explorer-link", t("sandbox.explorer"));
                    a.href = url; a.target = "_blank"; a.rel = "noopener";
                    li.appendChild(a);
                }
            }
            if (p.duration_ms != null) {
                li.appendChild(el("span", "event-meta", p.duration_ms + " ms"));
            }
            if (p.fee_usdc) {
                li.appendChild(el("span", "event-meta", t("sandbox.event.fee", { n: p.fee_usdc })));
            }
        }
        if (ev.reason || (ev.payload && ev.payload.reason)) {
            li.appendChild(el("span", "event-reason", ev.reason || ev.payload.reason));
        }
        list.appendChild(li);
    }

    // --- Live stream subscription (Firestore onSnapshot) / polling ----------

    var pollTimer = null;
    var lastSince = null;

    /**
     * 모든 pane의 스트림 리스트 내용을 비운다.
     */
    function clearStream() {
        var lists = document.querySelectorAll(".stream");
        for (var i = 0; i < lists.length; i++) { lists[i].innerHTML = ""; }
    }

    function startPolling(assetId) {
        // R7/AC-6: polling fallback every 2s, incremental via since=.
        if (pollTimer) { clearInterval(pollTimer); }
        pollTimer = setInterval(function () {
            var url = "/api/v1/events?asset_id=" + encodeURIComponent(assetId);
            if (lastSince) { url += "&since=" + encodeURIComponent(lastSince); }
            fetch(url, { headers: { Accept: "application/json" } })
                .then(function (r) { return r.json(); })
                .then(function (data) { ingestItems(data.items || []); })
                .catch(function () { /* swallow; next tick retries */ });
        }, 2000);
    }

    /**
     * 폴링 타이머를 멈추고 해제한다.
     */
    function stopPolling() {
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    }

    // Stop the tail once the simulation reaches a terminal state or the tab is
    // hidden — no point burning requests after SUCCESS/FAILED.
    global.addEventListener("visibilitychange", function () {
        if (document.hidden) { stopPolling(); }
    });

    /**
     * 폴링으로 가져온 이벤트 아이템을 순회하며 렌더링하고 lastSince를 전진시킨다.
     * SIMULATION_FAILED 도달 시 더 이상의 폴링을 중단한다.
     */
    function ingestItems(items) {
        if (!items || !items.length) { return; }
        for (var i = 0; i < items.length; i++) {
            var it = items[i];
            renderEvent({ type: it.type, payload: it.payload || {}, reason: null });
            if (it.timestamp) { lastSince = it.timestamp; }
            if (it.type === "SIMULATION_FAILED") { stopPolling(); }
        }
    }

    // Sandbox_feed docs from the real execution carry pane and detail metadata.
    function ingestFeedDocs(docs) {
        if (!docs) { return; }
        for (var i = 0; i < docs.length; i++) {
            var d = docs[i];
            renderEvent({ type: d.type, payload: d.detail || {}, reason: d.message });
        }
    }

    function subscribeFirestore(runId) {
        // R7: try the Firebase JS SDK; fall back to polling if it is absent at
        // runtime (the offline default). The decision is pure (shouldPollEvents).
        var sdkPresent = !!(global.firebase && global.firebase.firestore);
        var firestoreEnabled = !!(global.VP_SANDBOX && global.VP_SANDBOX.firestoreEnabled);
        if (shouldPollEvents({ firestoreEnabled: firestoreEnabled, firebaseSdkPresent: sdkPresent })) {
            return false; // caller polls
        }
        try {
            var db = global.firebase.firestore();
            db.collection("sandbox_feed").where("run_id", "==", runId)
                .onSnapshot(function (snap) {
                    snap.docChanges().forEach(function (chg) {
                        if (chg.type === "added") {
                            var d = chg.doc.data();
                            renderEvent({ type: d.type, payload: d.detail || {}, reason: d.message });
                        }
                    });
                });
            return true;
        } catch (e) {
            return false; // SDK present but errored -> fall back to polling
        }
    }

    // --- Trigger (R1: "시뮬레이션 시작") -----------------------------------

    /**
     * 샌드박스 상태 표시 요소의 텍스트와 상태 클래스를 갱신한다.
     */
    function setStatus(msg, cls) {
        var s = document.getElementById("sandbox-status");
        if (!s) { return; }
        s.textContent = msg || "";
        s.className = "sandbox-status" + (cls ? " " + cls : "");
    }

    /**
     * 샌드박스 폼을 초기화한다. ?asset= 으로 자산 ID를 사전 채우기한 뒤, 제출 시 스트림을
     * 리셋하고 /api/v1/sandbox/run 을 호출한다. 완료 후 Firestore 구독 또는 폴링으로 실시간 이벤트를 수신한다.
     */
    function init() {
        var form = document.getElementById("sandbox-form");
        if (!form) { return; }
        // UX-002: pre-fill the asset ID from ?asset= (arrival from the library
        // or a freshly registered work) so the demo flow needs no copy-paste.
        var assetField = document.getElementById("sandbox-asset-id");
        var presetAsset = new URLSearchParams(global.location.search).get("asset");
        if (presetAsset && assetField && !assetField.value) { assetField.value = presetAsset; }
        form.addEventListener("submit", function (e) {
            e.preventDefault();
            clearStream();
            lastSince = null;
            var assetId = document.getElementById("sandbox-asset-id").value.trim();
            var offer = document.getElementById("sandbox-offer").value;
            var usage = document.getElementById("sandbox-usage").value;
            var paymentTx = document.getElementById("sandbox-payment-tx").value.trim();
            var buyerWallet = document.getElementById("sandbox-buyer-wallet").value.trim();
            if (!assetId) { setStatus(t("sandbox.status.asset_required"), "err"); return; }
            if (!paymentTx || !buyerWallet) { setStatus(t("sandbox.status.fields_required"), "err"); return; }
            setStatus(t("sandbox.status.running"));
            var startBtn = document.getElementById("sandbox-start");
            if (startBtn) { startBtn.disabled = true; startBtn.setAttribute("aria-busy", "true"); }
            fetch("/api/v1/sandbox/run", {
                method: "POST",
                headers: { "Content-Type": "application/json", Accept: "application/json" },
                body: JSON.stringify({
                    asset_id: assetId, buyer_agent_id: "sandbox-ui",
                    offer_usdc: offer, usage_type: usage,
                    payment_tx_sig: paymentTx, buyer_wallet: buyerWallet
                })
            })
                .then(function (r) {
                    if (r.status === 404) { throw new Error(t("sandbox.status.asset_not_found")); }
                    return r.json();
                })
                .then(function (data) {
                    // The response carries the full ordered sandbox_feed doc list
                    // (run.steps) so the UI renders immediately even before the
                    // polling/Firestore tail catches up.
                    ingestFeedDocs(data.steps);
                    setStatus(data.ok ? t("sandbox.status.success") : (t("sandbox.status.failed") + (data.error || "")),
                              data.ok ? "ok" : "err");
                    if (startBtn) { startBtn.disabled = false; startBtn.removeAttribute("aria-busy"); }
                    // R7: prefer Firestore onSnapshot; else poll the shared feed.
                    if (data.ok && !subscribeFirestore(data.run_id)) { startPolling(assetId); }
                })
                .catch(function (err) {
                    if (startBtn) { startBtn.disabled = false; startBtn.removeAttribute("aria-busy"); }
                    setStatus(String(err.message || err), "err");
                });
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})(window);
