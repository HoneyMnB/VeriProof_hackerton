/*! Authenticated Firestore live-demo visualization. */
(function () {
    "use strict";
    var root = document.querySelector(".live-demo");
    if (!root) { return; }
    var feed = document.getElementById("live-demo-feed");
    var empty = document.getElementById("live-demo-empty");
    var skeleton = document.getElementById("live-demo-skeleton");
    var connection = document.getElementById("live-demo-connection");
    var seen = new Set();

    var labels = {
        ANCHORED: ["Work anchored", "proof"], HTTP_402: ["Payment requested", "chain"],
        OFFER: ["Offer submitted", "ai"], COUNTER: ["Counter offer", "ai"],
        ACCEPT: ["Terms accepted", "ai"], REJECT: ["Offer declined", "ai"],
        PAYMENT_VERIFIED: ["Payment verified", "chain"], CERT_ISSUED: ["License issued", "proof"],
        ROYALTY_SPLIT: ["Royalty distributed", "chain"], BATCH_SETTLED: ["Batch settled", "chain"]
    };

    function status(connected, reason) {
        connection.classList.toggle("is-connected", connected);
        connection.classList.toggle("is-error", !connected);
        connection.querySelector("strong").textContent = connected ? "Live" : "Offline";
        connection.querySelector("small").textContent = connected ? "Firestore connected" :
            (reason === "disabled" ? "Firestore is not enabled" : "Firestore is unavailable");
    }

    function detail(item) {
        var payload = item.payload || {};
        var parts = [];
        ["offer_sol", "counter_sol", "price_sol"].some(function (key) {
            if (payload[key] !== undefined) { parts.push(payload[key] + " SOL"); return true; }
            return false;
        });
        ["offer_usdc", "counter_usdc", "price_usdc"].some(function (key) {
            if (payload[key] !== undefined) { parts.push(payload[key] + " USDC"); return true; }
            return false;
        });
        if (payload.usage_type) { parts.push(String(payload.usage_type).replace("-", " ")); }
        if (payload.round !== undefined) { parts.push("round " + payload.round); }
        return parts.join(" · ") || "Verified state transition";
    }

    function renderItem(item) {
        var meta = labels[item.type] || [item.type.replace(/_/g, " "), "ai"];
        var row = document.createElement("li");
        var time = item.timestamp ? new Date(item.timestamp) : null;
        row.className = "live-event is-" + meta[1];
        row.dataset.eventId = item.event_id;
        row.innerHTML = '<span class="live-event__node"></span><time></time><div class="live-event__body"><div><strong></strong><span></span></div><p></p></div><span class="live-event__type"></span>';
        row.querySelector("time").textContent = time && !isNaN(time) ? time.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "Now";
        row.querySelector("strong").textContent = meta[0];
        row.querySelector(".live-event__body span").textContent = item.asset_title;
        row.querySelector("p").textContent = detail(item);
        row.querySelector(".live-event__type").textContent = item.type.replace(/_/g, " ");
        return row;
    }

    function render(data) {
        status(data.connected, data.reason);
        skeleton.hidden = true;
        var metrics = data.metrics || {};
        ["events", "assets", "negotiations", "settlements"].forEach(function (name) {
            document.getElementById("live-metric-" + name).textContent = metrics[name] === undefined ? "—" : metrics[name];
        });
        (data.items || []).forEach(function (item) {
            if (!seen.has(item.event_id)) { seen.add(item.event_id); feed.appendChild(renderItem(item)); }
        });
        while (feed.children.length > 40) { feed.removeChild(feed.firstElementChild); }
        empty.hidden = !data.connected || feed.children.length > 0;
    }

    function refresh() {
        fetch("/api/v1/live-demo/events", { credentials: "same-origin" })
            .then(function (response) { return response.json().then(function (body) { body.httpOk = response.ok; return body; }); })
            .then(render)
            .catch(function () { skeleton.hidden = true; status(false, "unavailable"); empty.hidden = true; });
    }
    refresh();
    window.setInterval(refresh, 2500);
}());
