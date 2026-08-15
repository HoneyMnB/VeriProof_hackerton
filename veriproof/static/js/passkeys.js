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

    function request(url, method, body) {
        return fetch(url, {
            method: method, credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
            body: body === undefined ? undefined : JSON.stringify(body || {})
        }).then(function (response) {
            return response.json().then(function (data) {
                if (!response.ok) {
                    var error = new Error(data.detail || "Passkey request failed.");
                    error.code = data.error || "passkey_failed";
                    throw error;
                }
                return data;
            });
        });
    }

    function post(url, body) { return request(url, "POST", body); }

    function friendlyError(error) {
        if (error && error.name === "NotAllowedError") { return "Passkey authentication was cancelled or timed out."; }
        if (error && error.name === "InvalidStateError") { return "This passkey is already registered."; }
        return error && error.message ? error.message : "Passkey request failed.";
    }

    function authenticate(optionsUrl, verifyUrl, optionsBody) {
        if (!window.PublicKeyCredential || !navigator.credentials) {
            return Promise.reject(new Error("This browser does not support passkeys."));
        }
        return post(optionsUrl, optionsBody || {})
            .then(function (options) { return navigator.credentials.get({ publicKey: requestOptions(options) }); })
            .then(function (credential) { return post(verifyUrl, { credential: credentialJson(credential) }); });
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
            authenticate(
                "/accounts/passkeys/login/options/",
                "/accounts/passkeys/login/verify/",
                { next: button.dataset.next || "/" }
            )
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
                .then(function () {
                    status.textContent = "Passkey registered successfully.";
                    name.value = "";
                    document.dispatchEvent(new CustomEvent("veriproof:passkey-registered"));
                })
                .catch(function (error) { status.textContent = friendlyError(error); status.classList.add("is-error"); })
                .finally(function () { button.disabled = false; });
        });
    }

    function bindManagement() {
        var list = document.getElementById("passkey-credential-list");
        var skeleton = document.getElementById("passkey-list-skeleton");
        var empty = document.getElementById("passkey-list-empty");
        var count = document.getElementById("passkey-list-count");
        var status = document.getElementById("passkey-management-status");
        if (!list) { return; }

        function formatDate(value) {
            if (!value) { return "Never used"; }
            var date = new Date(value);
            if (Number.isNaN(date.getTime())) { return "Unknown"; }
            return "Last used " + new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
        }

        function render(items) {
            list.replaceChildren();
            items.forEach(function (credential) {
                var item = document.createElement("li");
                item.className = "vp-passkey-list__item";
                var icon = item.appendChild(document.createElement("span"));
                icon.className = "vp-passkey-list__icon";
                icon.setAttribute("aria-hidden", "true");
                icon.textContent = "◇";
                var body = item.appendChild(document.createElement("span"));
                body.className = "vp-passkey-list__body";
                body.appendChild(document.createElement("strong")).textContent = credential.device_name || "Passkey";
                var meta = body.appendChild(document.createElement("small"));
                var transport = (credential.transports || []).join(" · ");
                meta.textContent = formatDate(credential.last_used_at) + (transport ? " · " + transport : "");
                var remove = item.appendChild(document.createElement("button"));
                remove.type = "button";
                remove.className = "vp-passkey-list__remove";
                remove.textContent = "Remove";
                remove.setAttribute("aria-label", "Remove " + (credential.device_name || "passkey"));
                var confirmTimer = null;
                remove.addEventListener("click", function () {
                    if (!remove.classList.contains("is-confirming")) {
                        remove.classList.add("is-confirming");
                        remove.textContent = "Confirm";
                        remove.setAttribute("aria-label", "Confirm removal of " + (credential.device_name || "passkey"));
                        status.textContent = "Click Confirm to remove this passkey.";
                        window.clearTimeout(confirmTimer);
                        confirmTimer = window.setTimeout(function () {
                            remove.classList.remove("is-confirming");
                            remove.textContent = "Remove";
                            remove.setAttribute("aria-label", "Remove " + (credential.device_name || "passkey"));
                            if (status.textContent === "Click Confirm to remove this passkey.") { status.textContent = ""; }
                        }, 5000);
                        return;
                    }
                    window.clearTimeout(confirmTimer);
                    remove.disabled = true;
                    status.textContent = "Removing passkey...";
                    status.classList.remove("is-error");
                    request("/accounts/passkeys/" + encodeURIComponent(credential.id) + "/", "DELETE")
                        .then(function () {
                            status.textContent = "Passkey removed.";
                            return load();
                        })
                        .then(function (items) {
                            document.dispatchEvent(new CustomEvent("veriproof:passkey-deleted", { detail: { count: items.length } }));
                        })
                        .catch(function (error) {
                            status.textContent = error.message;
                            status.classList.add("is-error");
                            remove.disabled = false;
                        });
                });
                list.appendChild(item);
            });
            skeleton.hidden = true;
            list.hidden = items.length === 0;
            empty.hidden = items.length !== 0;
            count.textContent = String(items.length);
        }

        function load() {
            skeleton.hidden = false;
            empty.hidden = true;
            return fetch("/accounts/passkeys/", { credentials: "same-origin" })
                .then(function (response) {
                    return response.json().then(function (data) {
                        if (!response.ok) { throw new Error(data.detail || "Could not load passkeys."); }
                        return data.items || [];
                    });
                })
                .then(function (items) { render(items); return items; })
                .catch(function (error) {
                    skeleton.hidden = true;
                    list.hidden = true;
                    empty.hidden = false;
                    empty.textContent = error.message;
                    status.classList.add("is-error");
                });
        }

        document.addEventListener("veriproof:passkey-registered", load);
        load();
    }

    window.VPPasskeys = { authenticate: authenticate, friendlyError: friendlyError };

    document.addEventListener("DOMContentLoaded", function () { bindLogin(); bindRegistration(); bindManagement(); });
}());
