/* Public work gallery: only switches between server-authorized watermarked previews. */
(function () {
    "use strict";

    function initGallery() {
        var gallery = document.querySelector("[data-gallery]");
        if (!gallery) { return; }
        var main = gallery.querySelector("[data-gallery-main]");
        var thumbnails = gallery.querySelectorAll("[data-gallery-thumbnail]");
        thumbnails.forEach(function (thumbnail) {
            thumbnail.addEventListener("click", function () {
                main.src = thumbnail.dataset.imageSrc;
                main.alt = thumbnail.dataset.imageAlt;
                thumbnails.forEach(function (item) {
                    var active = item === thumbnail;
                    item.classList.toggle("is-active", active);
                    item.setAttribute("aria-current", String(active));
                });
            });
        });
    }

    function initSolanaPayModal() {
        var open = document.querySelector("[data-solana-pay-open]");
        var modal = document.getElementById("solana-pay-modal");
        if (!open || !modal) { return; }
        var closeControls = modal.querySelectorAll("[data-solana-pay-close]");
        var send = modal.querySelector("[data-solana-pay-send]");
        var copy = modal.querySelector("[data-solana-pay-copy]");
        var status = document.getElementById("solana-pay-status");

        function showStatus(text, failed) {
            if (!status) { return; }
            status.textContent = text;
            status.classList.toggle("is-error", Boolean(failed));
        }

        function hide(focusTarget) {
            modal.hidden = true;
            if (focusTarget && typeof focusTarget.focus === "function") {
                focusTarget.focus();
                return;
            }
            open.focus();
        }

        function switchToDownload(downloadUrl) {
            if (!downloadUrl) {
                showStatus("다운로드 URL을 발급하지 못했습니다. 잠시 후 다시 시도해주세요.", true);
                return null;
            }
            var download = document.createElement("a");
            download.className = open.className;
            download.href = downloadUrl;
            download.textContent = "다운로드";
            download.setAttribute("download", "");
            download.setAttribute("data-solana-pay-download", "");
            open.replaceWith(download);
            return download;
        }

        function solanaProvider() {
            return window.solana && window.solana.isPhantom ? window.solana : null;
        }

        function decimalSolToLamports(value) {
            var parts = String(value || "0").split(".");
            var whole = parts[0] || "0";
            var fraction = (parts[1] || "").padEnd(9, "0").slice(0, 9);
            return (BigInt(whole) * 1000000000n) + BigInt(fraction || "0");
        }

        function checkPayment() {
            var root = document.querySelector("[data-asset-detail]");
            if (!root || !root.dataset.solpayVerifyUrl || !root.dataset.solpayReference) {
                showStatus("Payment verification is not available.", true);
                return Promise.resolve(false);
            }
            showStatus("결제 확인 중입니다...", false);
            return fetch(root.dataset.solpayVerifyUrl, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ reference: root.dataset.solpayReference })
            }).then(function (response) {
                return response.json().catch(function () { return {}; }).then(function (body) {
                    return { ok: response.ok, status: response.status, body: body };
                });
            }).then(function (result) {
                if (result.ok && result.body.status === "PAID") {
                    var download = switchToDownload(result.body.download_url);
                    if (!download) { return false; }
                    showStatus("결제가 완료되었습니다", false);
                    hide(download);
                    return true;
                }
                if (result.status === 202) {
                    showStatus("아직 결제가 확인되지 않았습니다. 잠시 후 자동으로 다시 확인합니다.", false);
                    return false;
                }
                showStatus(result.body.detail || "결제 확인에 실패했습니다.", true);
                return false;
            }).catch(function () {
                showStatus("결제 확인 중 네트워크 오류가 발생했습니다.", true);
                return false;
            });
        }

        function waitForPayment(attemptsRemaining) {
            return checkPayment().then(function (paid) {
                if (paid || attemptsRemaining <= 1) { return paid; }
                return new Promise(function (resolve) {
                    window.setTimeout(function () {
                        resolve(waitForPayment(attemptsRemaining - 1));
                    }, 2500);
                });
            });
        }

        function sendWithPhantom() {
            var root = document.querySelector("[data-asset-detail]");
            var provider = solanaProvider();
            var web3 = window.solanaWeb3;
            if (!provider) {
                showStatus("Phantom extension is not connected. Install or enable Phantom in this browser.", true);
                return;
            }
            if (!web3) {
                showStatus("Solana transaction library did not load. Refresh and try again.", true);
                return;
            }
            if (!root || !root.dataset.solpayRecipient || !root.dataset.solpayReference || !root.dataset.solpayAmount) {
                showStatus("Payment request is incomplete.", true);
                return;
            }
            send.disabled = true;
            showStatus("Connecting Phantom...", false);
            provider.connect().then(function (connectionResult) {
                var payer = connectionResult.publicKey || provider.publicKey;
                var rpcUrl = root.dataset.solpayRpc || "https://api.devnet.solana.com";
                var connection = new web3.Connection(rpcUrl, "confirmed");
                var recipient = new web3.PublicKey(root.dataset.solpayRecipient);
                var reference = new web3.PublicKey(root.dataset.solpayReference);
                var lamports = decimalSolToLamports(root.dataset.solpayAmount);
                if (lamports <= 0n || lamports > BigInt(Number.MAX_SAFE_INTEGER)) {
                    throw new Error("Unsupported SOL amount.");
                }
                var transferInstruction = web3.SystemProgram.transfer({
                    fromPubkey: payer,
                    toPubkey: recipient,
                    lamports: Number(lamports)
                });
                transferInstruction.keys.push({
                    pubkey: reference,
                    isSigner: false,
                    isWritable: false
                });
                var memoInstruction = new web3.TransactionInstruction({
                    keys: [],
                    programId: new web3.PublicKey("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"),
                    data: new TextEncoder().encode(root.dataset.solpayMemo || "")
                });
                var transaction = new web3.Transaction().add(transferInstruction, memoInstruction);
                transaction.feePayer = payer;
                return connection.getLatestBlockhash("confirmed").then(function (blockhash) {
                    transaction.recentBlockhash = blockhash.blockhash;
                    showStatus("Confirm the Devnet SOL payment in Phantom...", false);
                    return provider.signTransaction(transaction).then(function (signedTransaction) {
                        return connection.sendRawTransaction(signedTransaction.serialize()).then(function (signature) {
                            showStatus("거래가 제출되었습니다. Devnet 확인을 기다리는 중입니다...", false);
                            return connection.confirmTransaction({
                                signature: signature,
                                blockhash: blockhash.blockhash,
                                lastValidBlockHeight: blockhash.lastValidBlockHeight
                            }, "confirmed").then(function () {
                                showStatus("거래가 확인되었습니다. 결제 상태를 확인하는 중입니다...", false);
                                return waitForPayment(6);
                            });
                        });
                    });
                });
            }).catch(function (error) {
                showStatus(error && error.message ? error.message : "Phantom payment failed.", true);
            }).finally(function () {
                send.disabled = false;
            });
        }

        function show() {
            modal.hidden = false;
            showStatus("", false);
            if (send) { send.focus(); }
        }

        open.addEventListener("click", function () {
            show();
        });
        closeControls.forEach(function (control) {
            control.addEventListener("click", hide);
        });
        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && !modal.hidden) { hide(); }
        });
        if (copy) {
            copy.addEventListener("click", function () {
                var root = document.querySelector("[data-asset-detail]");
                var value = root && root.dataset.solpayRecipient ? root.dataset.solpayRecipient : "";
                if (!value) {
                    showStatus("Recipient public key is not available.", true);
                    return;
                }
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(value).then(function () {
                        showStatus("Recipient public key copied.", false);
                    }).catch(function () {
                        showStatus("Copy failed. Select the recipient public key manually.", true);
                    });
                    return;
                }
                showStatus("Clipboard is not available in this browser.", true);
            });
        }
        if (send) {
            send.addEventListener("click", sendWithPhantom);
        }
    }

    function init() {
        initGallery();
        initSolanaPayModal();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
}());
