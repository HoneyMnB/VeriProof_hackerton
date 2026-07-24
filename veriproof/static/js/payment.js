/* 사람 구매자의 단일 즉시결제 확인 흐름. 장바구니 상태를 만들지 않는다. */
(function () {
    "use strict";

    /**
     * 결제 확인 UI를 초기화한다. 자산 상세 루트와 결제 폼이 함께 존재할 때만
     * 상태·지갑·tx 입력 요소를 찾아 폼 제출과 로컬 데모 결제 버튼을 바인딩한다.
     */
    function init() {
        var root = document.querySelector("[data-asset-detail]");
        var form = document.getElementById("payment-proof-form");
        if (!root || !form) { return; }
        var status = document.getElementById("payment-status");
        var wallet = document.getElementById("buyer-wallet");
        var tx = document.getElementById("payment-tx");
        var t = window.VP && window.VP.i18n ? window.VP.i18n.t : function (key) { return key; };
        function show(text, failed) { status.textContent = text; status.classList.toggle("is-error", Boolean(failed)); }
        /**
         * 서버 settle 엔드포인트로 tx signature와 구매자 지갑을 보내 즉시결제를 확정한다.
         * 성공 시 상태 영역을 다운로드 링크로 교체하고, 검증 실패·네트워크 오류는 상태 영역에 에러로 표시한다.
         * @param {string} signature - 결제 트랜잭션 서명(로컬 데모 결제는 "mock:browser:<uuid>").
         */
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
