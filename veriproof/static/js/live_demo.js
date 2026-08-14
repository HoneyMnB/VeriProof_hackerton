/*! Authenticated Firestore-to-SSE live flow visualization. */
(function () {
    "use strict";
    var root = document.querySelector(".live-demo");
    if (!root) { return; }

    var registrationRoot = document.getElementById("live-registration-groups");
    var commerceRoot = document.getElementById("live-commerce-groups");
    var empty = document.getElementById("live-demo-empty");
    var skeleton = document.getElementById("live-demo-skeleton");
    var connection = document.getElementById("live-demo-connection");
    var events = new Map();
    var fallbackTimer = null;

    var labels = {
        REGISTRATION_STARTED: ["Registration started", "ai"], CONTENT_HASHED: ["Content fingerprinted", "proof"],
        AI_ANALYZED: ["AI metadata analyzed", "ai"], ANCHORING_STARTED: ["Anchoring requested", "chain"],
        ANCHORED: ["Work anchored", "chain"], REGISTRATION_CERTIFICATE_ISSUED: ["Registration proof issued", "proof"],
        ASSET_REGISTERED: ["Asset registered", "proof"], REGISTRATION_FAILED: ["Registration stopped", "error"],
        ASSET_DISCOVERED: ["Asset discovered", "ai"], HTTP_402: ["Payment terms requested", "chain"],
        OFFER: ["Buyer offer", "ai"], COUNTER: ["Seller counter-offer", "ai"], ACCEPT: ["Terms accepted", "ai"],
        REJECT: ["Offer declined", "error"], PAYMENT_SUBMITTED: ["Payment submitted", "chain"],
        PAYMENT_VERIFIED: ["Payment verified", "chain"], PAYMENT_FAILED: ["Payment rejected", "error"],
        LICENSE_ISSUED: ["License issued", "proof"], CERT_ISSUED: ["License proof anchored", "proof"],
        ROYALTY_SPLIT: ["Royalty distributed", "chain"], BATCH_SETTLED: ["Batch settled", "chain"]
    };

    function setStatus(connected, mode) {
        connection.classList.toggle("is-connected", connected);
        connection.classList.toggle("is-error", !connected);
        connection.querySelector("strong").textContent = connected ? "Live" : "Offline";
        connection.querySelector("small").textContent = connected ?
            (mode === "polling" ? "Snapshot fallback" : "Firestore · SSE") :
            (mode === "disabled" ? "Firestore is not enabled" : "Realtime stream unavailable");
    }

    function detail(item) {
        var payload = item.payload || {};
        var parts = [];
        ["offer_sol", "counter_sol", "price_sol"].some(function (key) {
            if (payload[key] !== undefined && payload[key] !== null) { parts.push(payload[key] + " SOL"); return true; }
            return false;
        });
        ["offer_usdc", "counter_usdc", "price_usdc"].some(function (key) {
            if (payload[key] !== undefined && payload[key] !== null) { parts.push(payload[key] + " USDC"); return true; }
            return false;
        });
        if (payload.category) { parts.push(payload.category); }
        if (payload.usage_type) { parts.push(String(payload.usage_type).replace("-", " ")); }
        if (payload.round !== undefined) { parts.push("round " + payload.round); }
        if (payload.reason) { parts.push(payload.reason); }
        return parts.join(" · ") || "Verified transition";
    }

    function shortKey(value) {
        var key = String(value || "unlinked");
        return key.length > 18 ? key.slice(0, 8) + "…" + key.slice(-6) : key;
    }

    function eventNode(item) {
        var meta = labels[item.type] || [item.type.replace(/_/g, " "), "ai"];
        var row = document.createElement("li");
        var time = item.timestamp ? new Date(item.timestamp) : null;
        row.className = "live-event is-" + meta[1];
        row.appendChild(document.createElement("span")).className = "live-event__node";
        var clock = row.appendChild(document.createElement("time"));
        clock.textContent = time && !isNaN(time) ? time.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "Now";
        var body = row.appendChild(document.createElement("div"));
        body.className = "live-event__body";
        body.appendChild(document.createElement("strong")).textContent = meta[0];
        body.appendChild(document.createElement("p")).textContent = detail(item);
        var type = row.appendChild(document.createElement("span"));
        type.className = "live-event__type";
        type.textContent = item.type.replace(/_/g, " ");
        return row;
    }

    function groupCard(key, items) {
        var card = document.createElement("article");
        card.className = "live-flow-card";
        var head = card.appendChild(document.createElement("header"));
        var title = head.appendChild(document.createElement("div"));
        title.appendChild(document.createElement("strong")).textContent = items[0].asset_title || "Untitled work";
        title.appendChild(document.createElement("small")).textContent = "Flow · " + shortKey(key);
        var state = head.appendChild(document.createElement("span"));
        state.className = "live-flow-card__state";
        state.textContent = labels[items[items.length - 1].type][0];
        var list = card.appendChild(document.createElement("ol"));
        list.className = "live-demo__feed";
        items.forEach(function (item) { list.appendChild(eventNode(item)); });
        return card;
    }

    function render() {
        var grouped = { registration: new Map(), commerce: new Map() };
        Array.from(events.values()).sort(function (a, b) { return String(a.timestamp).localeCompare(String(b.timestamp)); }).forEach(function (item) {
            var key = item.correlation_id || item.asset_id || "unlinked";
            if (!grouped[item.flow].has(key)) { grouped[item.flow].set(key, []); }
            grouped[item.flow].get(key).push(item);
        });
        [registrationRoot, commerceRoot].forEach(function (container) { container.replaceChildren(); });
        [[registrationRoot, grouped.registration], [commerceRoot, grouped.commerce]].forEach(function (entry) {
            Array.from(entry[1].entries()).reverse().forEach(function (group) { entry[0].appendChild(groupCard(group[0], group[1])); });
        });
        skeleton.hidden = true;
        empty.hidden = events.size > 0;
        var values = Array.from(events.values());
        var registrationIds = new Set(values.filter(function (item) { return item.flow === "registration"; }).map(function (item) { return item.correlation_id; }));
        document.getElementById("live-metric-events").textContent = values.length;
        document.getElementById("live-metric-registrations").textContent = registrationIds.size;
        document.getElementById("live-metric-negotiations").textContent = values.filter(function (item) { return ["OFFER", "COUNTER", "ACCEPT", "REJECT"].indexOf(item.type) >= 0; }).length;
        document.getElementById("live-metric-settlements").textContent = values.filter(function (item) { return item.type === "PAYMENT_VERIFIED"; }).length;
    }

    function consume(items) {
        (items || []).forEach(function (item) { if (item.event_id) { events.set(item.event_id, item); } });
        render();
    }

    function startPolling() {
        if (fallbackTimer) { return; }
        function refresh() {
            fetch("/api/v1/live-demo/events", { credentials: "same-origin" })
                .then(function (response) { return response.json(); })
                .then(function (data) { setStatus(data.connected, data.connected ? "polling" : data.reason); consume(data.items); })
                .catch(function () { setStatus(false, "unavailable"); skeleton.hidden = true; });
        }
        refresh();
        fallbackTimer = window.setInterval(refresh, 5000);
    }

    if (!window.EventSource) { startPolling(); return; }
    var stream = new EventSource("/api/v1/live-demo/stream");
    stream.addEventListener("snapshot", function (message) {
        var data = JSON.parse(message.data); setStatus(true, "sse"); consume(data.items);
    });
    stream.addEventListener("flow", function (message) { setStatus(true, "sse"); consume([JSON.parse(message.data)]); });
    stream.addEventListener("offline", function () { stream.close(); startPolling(); });
    stream.onerror = function () { stream.close(); startPolling(); };
}());
