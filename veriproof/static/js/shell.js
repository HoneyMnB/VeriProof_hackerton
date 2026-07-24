/*!
 * VeriProof AI — shared creator sidebar controller (UX-003).
 *
 * Owns the single sidebar module rendered by ``_creator_sidebar.html`` on every
 * internal page (Workspace / Library / Sandbox): sidebar collapse, wallet
 * connect, workspace summary, agent directives, verified actions, sales
 * results, expense entry, and the workspace-only "New chat" affordance.
 *
 * Contract with page scripts (no import step):
 *  - Exposes ``VP.getWallet`` / ``VP.requestJson`` / ``VP.appendAction`` /
 *    ``VP.refreshSummary`` on the shared ``window.VP`` namespace.
 *  - Dispatches ``vp:wallet-changed`` ({detail:{wallet}}) after a wallet save so
 *    each page refreshes its own surface (Workspace reloads chat history,
 *    Library / Sandbox reload the page to re-render server-side).
 *  - Dispatches ``vp:new-chat`` when the "New chat" button is pressed; only
 *    Workspace listens (the control only exists there).
 */
(function (global) {
    "use strict";

    function byId(id) { return document.getElementById(id); }

    function getWallet() {
        var shell = document.querySelector("[data-chat-shell]");
        return ((shell && shell.dataset.creatorWallet) || "").trim();
    }

    function csrfToken() {
        var match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : "";
    }

    function requestJson(url, options) {
        return fetch(url, options).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (body) {
                return { ok: response.ok, status: response.status, body: body };
            });
        });
    }

    // Locale-aware USDC formatting (Intl.NumberFormat per Web Interface Guidelines).
    var USD_FORMAT = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    function formatUsd(value) {
        var n = Number(value);
        return (isFinite(n) ? USD_FORMAT.format(n) : String(value == null ? "0" : value)) + " USDC";
    }

    function setStatus(text, isError) {
        // Workspace exposes #assistant-status; Library/Sandbox have no status
        // surface and rely on a page reload for feedback instead.
        var s = byId("assistant-status");
        if (!s) { return; }
        s.textContent = text || "";
        s.classList.toggle("is-error", Boolean(isError));
    }

    function init() {
        var shell = document.querySelector("[data-chat-shell]");
        if (!shell) { return; }
        var walletInput = byId("creator-wallet");
        // i18n helper — VP.i18n loads in <head> and survives dashboard.js's merge
        // into window.VP, so it is available when this init runs.
        var t = (global.VP && global.VP.i18n && global.VP.i18n.t) || function (k) { return k; };
        bindSidebarToggle(shell);
        bindNewChat();
        bindHistory();
        bindAccountSettings();
        // 지갑·정산 상세 기능은 계정/자산 화면으로 이동했다. 공통 셸은 탐색과
        // 계정 설정만 담당하며, 작업 화면에는 계정 저장 지갑만 제공한다.

        // Page-facing hooks (Workspace registers a chat action, refreshes after
        // registering a work). Defined inside init so they close over the loaders.
        global.VP = global.VP || {};
        global.VP.getWallet = getWallet;
        global.VP.requestJson = requestJson;
        global.VP.appendAction = appendAction;
        global.VP.refreshSummary = refreshSummary;

        function bindSidebarToggle(root) {
            var toggle = byId("sidebar-toggle");
            if (!toggle) { return; }
            // Keep the toggle's accessible name + expanded state in sync with the
            // collapsed class so screen readers announce the current affordance.
            function applyToggleState() {
                var collapsed = root.classList.contains("is-sidebar-collapsed");
                toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
                var label = t(collapsed ? "sidebar.expand" : "sidebar.collapse");
                toggle.setAttribute("aria-label", label);
                toggle.title = label;
            }
            // Re-apply the toggle's accessible label when the UI language changes.
            global.addEventListener("vp:language-change", applyToggleState);
            toggle.addEventListener("click", function () {
                root.classList.toggle("is-sidebar-collapsed");
                localStorage.setItem("veriproof.sidebarCollapsed", root.classList.contains("is-sidebar-collapsed") ? "1" : "0");
                applyToggleState();
            });
            if (localStorage.getItem("veriproof.sidebarCollapsed") === "1") { root.classList.add("is-sidebar-collapsed"); }
            applyToggleState();
        }

        function bindNewChat() {
            var btn = byId("new-chat");
            // Only a real <button> (Workspace) is interactive here; on other
            // pages the partial renders an <a> link to "/" so nothing to bind.
            if (!btn || btn.tagName !== "BUTTON") { return; }
            btn.addEventListener("click", function () {
                global.dispatchEvent(new CustomEvent("vp:new-chat"));
            });
        }

        function bindHistory() {
            var modal = byId("history-modal");
            var open = byId("open-history");
            var list = byId("history-list");
            var count = byId("history-count");
            var search = byId("history-search");
            var searchForm = byId("history-search-form");
            var recent = byId("recent-history");
            var recentList = byId("recent-history-list");
            if (!modal || !open || !list || !search || !searchForm || !recent || !recentList) { return; }
            var conversations = [];
            var modalConversations = [];
            var activeHistoryMenu = null;

            // 최근 목록과 전체 보기 모두 사용자 발화의 제목만 표시한다. AI 응답 본문,
            // 역할, 시간은 히스토리 탐색 UI에 노출하지 않는다.
            function historyTitleForModal(title) {
                var fullTitle = String(title || "");
                var limit = /[\uac00-\ud7a3]/.test(fullTitle) ? 30 : 50;
                var characters = Array.from(fullTitle);
                return characters.length > limit ? characters.slice(0, limit).join("") + "…" : fullTitle;
            }

            function historyTitleForSidebar(title) {
                var fullTitle = String(title || "");
                var hasLatinLetter = /[A-Za-z]/.test(fullTitle);
                var hasCjkOrHangul = /[\u3400-\u9fff\uf900-\ufaff\uac00-\ud7af]/.test(fullTitle);
                var limit = hasLatinLetter && !hasCjkOrHangul ? 30 : 20;
                var characters = Array.from(fullTitle);
                return characters.length > limit ? characters.slice(0, limit).join("") + "..." : fullTitle;
            }

            function closeHistoryMenu(returnFocus) {
                if (!activeHistoryMenu) { return; }
                activeHistoryMenu.menu.hidden = true;
                activeHistoryMenu.button.setAttribute("aria-expanded", "false");
                if (returnFocus) { activeHistoryMenu.button.focus(); }
                activeHistoryMenu = null;
            }

            function updateConversationTitle(conversationId, title) {
                [conversations, modalConversations].forEach(function (items) {
                    items.forEach(function (conversation) {
                        if (conversation.conversation_id === conversationId) { conversation.title = title; }
                    });
                });
            }

            function deleteConversation(item, onSuccess, onFailure) {
                requestJson("/api/v1/assistant/conversations/" + encodeURIComponent(item.conversation_id), {
                    method: "DELETE",
                    headers: { "X-CSRFToken": csrfToken() }
                }).then(function (result) {
                    if (!result.ok) {
                        setStatus(result.body.detail || t("sidebar.history.action_failed"), true);
                        if (onFailure) { onFailure(); }
                        return;
                    }
                    conversations = conversations.filter(function (conversation) { return conversation.conversation_id !== item.conversation_id; });
                    modalConversations = modalConversations.filter(function (conversation) { return conversation.conversation_id !== item.conversation_id; });
                    render();
                    global.dispatchEvent(new CustomEvent("vp:conversation-deleted", { detail: { conversationId: item.conversation_id } }));
                    if (onSuccess) { onSuccess(); }
                }).catch(function () {
                    setStatus(t("sidebar.history.action_failed"), true);
                    if (onFailure) { onFailure(); }
                });
            }

            function showRenameForm(menu, item) {
                menu.replaceChildren();
                var form = document.createElement("form");
                var input = document.createElement("input");
                var actions = document.createElement("div");
                var save = document.createElement("button");
                var cancel = document.createElement("button");
                input.type = "text";
                input.maxLength = 120;
                input.value = item.title || "";
                input.setAttribute("aria-label", t("sidebar.history.rename_placeholder"));
                save.type = "submit";
                save.textContent = t("sidebar.history.save");
                cancel.type = "button";
                cancel.textContent = t("sidebar.history.cancel");
                cancel.addEventListener("click", function () { closeHistoryMenu(true); });
                form.addEventListener("submit", function (event) {
                    event.preventDefault();
                    requestJson("/api/v1/assistant/conversations/" + encodeURIComponent(item.conversation_id), {
                        method: "PATCH",
                        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
                        body: JSON.stringify({ title: input.value })
                    }).then(function (result) {
                        if (!result.ok) { setStatus(result.body.detail || t("sidebar.history.action_failed"), true); return; }
                        updateConversationTitle(item.conversation_id, result.body.title);
                        closeHistoryMenu(false);
                        render();
                    }).catch(function () { setStatus(t("sidebar.history.action_failed"), true); });
                });
                actions.append(save, cancel);
                form.append(input, actions);
                menu.appendChild(form);
                input.focus();
            }

            function showDeleteConfirmation(menu, item) {
                menu.replaceChildren();
                var message = document.createElement("p");
                var confirm = document.createElement("button");
                var cancel = document.createElement("button");
                message.textContent = t("sidebar.history.delete_confirm");
                confirm.type = "button";
                confirm.className = "vp-history-item__delete-confirm";
                confirm.textContent = t("sidebar.history.delete_confirm_action");
                cancel.type = "button";
                cancel.textContent = t("sidebar.history.cancel");
                cancel.addEventListener("click", function () { closeHistoryMenu(true); });
                confirm.addEventListener("click", function () {
                    confirm.disabled = true;
                    deleteConversation(item, function () { closeHistoryMenu(false); }, function () { confirm.disabled = false; });
                });
                menu.append(message, confirm, cancel);
                confirm.focus();
            }

            function appendHistoryMenu(row, item) {
                var button = document.createElement("button");
                var menu = document.createElement("div");
                var rename = document.createElement("button");
                var remove = document.createElement("button");
                var icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
                button.type = "button";
                button.className = "vp-history-item__menu-button";
                button.setAttribute("aria-label", t("sidebar.history.options"));
                button.setAttribute("aria-haspopup", "menu");
                button.setAttribute("aria-expanded", "false");
                icon.setAttribute("width", "18");
                icon.setAttribute("height", "18");
                icon.setAttribute("viewBox", "0 0 18 18");
                icon.setAttribute("aria-hidden", "true");
                [4, 9, 14].forEach(function (cx) {
                    var dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
                    dot.setAttribute("cx", String(cx));
                    dot.setAttribute("cy", "9");
                    dot.setAttribute("r", "1.35");
                    dot.setAttribute("fill", "currentColor");
                    icon.appendChild(dot);
                });
                button.appendChild(icon);
                menu.className = "vp-history-item__menu";
                menu.setAttribute("role", "menu");
                menu.hidden = true;
                rename.type = "button";
                rename.textContent = t("sidebar.history.rename");
                rename.setAttribute("role", "menuitem");
                remove.type = "button";
                remove.className = "vp-history-item__delete";
                remove.textContent = t("sidebar.history.delete");
                remove.setAttribute("role", "menuitem");
                button.addEventListener("click", function (event) {
                    event.stopPropagation();
                    var wasOpen = activeHistoryMenu && activeHistoryMenu.menu === menu;
                    closeHistoryMenu(false);
                    if (wasOpen) { return; }
                    menu.hidden = false;
                    button.setAttribute("aria-expanded", "true");
                    activeHistoryMenu = { row: row, menu: menu, button: button };
                });
                rename.addEventListener("click", function () { showRenameForm(menu, item); });
                remove.addEventListener("click", function () { showDeleteConfirmation(menu, item); });
                menu.append(rename, remove);
                row.append(button, menu);
            }

            function appendModalDeleteButton(row, item) {
                var remove = document.createElement("button");
                var cancel = document.createElement("button");
                var confirming = false;
                remove.type = "button";
                remove.className = "vp-history-list__delete";
                remove.textContent = t("sidebar.history.delete");
                cancel.type = "button";
                cancel.className = "vp-history-list__cancel-delete";
                cancel.textContent = t("sidebar.history.cancel");
                cancel.hidden = true;
                cancel.addEventListener("click", function () {
                    confirming = false;
                    remove.textContent = t("sidebar.history.delete");
                    remove.classList.remove("is-confirming");
                    cancel.hidden = true;
                });
                remove.addEventListener("click", function () {
                    if (!confirming) {
                        confirming = true;
                        remove.textContent = t("sidebar.history.delete_confirm_action");
                        remove.classList.add("is-confirming");
                        cancel.hidden = false;
                        return;
                    }
                    remove.disabled = true;
                    deleteConversation(item, null, function () { remove.disabled = false; });
                });
                row.append(remove, cancel);
            }

            function rowFor(item, closeModal) {
                var row = document.createElement("li");
                var button = document.createElement("button");
                button.type = "button";
                button.textContent = closeModal ? historyTitleForModal(item.title) : item.title;
                button.title = item.title;
                button.addEventListener("click", function () {
                    if (closeModal) { modal.hidden = true; }
                    global.location.href = "/?conversation=" + encodeURIComponent(item.conversation_id);
                });
                row.appendChild(button);
                if (closeModal) {
                    row.className = "vp-history-list__item";
                    button.className = "vp-history-list__open";
                    appendModalDeleteButton(row, item);
                }
                if (!closeModal) {
                    row.className = "vp-history-item";
                    button.className = "vp-history-item__open";
                    button.textContent = historyTitleForSidebar(item.title);
                    appendHistoryMenu(row, item);
                }
                return row;
            }
            function render() {
                list.replaceChildren();
                modalConversations.forEach(function (item) { list.appendChild(rowFor(item, true)); });
                count.textContent = t("sidebar.history.count").replace("{count}", modalConversations.length);
                if (!list.children.length) {
                    var empty = document.createElement("li");
                    empty.className = "vp-list-empty";
                    empty.textContent = t("sidebar.history.empty");
                    list.appendChild(empty);
                }
                recentList.replaceChildren();
                conversations.slice(0, 4).forEach(function (item) { recentList.appendChild(rowFor(item, false)); });
                recent.hidden = conversations.length === 0;
            }
            function loadConversations(openModal) {
                var wallet = getWallet();
                if (!wallet) { conversations = []; modalConversations = []; render(); return; }
                requestJson("/api/v1/assistant/history?creator=" + encodeURIComponent(wallet)).then(function (result) {
                    conversations = result.ok ? (result.body.conversations || []) : [];
                    modalConversations = conversations;
                    render();
                    if (openModal) { modal.hidden = false; }
                }).catch(function () { conversations = []; modalConversations = []; render(); if (openModal) { modal.hidden = false; } });
            }
            function searchConversations() {
                var button = searchForm.querySelector("button[type='submit']");
                button.disabled = true;
                requestJson("/api/v1/assistant/conversations/search?q=" + encodeURIComponent(search.value.trim())).then(function (result) {
                    if (!result.ok) {
                        setStatus(result.body.detail || t("sidebar.history.action_failed"), true);
                        return;
                    }
                    modalConversations = result.body.conversations || [];
                    render();
                }).catch(function () { setStatus(t("sidebar.history.action_failed"), true); }).finally(function () {
                    button.disabled = false;
                });
            }
            function close() { modal.hidden = true; open.focus(); }
            open.addEventListener("click", function () {
                var wallet = getWallet();
                if (!wallet) { setStatus("Connect a creator wallet in account settings first.", true); return; }
                search.value = "";
                loadConversations(true);
            });
            searchForm.addEventListener("submit", function (event) {
                event.preventDefault();
                searchConversations();
            });
            modal.querySelectorAll("[data-history-close]").forEach(function (element) { element.addEventListener("click", close); });
            document.addEventListener("pointerdown", function (event) {
                if (activeHistoryMenu && !activeHistoryMenu.row.contains(event.target)) { closeHistoryMenu(false); }
            });
            global.addEventListener("keydown", function (event) {
                if (event.key === "Escape" && activeHistoryMenu) { closeHistoryMenu(true); return; }
                if (event.key === "Escape" && !modal.hidden) { close(); }
            });
            global.addEventListener("vp:history-changed", function () { loadConversations(false); });
            global.addEventListener("vp:wallet-changed", function () { loadConversations(false); });
            loadConversations(false);
        }

        function bindWallet() {
            var form = byId("wallet-form");
            if (!form) { return; }
            form.addEventListener("submit", function (event) {
                event.preventDefault();
                var wallet = walletInput.value.trim();
                if (!wallet) { setStatus("Enter a creator wallet first.", true); return; }
                saveSettings({ creator_wallet: wallet }).then(function (result) {
                    if (!result.ok) { setStatus(result.body.detail || "Wallet could not be saved.", true); return; }
                    setCreatorWallet(result.body.creator_wallet);
                    setStatus("Creator wallet saved.");
                    refreshAccountData();
                }).catch(function () { setStatus("Network error while saving wallet.", true); });
            });
        }

        function bindAccountSettings() {
            var modal = byId("account-modal");
            var open = byId("account-menu-button");
            var form = byId("account-settings-form");
            if (!modal || !open || !form) { return; }
            function selectTab(tabName) {
                modal.querySelectorAll("[data-settings-tab]").forEach(function (tab) {
                    tab.classList.toggle("is-active", tab.dataset.settingsTab === tabName);
                });
                modal.querySelectorAll("[data-settings-panel]").forEach(function (panel) {
                    panel.hidden = panel.dataset.settingsPanel !== tabName;
                });
                if (tabName === "sales") { global.dispatchEvent(new CustomEvent("vp:sales-tab-open")); }
                if (tabName === "wallet") { loadWalletMonitor(); }
            }

            function loadWalletMonitor() {
                var list = byId("settings-wallet-list");
                if (!list) { return; }
                list.replaceChildren();
                requestJson("/accounts/wallets/").then(function (result) {
                    if (!result.ok) { list.textContent = t("shell.status.settings_failed"); return; }
                    (result.body.items || []).forEach(function (wallet) {
                        var row = document.createElement("article");
                        var details = document.createElement("div");
                        var title = document.createElement("strong");
                        var address = document.createElement("code");
                        var roles = document.createElement("small");
                        var activate = document.createElement("button");
                        row.className = "vp-wallet-row" + (wallet.is_active ? " is-active" : "");
                        title.textContent = wallet.label;
                        address.textContent = wallet.address;
                        roles.textContent = [wallet.accepts_deposits ? t("account.wallet_deposits") : "", wallet.receives_payouts ? t("account.wallet_payouts") : ""].filter(Boolean).join(" · ");
                        activate.type = "button";
                        activate.textContent = wallet.is_active ? t("account.wallet_active") : t("account.wallet_use");
                        activate.disabled = wallet.is_active;
                        activate.addEventListener("click", function () {
                            requestJson("/accounts/wallets/" + wallet.id + "/activate/", { method: "POST", headers: { "X-CSRFToken": csrfToken() } }).then(function (activateResult) {
                                if (!activateResult.ok) { return; }
                                setCreatorWallet(activateResult.body.creator_wallet);
                                loadWalletMonitor();
                            });
                        });
                        details.append(title, address, roles); row.append(details, activate); list.appendChild(row);
                    });
                    if (!list.children.length) { list.textContent = t("account.wallet_empty"); }
                }).catch(function () { list.textContent = t("shell.status.settings_network"); });
            }

            modal.querySelectorAll("[data-settings-tab]").forEach(function (tab) {
                tab.addEventListener("click", function () { selectTab(tab.dataset.settingsTab); });
            });
            function close() { modal.hidden = true; open.focus(); }
            open.addEventListener("click", function () { modal.hidden = false; selectTab("account"); byId("account-display-name").focus(); });
            modal.querySelectorAll("[data-account-close]").forEach(function (button) { button.addEventListener("click", close); });
            global.addEventListener("keydown", function (event) { if (event.key === "Escape" && !modal.hidden) { close(); } });
            form.addEventListener("submit", function (event) {
                event.preventDefault();
                var status = byId("account-settings-status");
                saveSettings({
                    display_name: byId("account-display-name").value.trim(),
                    language: byId("account-language").value,
                    recovery_email: byId("account-recovery-email").value.trim(),
                    contact_phone: byId("account-contact-phone").value.trim(),
                    creator_wallet: getWallet()
                }).then(function (result) {
                    if (!result.ok) { status.textContent = result.body.detail || t("shell.status.settings_failed"); status.classList.add("is-error"); return; }
                    setCreatorWallet(result.body.creator_wallet);
                    status.textContent = t("shell.status.settings_saved");
                    status.classList.remove("is-error");
                    global.setTimeout(function () { global.location.reload(); }, 350);
                }).catch(function () { status.textContent = t("shell.status.settings_network"); status.classList.add("is-error"); });
            });
            var walletForm = byId("account-wallet-form");
            if (walletForm) {
                walletForm.addEventListener("submit", function (event) {
                    event.preventDefault();
                    var status = byId("account-wallet-status");
                    requestJson("/accounts/wallets/save/", {
                        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
                        body: JSON.stringify({ label: byId("account-wallet-label").value.trim(), address: byId("account-creator-wallet").value.trim(), accepts_deposits: byId("account-wallet-deposits").checked, receives_payouts: byId("account-wallet-payouts").checked })
                    }).then(function (result) {
                        if (!result.ok) { status.textContent = result.body.detail || t("shell.status.settings_failed"); status.classList.add("is-error"); return; }
                        walletForm.reset();
                        status.textContent = t("shell.status.settings_saved");
                        status.classList.remove("is-error");
                        loadWalletMonitor();
                    }).catch(function () { status.textContent = t("shell.status.settings_network"); status.classList.add("is-error"); });
                });
            }
            var passwordForm = byId("account-password-form");
            if (passwordForm) {
                passwordForm.addEventListener("submit", function (event) {
                    event.preventDefault();
                    var status = byId("account-password-status");
                    requestJson("/accounts/password/", {
                        method: "POST",
                        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
                        body: JSON.stringify({ current_password: byId("account-current-password").value, new_password: byId("account-new-password").value, confirmation: byId("account-confirm-password").value })
                    }).then(function (result) {
                        if (!result.ok) { status.textContent = result.body.detail || t("shell.status.settings_failed"); status.classList.add("is-error"); return; }
                        passwordForm.reset();
                        status.textContent = t("shell.status.settings_saved");
                        status.classList.remove("is-error");
                    }).catch(function () { status.textContent = t("shell.status.settings_network"); status.classList.add("is-error"); });
                });
            }
            // Instant language switch: flip the UI immediately (cookie + re-apply +
            // vp:language-change) and persist atomically — saveSettings sends all
            // three modal fields so display_name / creator_wallet are not clobbered.
            // No reload: the visual already reflects the new language.
            var langSelect = byId("account-language");
            if (langSelect) {
                langSelect.addEventListener("change", function () {
                    var status = byId("account-settings-status");
                    var previous = global.VP && global.VP.i18n ? global.VP.i18n.getLocale() : null;
                    if (global.VP && global.VP.i18n) { global.VP.i18n.setLocale(langSelect.value); }
                    saveSettings({ language: langSelect.value }).then(function (result) {
                        if (result.ok) {
                            status.textContent = t("shell.status.settings_saved");
                            status.classList.remove("is-error");
                            return;
                        }
                        if (previous && global.VP && global.VP.i18n) {
                            global.VP.i18n.setLocale(previous);
                            langSelect.value = previous;
                        }
                        status.textContent = result.body.detail || t("shell.status.settings_failed");
                        status.classList.add("is-error");
                    }).catch(function () {
                        if (previous && global.VP && global.VP.i18n) {
                            global.VP.i18n.setLocale(previous);
                            langSelect.value = previous;
                        }
                        status.textContent = t("shell.status.settings_network");
                        status.classList.add("is-error");
                    });
                });
            }
        }

        // 계정 설정은 세션 CSRF 보호를 거쳐 저장하고, 지갑 변경은 한 경로로만 반영한다.
        function saveSettings(partial) {
            var modalName = byId("account-display-name");
            var modalLanguage = byId("account-language");
            var modalWallet = byId("account-creator-wallet");
            var recoveryEmail = byId("account-recovery-email");
            var contactPhone = byId("account-contact-phone");
            return requestJson("/accounts/preferences/", {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
                body: JSON.stringify({
                    display_name: partial.display_name != null ? partial.display_name : (modalName ? modalName.value.trim() : ""),
                    language: partial.language || (modalLanguage ? modalLanguage.value : "ko"),
                    recovery_email: partial.recovery_email != null ? partial.recovery_email : (recoveryEmail ? recoveryEmail.value.trim() : ""),
                    contact_phone: partial.contact_phone != null ? partial.contact_phone : (contactPhone ? contactPhone.value.trim() : ""),
                    creator_wallet: partial.creator_wallet != null ? partial.creator_wallet : (modalWallet ? modalWallet.value.trim() : "")
                })
            });
        }

        function setCreatorWallet(wallet) {
            shell.dataset.creatorWallet = wallet || "";
            if (walletInput) { walletInput.value = wallet || ""; }
            var modalWallet = byId("account-creator-wallet");
            if (modalWallet) { modalWallet.value = wallet || ""; }
        }

        function refreshAccountData() {
            loadOverview();
            loadDirectives();
            loadActions();
            loadSales();
            global.dispatchEvent(new CustomEvent("vp:wallet-changed", { detail: { wallet: getWallet() } }));
        }

        function bindExpense() {
            var form = byId("expense-form");
            if (!form) { return; }
            form.addEventListener("submit", function (event) {
                event.preventDefault();
                var wallet = getWallet();
                var memo = byId("expense-memo").value.trim();
                var amount = byId("expense-amount").value;
                if (!wallet || !memo || !amount) { setStatus("Connect a wallet and enter an expense purpose and amount.", true); return; }
                requestJson("/api/v1/assistant/expenses", {
                    method: "POST",
                    headers: { "Content-Type": "application/json", Accept: "application/json" },
                    body: JSON.stringify({ creator_wallet: wallet, memo: memo, amount_usdc: amount })
                }).then(function (result) {
                    if (!result.ok) { setStatus(result.body.detail || result.body.error || "Expense could not be recorded.", true); return; }
                    byId("expense-memo").value = "";
                    byId("expense-amount").value = "";
                    setStatus("Expense recorded.");
                    loadOverview();
                    loadSales();
                }).catch(function () { setStatus("Network error while recording the expense.", true); });
            });
        }

        function bindDirectives() {
            var form = byId("directive-form");
            if (!form) { return; }
            form.addEventListener("submit", function (event) {
                event.preventDefault();
                var wallet = getWallet();
                var title = byId("directive-title").value.trim();
                var instruction = byId("directive-instruction").value.trim();
                if (!wallet || !title || !instruction) {
                    setStatus("Connect a wallet and enter an instruction title and detail.", true);
                    return;
                }
                requestJson("/api/v1/assistant/directives", {
                    method: "POST",
                    headers: { "Content-Type": "application/json", Accept: "application/json" },
                    body: JSON.stringify({ creator_wallet: wallet, title: title, instruction: instruction })
                }).then(function (result) {
                    if (!result.ok) { setStatus(result.body.detail || result.body.error || "Instruction could not be saved.", true); return; }
                    byId("directive-title").value = "";
                    byId("directive-instruction").value = "";
                    setStatus("Agent instruction saved.");
                    loadDirectives();
                    loadOverview();
                }).catch(function () { setStatus("Network error while saving the instruction.", true); });
            });
        }

        function bindSubscription() {
            var form = byId("subscription-form");
            var select = byId("subscription-plan");
            if (!form || !select) { return; }
            requestJson("/api/v1/subscriptions/plans").then(function (result) {
                if (!result.ok) { return; }
                select.replaceChildren();
                (result.body.items || []).forEach(function (plan) {
                    var option = document.createElement("option");
                    option.value = plan.code;
                    option.textContent = plan.name + " · " + plan.monthly_fee_usdc + " USDC / " + plan.included_registrations + " registrations";
                    select.appendChild(option);
                });
            });
            form.addEventListener("submit", function (event) {
                event.preventDefault();
                var wallet = getWallet();
                var payment = byId("subscription-payment").value.trim();
                if (!wallet || !select.value || !payment) { setStatus("Connect a wallet, choose a plan, and enter the payment ID.", true); return; }
                requestJson("/api/v1/subscriptions/activate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ creator_wallet: wallet, plan_code: select.value, payment_tx_sig: payment }) }).then(function (result) {
                    if (!result.ok) { setStatus(result.body.detail || result.body.error || "Subscription could not be activated.", true); return; }
                    byId("subscription-payment").value = "";
                    setStatus("Registration subscription activated.");
                    loadOverview();
                });
            });
        }

        function loadOverview() {
            var overview = byId("assistant-overview");
            var loading = byId("assistant-loading");
            var wallet = getWallet();
            if (!overview) { return; }
            if (!wallet) { overview.replaceChildren(); return; }
            if (loading) { loading.hidden = false; }
            requestJson("/api/v1/assistant/overview?creator=" + encodeURIComponent(wallet)).then(function (result) {
                if (loading) { loading.hidden = true; }
                if (!result.ok) { overview.replaceChildren(); return; }
                renderPairs(overview, [
                    ["Works", result.body.asset_count],
                    ["Shared", result.body.public_asset_count],
                    ["Income", formatUsd(result.body.license_revenue_usdc || 0)],
                    ["Expenses", formatUsd(result.body.expense_usdc || 0)],
                    ["Net", formatUsd(result.body.net_usdc || 0)]
                ]);
            }).catch(function () { if (loading) { loading.hidden = true; } });
        }

        function loadDirectives() {
            var list = byId("directive-list");
            var wallet = getWallet();
            if (!list) { return; }
            if (!wallet) { list.replaceChildren(); return; }
            requestJson("/api/v1/assistant/directives?creator=" + encodeURIComponent(wallet)).then(function (result) {
                if (!result.ok) { list.replaceChildren(); return; }
                list.replaceChildren();
                result.body.items.forEach(function (directive) {
                    var item = document.createElement("li");
                    var title = document.createElement("strong");
                    var text = document.createElement("p");
                    title.textContent = directive.title;
                    text.textContent = directive.instruction;
                    item.append(title, text);
                    list.appendChild(item);
                });
            });
        }

        function loadActions() {
            var list = byId("action-list");
            var wallet = getWallet();
            if (!list) { return; }
            if (!wallet) { list.replaceChildren(); return; }
            requestJson("/api/v1/assistant/actions?creator=" + encodeURIComponent(wallet)).then(function (result) {
                if (!result.ok) { return; }
                list.replaceChildren();
                (result.body.items || []).slice(0, 8).forEach(function (action) { list.appendChild(actionListItem(action)); });
                if (!list.children.length) { list.appendChild(emptyItem("No actions yet.")); }
            });
        }

        function loadSales() {
            var summary = byId("sales-summary");
            var list = byId("sales-list");
            var wallet = getWallet();
            if (!summary) { return; }
            if (!wallet) { summary.replaceChildren(); if (list) { list.replaceChildren(); } return; }
            requestJson("/api/v1/assistant/sales?creator=" + encodeURIComponent(wallet)).then(function (result) {
                if (!result.ok || !result.body || !result.body.summary) { summary.replaceChildren(); if (list) { list.replaceChildren(); } return; }
                var s = result.body.summary;
                renderPairs(summary, [
                    ["Sales", s.sale_count],
                    ["Gross", formatUsd(s.gross_usdc || 0)],
                    ["Fee", formatUsd(s.platform_fee_usdc || 0)],
                    ["Proceeds", formatUsd(s.creator_proceeds_usdc || 0)]
                ]);
                if (list) {
                    list.replaceChildren();
                    (result.body.items || []).slice(0, 6).forEach(function (sale) {
                        var item = document.createElement("li");
                        var title = document.createElement("strong");
                        var detail = document.createElement("p");
                        title.textContent = sale.asset_title || sale.asset_id || "sale";
                        detail.textContent = formatUsd(sale.price_usdc || 0) + (sale.usage_type ? " · " + sale.usage_type : "");
                        item.append(title, detail);
                        list.appendChild(item);
                    });
                    if (!list.children.length) { list.appendChild(emptyItem("No sales yet.")); }
                }
            });
        }

        // Prepend a chat-driven action to the list (called by workspace.js).
        function appendAction(action) {
            var list = byId("action-list");
            if (!list || !action) { return; }
            var placeholder = list.querySelector(".vp-list-empty");
            if (placeholder) { placeholder.remove(); }
            list.insertBefore(actionListItem(action), list.firstChild);
        }

        function refreshSummary() { loadOverview(); loadActions(); loadSales(); }

        function actionListItem(action) {
            var item = document.createElement("li");
            var title = document.createElement("strong");
            var detail = document.createElement("p");
            title.textContent = action.action_name || "action";
            detail.textContent = (action.status || "") + (action.verification_passed ? " · verified" : " · not verified");
            item.append(title, detail);
            return item;
        }

        function emptyItem(text) {
            var li = document.createElement("li");
            li.className = "vp-list-empty";
            li.textContent = text;
            return li;
        }

        function renderPairs(target, pairs) {
            target.replaceChildren();
            pairs.forEach(function (pair) {
                var dt = document.createElement("dt");
                var dd = document.createElement("dd");
                dt.textContent = pair[0];
                dd.textContent = pair[1];
                target.append(dt, dd);
            });
        }
    }

    if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", init); } else { init(); }
}(typeof window !== "undefined" ? window : this));
