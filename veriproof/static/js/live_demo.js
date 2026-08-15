/*! Authenticated Firestore-to-SSE live flow visualization. */
(function () {
    "use strict";
    var root = document.querySelector(".live-demo");
    if (!root) { return; }

    var flowRoots = {
        registration: document.getElementById("live-registration-groups"),
        commerce: document.getElementById("live-commerce-groups")
    };
    var emptyStates = {
        registration: document.getElementById("live-registration-empty"),
        commerce: document.getElementById("live-commerce-empty")
    };
    var tabs = Array.from(document.querySelectorAll(".live-demo__tab"));
    var panels = Array.from(document.querySelectorAll('[role="tabpanel"]'));
    var skeletons = Array.from(document.querySelectorAll(".live-demo__skeleton"));
    var connection = document.getElementById("live-demo-connection");
    var events = new Map();
    var fallbackTimer = null;
    var activeFlow = "registration";
    var expandedGroups = { registration: null, commerce: null };
    var unseenEvents = { registration: 0, commerce: 0 };

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

    function activateFlow(flow, moveFocus) {
        if (!flowRoots[flow]) { return; }
        activeFlow = flow;
        unseenEvents[flow] = 0;
        tabs.forEach(function (tab) {
            var selected = tab.dataset.flow === flow;
            tab.classList.toggle("is-active", selected);
            tab.setAttribute("aria-selected", String(selected));
            tab.tabIndex = selected ? 0 : -1;
            if (selected && moveFocus) { tab.focus(); }
        });
        panels.forEach(function (panel) {
            panel.hidden = panel.id !== "live-panel-" + flow;
        });
        updateTabIndicators();
    }

    function updateTabIndicators() {
        tabs.forEach(function (tab) {
            var flow = tab.dataset.flow;
            var count = Array.from(events.values()).filter(function (item) { return item.flow === flow; }).length;
            tab.querySelector(".live-demo__tab-count").textContent = count;
            tab.querySelector(".live-demo__tab-activity").hidden = unseenEvents[flow] === 0;
            tab.setAttribute("aria-label", (flow === "registration" ? "Asset registration" : "A2A commerce") +
                ", " + count + " events" + (unseenEvents[flow] ? ", " + unseenEvents[flow] + " new" : ""));
        });
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

    function expandGroup(flow, key) {
        expandedGroups[flow] = key;
        flowRoots[flow].querySelectorAll(".live-flow-card").forEach(function (card) {
            var expanded = card.dataset.groupKey === key;
            card.classList.toggle("is-expanded", expanded);
            card.querySelector(".live-flow-card__toggle").setAttribute("aria-expanded", String(expanded));
            card.querySelector(".live-demo__feed").hidden = !expanded;
        });
    }

    function groupCard(flow, key, items, expanded, index) {
        var card = document.createElement("article");
        card.className = "live-flow-card" + (expanded ? " is-expanded" : "");
        card.dataset.groupKey = key;
        var head = card.appendChild(document.createElement("header"));
        var toggle = head.appendChild(document.createElement("button"));
        var panelId = "live-flow-" + flow + "-" + index;
        toggle.type = "button";
        toggle.className = "live-flow-card__toggle";
        toggle.setAttribute("aria-expanded", String(expanded));
        toggle.setAttribute("aria-controls", panelId);
        var title = toggle.appendChild(document.createElement("span"));
        title.className = "live-flow-card__title";
        title.appendChild(document.createElement("strong")).textContent = items[0].asset_title || "Untitled work";
        title.appendChild(document.createElement("small")).textContent = "Flow · " + shortKey(key);
        var state = toggle.appendChild(document.createElement("span"));
        state.className = "live-flow-card__state";
        state.textContent = (labels[items[items.length - 1].type] || [items[items.length - 1].type])[0];
        var chevron = toggle.appendChild(document.createElement("span"));
        chevron.className = "live-flow-card__chevron";
        chevron.textContent = "⌄";
        var list = card.appendChild(document.createElement("ol"));
        list.className = "live-demo__feed";
        list.id = panelId;
        list.hidden = !expanded;
        items.forEach(function (item) { list.appendChild(eventNode(item)); });
        toggle.addEventListener("click", function () { expandGroup(flow, key); });
        return card;
    }

    function sortedGroups(groupMap) {
        return Array.from(groupMap.entries()).sort(function (left, right) {
            var leftTime = String(left[1][left[1].length - 1].timestamp || "");
            var rightTime = String(right[1][right[1].length - 1].timestamp || "");
            return rightTime.localeCompare(leftTime);
        });
    }

    function render() {
        var grouped = { registration: new Map(), commerce: new Map() };
        Array.from(events.values()).sort(function (a, b) { return String(a.timestamp).localeCompare(String(b.timestamp)); }).forEach(function (item) {
            var key = item.correlation_id || item.asset_id || "unlinked";
            if (!grouped[item.flow].has(key)) { grouped[item.flow].set(key, []); }
            grouped[item.flow].get(key).push(item);
        });
        Object.keys(flowRoots).forEach(function (flow) {
            var container = flowRoots[flow];
            var groups = sortedGroups(grouped[flow]);
            container.replaceChildren();
            if (groups.length && (!expandedGroups[flow] || !grouped[flow].has(expandedGroups[flow]))) {
                expandedGroups[flow] = groups[0][0];
            }
            groups.forEach(function (group, index) {
                container.appendChild(groupCard(flow, group[0], group[1], expandedGroups[flow] === group[0], index));
            });
            emptyStates[flow].hidden = groups.length > 0;
        });
        skeletons.forEach(function (skeleton) { skeleton.hidden = true; });
        var values = Array.from(events.values());
        var registrationIds = new Set(values.filter(function (item) { return item.flow === "registration"; }).map(function (item) { return item.correlation_id; }));
        document.getElementById("live-metric-events").textContent = values.length;
        document.getElementById("live-metric-registrations").textContent = registrationIds.size;
        document.getElementById("live-metric-negotiations").textContent = values.filter(function (item) { return ["OFFER", "COUNTER", "ACCEPT", "REJECT"].indexOf(item.type) >= 0; }).length;
        document.getElementById("live-metric-settlements").textContent = values.filter(function (item) { return item.type === "PAYMENT_VERIFIED"; }).length;
        updateTabIndicators();
    }

    function consume(items, notifyInactive) {
        (items || []).forEach(function (item) {
            if (!item.event_id) { return; }
            var isNew = !events.has(item.event_id);
            events.set(item.event_id, item);
            if (notifyInactive && isNew && item.flow !== activeFlow) { unseenEvents[item.flow] += 1; }
        });
        render();
    }

    function startPolling() {
        if (fallbackTimer) { return; }
        function refresh() {
            fetch("/api/v1/live-demo/events", { credentials: "same-origin" })
                .then(function (response) { return response.json(); })
                .then(function (data) { setStatus(data.connected, data.connected ? "polling" : data.reason); consume(data.items, true); })
                .catch(function () { setStatus(false, "unavailable"); render(); });
        }
        refresh();
        fallbackTimer = window.setInterval(refresh, 5000);
    }

    tabs.forEach(function (tab, index) {
        tab.addEventListener("click", function () { activateFlow(tab.dataset.flow, false); });
        tab.addEventListener("keydown", function (event) {
            var targetIndex = null;
            if (event.key === "ArrowRight") { targetIndex = (index + 1) % tabs.length; }
            if (event.key === "ArrowLeft") { targetIndex = (index - 1 + tabs.length) % tabs.length; }
            if (event.key === "Home") { targetIndex = 0; }
            if (event.key === "End") { targetIndex = tabs.length - 1; }
            if (targetIndex === null) { return; }
            event.preventDefault();
            activateFlow(tabs[targetIndex].dataset.flow, true);
        });
    });
    activateFlow(activeFlow, false);

    if (!window.EventSource) { startPolling(); return; }
    var stream = new EventSource("/api/v1/live-demo/stream");
    stream.addEventListener("snapshot", function (message) {
        var data = JSON.parse(message.data); setStatus(true, "sse"); consume(data.items, false);
    });
    stream.addEventListener("flow", function (message) { setStatus(true, "sse"); consume([JSON.parse(message.data)], true); });
    stream.addEventListener("offline", function () { stream.close(); startPolling(); });
    stream.onerror = function () { stream.close(); startPolling(); };
}());
