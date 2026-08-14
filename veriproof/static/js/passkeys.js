/*! VeriProof WebAuthn passkey registration and passwordless login. */
(function () {
    "use strict";

    function csrfToken() {
        var input = document.querySelector("[name=csrfmiddlewaretoken]");
        return input ? input.value : "";
    }

    function decode(value) {
        var normalized = value.replace(/-/g, "+").replace(/_/g, "/");
        var binary = atob(normalized + "=".repeat((4 - normalized.length % 4) % 4));
        return Uint8Array.from(binary, function (character) { return character.charCodeAt(0); });
    }

    function creationOptions(value) {
        value.challenge = decode(value.challenge);
        value.user.id = decode(value.user.id);
        (value.excludeCredentials || []).forEach(function (item) { item.id = decode(item.id); });
        return value;
    }

    function requestOptions(value) {
        value.challenge = decode(value.challenge);
        (value.allowCredentials || []).forEach(function (item) { item.id = decode(item.id); });
        return value;
    }

    function credentialJson(credential) {
        if (credential.toJSON) { return credential.toJSON(); }
        function encode(buffer) {
            var bytes = new Uint8Array(buffer);
            var binary = "";
            bytes.forEach(function (byte) { binary += String.fromCharCode(byte); });
            return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
        }
        var response = credential.response;
        var result = {
            id: credential.id, rawId: encode(credential.rawId), type: credential.type,
            authenticatorAttachment: credential.authenticatorAttachment,
            clientExtensionResults: credential.getClientExtensionResults(), response: {
                clientDataJSON: encode(response.clientDataJSON),
                authenticatorData: response.authenticatorData ? encode(response.authenticatorData) : undefined,
                signature: response.signature ? encode(response.signature) : undefined,
                userHandle: response.userHandle ? encode(response.userHandle) : null,
                attestationObject: response.attestationObject ? encode(response.attestationObject) : undefined,
                transports: response.getTransports ? response.getTransports() : []
            }
        };
        Object.keys(result.response).forEach(function (key) {
            if (result.response[key] === undefined) { delete result.response[key]; }
        });
        return result;
    }

    function post(url, body) {
        return fetch(url, {
            method: "POST", credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
            body: JSON.stringify(body || {})
        }).then(function (response) {
            return response.json().then(function (data) {
                if (!response.ok) { throw new Error(data.detail || "Passkey request failed."); }
                return data;
            });
        });
    }

    function friendlyError(error) {
        if (error && error.name === "NotAllowedError") { return "Passkey authentication was cancelled or timed out."; }
        if (error && error.name === "InvalidStateError") { return "This passkey is already registered."; }
        return error && error.message ? error.message : "Passkey request failed.";
    }

    function bindLogin() {
        var button = document.getElementById("passkey-login-button");
        var status = document.getElementById("passkey-login-status");
        if (!button) { return; }
        if (!window.PublicKeyCredential || !navigator.credentials) {
            button.hidden = true;
            status.textContent = "This browser does not support passkeys.";
            return;
        }
        button.addEventListener("click", function () {
            button.disabled = true; status.textContent = "Waiting for your device...";
            post("/accounts/passkeys/login/options/", { next: button.dataset.next || "/" })
                .then(function (options) { return navigator.credentials.get({ publicKey: requestOptions(options) }); })
                .then(function (credential) { return post("/accounts/passkeys/login/verify/", { credential: credentialJson(credential) }); })
                .then(function (result) { window.location.assign(result.redirect || "/"); })
                .catch(function (error) { status.textContent = friendlyError(error); button.disabled = false; });
        });
    }

    function bindRegistration() {
        var button = document.getElementById("passkey-register-button");
        var status = document.getElementById("passkey-register-status");
        var name = document.getElementById("passkey-device-name");
        if (!button) { return; }
        if (!window.PublicKeyCredential || !navigator.credentials) {
            button.disabled = true; status.textContent = "This browser does not support passkeys.";
            return;
        }
        button.addEventListener("click", function () {
            button.disabled = true; status.textContent = "Waiting for your device...";
            post("/accounts/passkeys/register/options/", {})
                .then(function (options) { return navigator.credentials.create({ publicKey: creationOptions(options) }); })
                .then(function (credential) {
                    return post("/accounts/passkeys/register/verify/", {
                        credential: credentialJson(credential), device_name: name.value.trim()
                    });
                })
                .then(function () { status.textContent = "Passkey registered successfully."; name.value = ""; })
                .catch(function (error) { status.textContent = friendlyError(error); status.classList.add("is-error"); })
                .finally(function () { button.disabled = false; });
        });
    }

    document.addEventListener("DOMContentLoaded", function () { bindLogin(); bindRegistration(); });
}());
