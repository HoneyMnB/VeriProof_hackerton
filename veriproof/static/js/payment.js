/* 사람 구매자의 단일 즉시결제 확인 흐름. 장바구니 상태를 만들지 않는다. */
(function () {
    "use strict";

    function init() {
        var root = document.querySelector("[data-asset-detail]");
        var form = document.getElementById("payment-proof-form");
        if (!root || !form) { return; }
        var status = document.getElementById("payment-status");
        var wallet = document.getElementById("buyer-wallet");
        var tx = document.getElementById("payment-tx");
        var t = window.VP && window.VP.i18n ? window.VP.i18n.t : function (key) { return key; };
        function show(text, failed) { status.textContent = text; status.classList.toggle("is-error", Boolean(failed)); }
        function settle(signature) {
            if (!wallet.value.trim()) { show(t("detail.status.wallet_required"), true); wallet.focus(); return; }
            show(t("detail.status.verifying"), false);
            fetch(root.dataset.settleUrl, {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ tx_signature: signature, buyer_wallet: wallet.value.trim() })
            }).then(function (response) {
                return response.json().catch(function () { return {}; }).then(function (body) { return { ok: response.ok, body: body }; });
            }).then(function (result) {
                if (!result.ok || !result.body.download_url) { show(result.body.detail || t("detail.status.failed"), true); return; }
                var link = document.createElement("a");
                link.href = result.body.download_url; link.textContent = t("detail.download");
                link.className = "asset-detail__download";
                status.replaceChildren(link); status.classList.remove("is-error");
            }).catch(function () { show(t("detail.status.network"), true); });
        }
        form.addEventListener("submit", function (event) { event.preventDefault(); settle(tx.value.trim()); });
        var demo = document.getElementById("local-demo-pay");
        if (demo) { demo.addEventListener("click", function () { settle("mock:browser:" + crypto.randomUUID()); }); }
    }
    if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", init); } else { init(); }
}());
