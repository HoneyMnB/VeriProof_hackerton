/*!
 * VeriProof AI — SPEC-005 shared pure helpers (vanilla JS).
 *
 * Mirror 1:1 of the Python SSOT in ``apps/ip/dashboard.py``. Kept in sync so
 * the offline pytest suite verifies the data contracts this file implements.
 * Exposed on ``window.VP`` (no module / build step) and consumed by
 * ``library.js`` and ``workspace.js``.
 *
 * Coverage:
 * - R7 / AC-6: explorerUrl(anchorTxSig, cluster?) -> string | null
 * - R8 / AC-7: buildCertificatePayload(asset, certificateTxSig?) -> object
 * - R6 / AC-5: previewSrc(watermarkUrl, thumbnailUrl, showWatermark) -> string
 * - R10 / AC-9: shouldPollEvents({firestoreEnabled, firebaseSdkPresent}) -> bool
 * - R2 / AC-2: analysisCardFields(response) -> object
 */
(function (global) {
    "use strict";

    var EXPLORER_BASE = "https://explorer.solana.com";
    var DEFAULT_CLUSTER = "devnet";

    // R7 / AC-6: Solana Explorer URL builder (None/empty -> null for draft assets).
    function explorerUrl(anchorTxSig, cluster) {
        if (!anchorTxSig) { return null; }
        var c = cluster || DEFAULT_CLUSTER;
        return EXPLORER_BASE + "/tx/" + anchorTxSig + "?cluster=" + c;
    }

    // R8 / AC-7: certificate QR payload — on-chain proof ONLY, no original bytes/url.
    function buildCertificatePayload(asset, certificateTxSig) {
        return {
            asset_id: asset.asset_id,
            image_sha256: asset.image_sha256,
            anchor_tx_sig: asset.anchor_tx_sig || null,
            certificate_tx_sig: certificateTxSig || null,
            creator_wallet: asset.creator_wallet,
            explorer_url: explorerUrl(asset.anchor_tx_sig)
        };
    }

    // R6 / AC-5: preview src switch (watermark <-> thumbnail; never original).
    function previewSrc(watermarkUrl, thumbnailUrl, showWatermark) {
        return showWatermark ? watermarkUrl : thumbnailUrl;
    }

    // R10 / AC-9: polling decision — poll unless Firestore AND SDK are both live.
    function shouldPollEvents(opts) {
        var fs = !!opts.firestoreEnabled;
        var sdk = !!opts.firebaseSdkPresent;
        return !(fs && sdk);
    }

    // R2 / AC-2 + R4 / AC-3: analysis + completion card field extraction.
    function analysisCardFields(response) {
        var analysis = (response && response.analysis) || {};
        return {
            asset_id: response ? response.asset_id : null,
            anchor_tx: response ? response.anchor_tx : null,
            x402_endpoint: response ? response.x402_endpoint : null,
            tags: Array.isArray(analysis.tags) ? analysis.tags.slice() : [],
            category: analysis.category || null,
            originality_score: analysis.originality_score,
            recommended_min_price_usdc: analysis.recommended_min_price_usdc,
            degraded: !!analysis.degraded
        };
    }

    /**
     * Minimal dependency-free QR rendering fallback.
     *
     * The hackathon offline constraint forbids a vendored QR library / build
     * step. When a real QR encoder is unavailable we render a "scan block"
     * containing the canonical proof URL (explorer_url) plus the payload fields
     * — this is the documented QR fallback per the SPEC-005 task instructions.
     * If a global ``VP.renderQr`` override is installed (e.g. a future vendored
     * encoder), it takes precedence.
     */
    function renderQr(container, payload) {
        if (!container) { return; }
        // Honour an injected encoder if present (progressive enhancement).
        if (typeof global.VP !== "undefined" && typeof global.VP.__encodeQr === "function") {
            container.innerHTML = "";
            container.appendChild(global.VP.__encodeQr(payload));
            return;
        }
        // Fallback: a scannable text block of the proof URL + payload.
        var url = payload.explorer_url || (document.location.origin + "/library?asset=" + payload.asset_id);
        var block = document.createElement("div");
        block.className = "scan-block";
        block.textContent = url + "\n\nasset: " + payload.asset_id + "\nsha256: " + (payload.image_sha256 || "").slice(0, 16) + "…";
        container.innerHTML = "";
        container.appendChild(block);
    }

    // Merge into the existing window.VP. vp-i18n.js (loaded in <head>) may
    // already have added VP.i18n; reassigning a fresh object here would wipe it,
    // so extend the shared namespace instead. shell.js / page scripts do the same.
    global.VP = global.VP || {};
    global.VP.explorerUrl = explorerUrl;
    global.VP.buildCertificatePayload = buildCertificatePayload;
    global.VP.previewSrc = previewSrc;
    global.VP.shouldPollEvents = shouldPollEvents;
    global.VP.analysisCardFields = analysisCardFields;
    global.VP.renderQr = renderQr;
})(typeof window !== "undefined" ? window : this);
