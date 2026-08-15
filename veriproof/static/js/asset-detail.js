/* Public asset UI: gallery plus sponsor-paid USDC checkout. */
(function () {
    "use strict";

    function initGallery() {
        var gallery = document.querySelector("[data-gallery]");
        if (!gallery) { return; }
        var main = gallery.querySelector("[data-gallery-main]");
        gallery.querySelectorAll("[data-gallery-thumbnail]").forEach(function (thumbnail) {
            thumbnail.addEventListener("click", function () {
                main.src = thumbnail.dataset.imageSrc;
                main.alt = thumbnail.dataset.imageAlt;
                gallery.querySelectorAll("[data-gallery-thumbnail]").forEach(function (item) {
                    var active = item === thumbnail;
                    item.classList.toggle("is-active", active);
                    item.setAttribute("aria-current", String(active));
                });
            });
        });
    }

    function initSponsoredUsdcModal() {
        var root = document.querySelector("[data-asset-detail]");
        var open = document.querySelector("[data-sponsored-usdc-open]");
        var modal = document.getElementById("sponsored-usdc-modal");
        if (!root || !open || !modal) { return; }
        var send = modal.querySelector("[data-sponsored-usdc-send]");
        var status = document.getElementById("sponsored-usdc-status");

        function showStatus(message, failed) {
            status.textContent = message;
            status.classList.toggle("is-error", Boolean(failed));
        }
        function provider() {
            return window.solana && window.solana.isPhantom ? window.solana : null;
        }
        function csrfHeaders() {
            return { "Content-Type": "application/json", "X-CSRFToken": root.dataset.csrfToken || "" };
        }
        function jsonResponse(response) {
            return response.json().catch(function () { return {}; }).then(function (body) {
                return { ok: response.ok, status: response.status, body: body };
            });
        }
        function close() {
            modal.hidden = true;
            open.focus();
        }
        function show() {
            modal.hidden = false;
            showStatus("", false);
            send.focus();
        }
        function switchToDownload(url) {
            if (!url) { throw new Error("다운로드 URL을 발급하지 못했습니다."); }
            var download = document.createElement("a");
            download.className = open.className;
            download.href = url;
            download.download = "";
            download.textContent = "다운로드";
            open.replaceWith(download);
            close();
        }
        function waitForSettlement(intentId, signature, remaining) {
            return fetch(root.dataset.sponsoredUsdcUrl + "/settle", {
                method: "POST", headers: csrfHeaders(),
                body: JSON.stringify({ intent_id: intentId, transaction_signature: signature })
            }).then(jsonResponse).then(function (result) {
                if (result.ok && result.body.status === "PAID") {
                    switchToDownload(result.body.download_url);
                    return;
                }
                if (result.status === 202 && remaining > 0) {
                    showStatus("거래 최종 확정을 기다리는 중입니다…", false);
                    return new Promise(function (resolve) { window.setTimeout(resolve, 2500); }).then(function () {
                        return waitForSettlement(intentId, signature, remaining - 1);
                    });
                }
                throw new Error(result.body.detail || "USDC 결제를 확인하지 못했습니다.");
            });
        }
        function pay() {
            var wallet = provider();
            var web3 = window.solanaWeb3;
            if (!wallet || !web3) {
                showStatus("Phantom 지갑을 사용할 수 없습니다.", true);
                return;
            }
            send.disabled = true;
            showStatus("Phantom 지갑에 연결 중입니다…", false);
            wallet.connect().then(function (connection) {
                var buyer = connection.publicKey || wallet.publicKey;
                if (!buyer) { throw new Error("지갑 주소를 확인할 수 없습니다."); }
                return fetch(root.dataset.sponsoredUsdcUrl, {
                    method: "POST", headers: csrfHeaders(),
                    body: JSON.stringify({ buyer_wallet: buyer.toString() })
                }).then(jsonResponse);
            }).then(function (result) {
                if (!result.ok) { throw new Error(result.body.detail || "결제 요청을 만들지 못했습니다."); }
                var transaction = web3.Transaction.from(Uint8Array.from(atob(result.body.transaction), function (c) { return c.charCodeAt(0); }));
                showStatus("USDC 전송을 Phantom에서 승인해주세요. 네트워크 수수료는 VeriProof가 부담합니다.", false);
                return wallet.signTransaction(transaction).then(function (signed) {
                    var rpc = new web3.Connection(root.dataset.solanaRpc || "https://api.devnet.solana.com", "confirmed");
                    return rpc.sendRawTransaction(signed.serialize()).then(function (signature) {
                        showStatus("거래가 제출되었습니다. 최종 확정을 확인 중입니다…", false);
                        return waitForSettlement(result.body.intent_id, signature, 12);
                    });
                });
            }).catch(function (error) {
                showStatus(error && error.message ? error.message : "USDC 결제에 실패했습니다.", true);
            }).finally(function () { send.disabled = false; });
        }

        open.addEventListener("click", show);
        modal.querySelectorAll("[data-sponsored-usdc-close]").forEach(function (control) { control.addEventListener("click", close); });
        document.addEventListener("keydown", function (event) { if (event.key === "Escape" && !modal.hidden) { close(); } });
        send.addEventListener("click", pay);
    }

    function init() { initGallery(); initSponsoredUsdcModal(); }
    if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", init); } else { init(); }
}());
