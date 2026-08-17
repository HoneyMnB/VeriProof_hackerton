/* Live registration progress overlay: client steps + server pipeline stages over SSE.
 *
 * Exposes ``VP.registrationProgress`` for workspace.js:
 *   open({title, fileName}) → step(key, state, detail) → connect() → finish(result) | fail(message)
 * Server stages arrive from ``GET /api/v1/ip/register/stream`` (AgentEvent rows). */
(function (global) {
    "use strict";

    var VP = global.VP = global.VP || {};
    var STREAM_URL = "/api/v1/ip/register/stream";
    var READY_TIMEOUT_MS = 1500;
    var RING_LENGTH = 326.7;
    var STEPS = [
        { key: "wallet" }, { key: "draft" }, { key: "confirm" }, { key: "hash" }, { key: "analyze" },
        { key: "anchor", chain: true }, { key: "certificate", chain: true }, { key: "store" }, { key: "asset" }
    ];
    // Server event → step transition. ``detail`` builds a human-readable summary from the safe payload.
    var EVENTS = {
        REGISTRATION_STARTED: { step: "hash", state: "active" },
        CONTENT_HASHED: { step: "hash", state: "done", detail: function (p) { return p.content_sha256 ? t("workspace.progress.detail.hash", { hash: String(p.content_sha256).slice(0, 16) }) : ""; } },
        AI_ANALYZED: { step: "analyze", state: "done", detail: function (p) { return t("workspace.progress.detail.analysis", { category: p.category || "—", tags: p.tag_count == null ? "—" : p.tag_count, score: p.originality_score == null ? "—" : p.originality_score }); } },
        ANCHORING_STARTED: { step: "anchor", state: "active", detail: function (p) { return p.network ? t("workspace.progress.detail.network", { network: p.network }) : ""; } },
        ANCHORED: { step: "anchor", state: "done", detail: function (p) { return txDetail(p.anchor_tx_sig); } },
        REGISTRATION_CERTIFICATE_ISSUED: { step: "certificate", state: "done", detail: function (p) { return txDetail(p.registration_certificate_tx_sig); } },
        CONTENT_STORED: { step: "store", state: "done" },
        ASSET_REGISTERED: { step: "asset", state: "done" }
    };

    var els = null;
    var state = null;

    function t(key, vars) { return (VP.i18n && VP.i18n.t) ? VP.i18n.t(key, vars) : key; }
    function byId(id) { return document.getElementById(id); }
    function shortSig(sig) { return sig.length > 18 ? sig.slice(0, 8) + "…" + sig.slice(-8) : sig; }
    function txDetail(sig) {
        if (!sig) { return ""; }
        var link = document.createElement("a");
        link.href = VP.explorerUrl ? VP.explorerUrl(sig) : "https://explorer.solana.com/tx/" + encodeURIComponent(sig) + "?cluster=devnet";
        link.target = "_blank"; link.rel = "noopener noreferrer";
        link.textContent = t("workspace.progress.detail.view_tx");
        var code = document.createElement("code"); code.textContent = shortSig(sig);
        var wrap = document.createDocumentFragment(); wrap.append(code, " ", link);
        return wrap;
    }
    function elapsed(from) { return ((performance.now() - from) / 1000).toFixed(1) + "s"; }

    function ensureElements() {
        if (els) { return els; }
        var root = byId("registration-progress");
        if (!root) { return null; }
        els = {
            root: root, title: byId("registration-progress-title"), file: byId("registration-progress-file"),
            close: byId("registration-progress-close"), ring: byId("registration-progress-ring"), count: byId("registration-progress-count"),
            status: byId("registration-progress-status"), detail: byId("registration-progress-detail"),
            steps: byId("registration-progress-steps"), result: byId("registration-progress-result")
        };
        els.close.addEventListener("click", close);
        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && !els.root.hidden && !els.close.hidden) { close(); }
        });
        return els;
    }

    function buildSteps() {
        els.steps.replaceChildren();
        state.nodes = {};
        STEPS.forEach(function (step, index) {
            var item = document.createElement("li");
            item.className = "vp-regprog__step" + (step.chain ? " is-chain" : "");
            item.dataset.step = step.key;
            var node = document.createElement("span"); node.className = "vp-regprog__node";
            node.innerHTML = '<span class="vp-regprog__node-index">' + (index + 1) + '</span><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.5l3 3 6-6.5"></path></svg><span class="vp-regprog__node-bang" aria-hidden="true">!</span>';
            var body = document.createElement("div"); body.className = "vp-regprog__step-body";
            var label = document.createElement("span"); label.className = "vp-regprog__step-label";
            label.setAttribute("data-i18n", "workspace.progress.step." + step.key); label.textContent = t("workspace.progress.step." + step.key);
            if (step.chain) {
                var chip = document.createElement("span"); chip.className = "vp-regprog__step-chip";
                chip.setAttribute("data-i18n", "workspace.progress.chain"); chip.textContent = t("workspace.progress.chain");
                label.appendChild(chip);
            }
            var detail = document.createElement("span"); detail.className = "vp-regprog__step-detail";
            var wave = document.createElement("span"); wave.className = "vp-regprog__step-wave"; wave.setAttribute("aria-hidden", "true");
            body.append(label, detail, wave);
            var time = document.createElement("span"); time.className = "vp-regprog__step-time";
            item.append(node, body, time);
            els.steps.appendChild(item);
            state.nodes[step.key] = { item: item, detail: detail, time: time, state: "pending", activeAt: 0 };
        });
    }

    function stepIndex(key) { for (var i = 0; i < STEPS.length; i += 1) { if (STEPS[i].key === key) { return i; } } return -1; }

    function setNodeState(key, next) {
        var node = state.nodes[key];
        if (!node || node.state === next) { return; }
        node.item.classList.remove("is-active", "is-done", "is-error");
        if (next === "active") { node.activeAt = performance.now(); node.time.textContent = ""; }
        if (next === "done" && node.activeAt) { node.time.textContent = elapsed(node.activeAt); }
        if (next !== "pending") { node.item.classList.add("is-" + next); }
        node.state = next;
    }

    function setDetail(key, content) {
        var node = state.nodes[key];
        if (!node || content == null || content === "") { return; }
        node.detail.replaceChildren(typeof content === "string" ? document.createTextNode(content) : content);
        node.item.classList.add("has-detail");
    }

    /** Move the flow so ``key`` is in ``nextState``: earlier steps complete, the following step starts. */
    function step(key, nextState, detail) {
        if (!state) { return; }
        var index = stepIndex(key);
        if (index < 0) { return; }
        STEPS.slice(0, index).forEach(function (previous) {
            if (state.nodes[previous.key].state !== "done") { setNodeState(previous.key, "done"); }
        });
        setNodeState(key, nextState);
        if (nextState === "done" && index + 1 < STEPS.length && state.nodes[STEPS[index + 1].key].state === "pending") {
            setNodeState(STEPS[index + 1].key, "active");
        }
        setDetail(key, detail);
        render();
    }

    function currentActive() {
        for (var i = 0; i < STEPS.length; i += 1) { if (state.nodes[STEPS[i].key].state === "active") { return STEPS[i].key; } }
        return null;
    }

    function render() {
        var done = STEPS.filter(function (item) { return state.nodes[item.key].state === "done"; }).length;
        els.count.textContent = done + "/" + STEPS.length;
        els.ring.style.strokeDashoffset = String(RING_LENGTH * (1 - done / STEPS.length));
        var active = currentActive();
        var activeChain = Boolean(active) && Boolean(STEPS[stepIndex(active)].chain);
        els.root.classList.toggle("is-chain", activeChain);
        els.root.classList.toggle("is-running", Boolean(active) && !state.finished);
        if (active && !state.finished) {
            els.status.textContent = t("workspace.progress.step." + active);
            var detailNode = state.nodes[active].detail;
            els.detail.textContent = detailNode.textContent || t("workspace.progress.hint." + active);
        }
    }

    function open(options) {
        if (!ensureElements()) { return false; }
        closeStream();
        state = { nodes: {}, source: null, correlationId: null, finished: false, startedAt: performance.now() };
        els.root.classList.remove("is-chain", "is-running", "is-streaming", "is-offline", "is-failed", "is-complete");
        els.title.textContent = options.title || t("workspace.progress.untitled");
        els.file.textContent = options.fileName || "";
        els.result.hidden = true; els.result.replaceChildren();
        els.close.hidden = !options.dismissible;
        buildSteps();
        els.root.hidden = false;
        document.body.classList.add("is-registration-progress-open");
        step("wallet", "active");
        return true;
    }

    /** Open the SSE stream; resolve once the server confirms its cursor (or after a short timeout). */
    function connect() {
        if (!state || !global.EventSource) { markOffline(); return Promise.resolve(false); }
        return new Promise(function (resolve) {
            var settled = false;
            var source = new EventSource(STREAM_URL);
            state.source = source;
            var timer = setTimeout(function () { if (!settled) { settled = true; resolve(false); } }, READY_TIMEOUT_MS);
            source.addEventListener("ready", function () {
                els.root.classList.add("is-streaming"); els.root.classList.remove("is-offline");
                if (!settled) { settled = true; clearTimeout(timer); resolve(true); }
            });
            source.addEventListener("stage", function (event) { handleStage(JSON.parse(event.data)); });
            source.addEventListener("closed", function () { closeStream(); });
            source.onerror = function () {
                if (state && !state.finished) { markOffline(); }
                if (!settled) { settled = true; clearTimeout(timer); resolve(false); }
            };
        });
    }

    function markOffline() { els.root.classList.remove("is-streaming"); els.root.classList.add("is-offline"); }

    function handleStage(item) {
        if (!state || state.finished || !item || !item.type) { return; }
        if (state.correlationId && item.correlation_id !== state.correlationId) { return; }
        if (!state.correlationId && item.correlation_id) { state.correlationId = item.correlation_id; }
        var payload = item.payload || {};
        if (item.type === "REGISTRATION_FAILED") {
            var active = currentActive() || "asset";
            step(active, "error", payload.reason ? t("workspace.progress.detail.reason", { reason: payload.reason }) : "");
            return;
        }
        var rule = EVENTS[item.type];
        if (!rule) { return; }
        step(rule.step, rule.state, rule.detail ? rule.detail(payload) : "");
    }

    /** Registration request finished successfully: reconcile with the response and show proofs. */
    function finish(result) {
        if (!state) { return; }
        closeStream();
        state.finished = true;
        if (result.anchor_tx) { setDetail("anchor", txDetail(result.anchor_tx)); }
        if (result.registration_certificate_tx) { setDetail("certificate", txDetail(result.registration_certificate_tx)); }
        STEPS.forEach(function (item) { setNodeState(item.key, "done"); });
        render();
        els.root.classList.remove("is-running", "is-chain");
        els.root.classList.add("is-complete");
        els.status.textContent = t("workspace.progress.complete");
        els.detail.textContent = t(result.anchor_tx ? "workspace.status.registered_available" : "workspace.status.registered_pending");
        renderResult(true, result);
    }

    /** Any client- or server-side failure: mark the running step and explain why. */
    function fail(message) {
        if (!state || state.finished) { return; }
        closeStream();
        state.finished = true;
        var active = currentActive();
        if (active) { setNodeState(active, "error"); setDetail(active, message); }
        render();
        els.root.classList.remove("is-running", "is-chain", "is-streaming");
        els.root.classList.add("is-failed");
        els.status.textContent = t("workspace.progress.failed");
        els.detail.textContent = message || "";
        renderResult(false, { message: message });
    }

    function renderResult(ok, result) {
        var head = document.createElement("div"); head.className = "vp-regprog__result-head";
        var text = document.createElement("div");
        var strong = document.createElement("strong"); strong.textContent = t(ok ? "workspace.progress.result_ok" : "workspace.progress.result_failed");
        var body = document.createElement("p"); body.textContent = ok ? t("workspace.progress.result_ok_body") : (result.message || "");
        text.append(strong, body);
        var actions = document.createElement("div"); actions.className = "vp-regprog__result-actions";
        if (ok) {
            var library = document.createElement("button"); library.type = "button"; library.className = "vp-regprog__secondary";
            library.textContent = t("workspace.progress.open_library");
            library.addEventListener("click", function () { global.location.href = "/library"; });
            actions.appendChild(library);
        }
        var done = document.createElement("button"); done.type = "button"; done.textContent = t(ok ? "workspace.progress.done" : "workspace.progress.close_short");
        done.addEventListener("click", close);
        actions.appendChild(done);
        head.append(text, actions);
        els.result.replaceChildren(head);
        if (ok) {
            var proofs = document.createElement("ul"); proofs.className = "vp-regprog__proofs";
            [["workspace.progress.proof.asset", result.asset_id, null], ["workspace.progress.proof.anchor", result.anchor_tx, VP.explorerUrl], ["workspace.progress.proof.certificate", result.registration_certificate_tx, VP.explorerUrl]].forEach(function (entry) {
                if (!entry[1]) { return; }
                var item = document.createElement("li");
                var label = document.createElement("span"); label.textContent = t(entry[0]);
                var code = document.createElement("code"); code.textContent = entry[1]; code.title = entry[1];
                item.append(label, code);
                if (entry[2]) {
                    var link = document.createElement("a"); link.href = entry[2](entry[1]); link.target = "_blank"; link.rel = "noopener noreferrer";
                    link.textContent = t("workspace.progress.detail.view_tx"); item.appendChild(link);
                }
                proofs.appendChild(item);
            });
            els.result.appendChild(proofs);
        }
        els.result.hidden = false;
        els.close.hidden = false;
        done.focus();
    }

    function closeStream() {
        if (state && state.source) { state.source.close(); state.source = null; }
    }

    function close() {
        if (!els) { return; }
        closeStream();
        els.root.hidden = true;
        document.body.classList.remove("is-registration-progress-open");
        state = null;
    }

    VP.registrationProgress = { open: open, step: step, connect: connect, finish: finish, fail: fail, close: close };
}(window));
