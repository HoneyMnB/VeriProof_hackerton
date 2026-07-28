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

    /**
     * 현재 활성 창작자 지갑 주소를 [data-chat-shell] 요소의 data-creator-wallet에서 읽어 반환한다.
     */
    function getWallet() {
        var shell = document.querySelector("[data-chat-shell]");
        return ((shell && shell.dataset.creatorWallet) || "").trim();
    }

    /**
     * 문서 쿠키에서 csrftoken 값을 읽어 반환한다. 보호되는 POST/PATCH/DELETE 요청의 X-CSRFToken 헤더용.
     */
    function csrfToken() {
        var match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : "";
    }

    /**
     * fetch 래퍼. 응답 본문을 JSON으로 파싱해 {ok, status, body} 형태로 반환한다.
     * 본문이 JSON이 아니면 빈 객체로 폴백한다.
     */
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

    var SOL_FORMAT = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 9 });
    function formatSol(value) {
        var n = Number(value);
        return (isFinite(n) ? SOL_FORMAT.format(n) : String(value == null ? "0" : value)) + " SOL";
    }

    function setStatus(text, isError) {
        // Workspace exposes #assistant-status; Library/Sandbox have no status
        // surface and rely on a page reload for feedback instead.
        var s = byId("assistant-status");
        if (!s) { return; }
        s.textContent = text || "";
        s.classList.toggle("is-error", Boolean(isError));
    }

    /**
     * 공용 사이드바를 초기화한다. 사이드바 토글·새 채팅·히스토리·계정 설정을 바인딩하고
     * 페이지 진입용 VP.* 훅(getWallet/requestJson/appendAction/refreshSummary)을 노출한다.
     */
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

        /**
         * 사이드바 접기/펼치기 토글을 바인딩한다. 상태를 localStorage에 저장하고
         * 접근성 라벨·aria-expanded를 i18n와 함께 동기화한다.
         */
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

        /**
         * "새 채팅" 버튼 바인딩. Workspace의 <button>에만 동작하며 클릭 시 vp:new-chat 이벤트를 발생시킨다.
         */
        function bindNewChat() {
            var btn = byId("new-chat");
            // Only a real <button> (Workspace) is interactive here; on other
            // pages the partial renders an <a> link to "/" so nothing to bind.
            if (!btn || btn.tagName !== "BUTTON") { return; }
            btn.addEventListener("click", function () {
                global.dispatchEvent(new CustomEvent("vp:new-chat"));
            });
        }

        /**
         * 대화 히스토리 모달과 사이드바 최근 목록을 바인딩한다. 목록 로드/검색, 이름 변경·삭제 메뉴,
         * 페이지 이동, 키보드·외부 클릭 닫기를 처리한다.
         */
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
            /**
             * 모달용 대화 제목을 자른다. 한글은 30자, 그 외는 50자까지 표시하고 초과 시 …을 붙인다.
             */
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

            /**
             * 열려 있는 행별 메뉴(이름 변경/삭제)를 닫는다. returnFocus가 참이면 메뉴 버튼으로 포커스를 되돌린다.
             */
            function closeHistoryMenu(returnFocus) {
                if (!activeHistoryMenu) { return; }
                activeHistoryMenu.menu.hidden = true;
                activeHistoryMenu.button.setAttribute("aria-expanded", "false");
                if (returnFocus) { activeHistoryMenu.button.focus(); }
                activeHistoryMenu = null;
            }

            /**
             * 사이드바·모두 보기 양쪽 목록에서 해당 대화 ID의 제목을 새 제목으로 갱신한다.
             */
            function updateConversationTitle(conversationId, title) {
                [conversations, modalConversations].forEach(function (items) {
                    items.forEach(function (conversation) {
                        if (conversation.conversation_id === conversationId) { conversation.title = title; }
                    });
                });
            }

            /**
             * 대화를 DELETE 한다. 성공 시 양쪽 목록에서 제거하고 vp:conversation-deleted 이벤트를 발생시킨다.
             */
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

            /**
             * 행별 메뉴를 인라인 이름 변경 폼으로 교체하고, 제출 시 PATCH로 제목을 갱신한다.
             */
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

            /**
             * 행별 메뉴를 삭제 확인(재클릭 시 삭제) UI로 교체한다.
             */
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

            /**
             * 사이드바 히스토리 행에 점3개 메뉴 버튼과(이름 변경/삭제) 메뉴를 추가한다. 한 번에 하나만 열리도록 관리한다.
             */
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

            /**
             * 모달 행에 삭제 버튼을 추가한다. 한 번 클릭하면 확인 상태로 바뀌고, 두 번 클릭하면 실제로 삭제한다.
             */
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

            /**
             * 단일 대화 행(<li>)을 생성한다. closeModal 여부에 따라 모달용(제목 자르기 + 삭제 버튼) 또는
             * 사이드바용(제목 자르기 + 점3개 메뉴) 렌더링으로 분기한다.
             */
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
            /**
             * 모두 보기 목록과 사이드바 최근(상위 4개) 목록을 현재 대화 데이터로 다시 그린다.
             */
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
            /**
             * /api/v1/assistant/history 에서 대화 목록을 불러온다. openModal이 참이면 모달을 연다.
             */
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
            /**
             * /api/v1/assistant/conversations/search 로 검색어를 보내 결과를 모달 목록에 반영한다.
             */
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

        /**
         * 지갑 입력 폼(#wallet-form) 제출을 바인딩한다. 입력값을 saveSettings로 저장하고
         * 성공 시 창작자 지갑을 갱신한 뒤 계정 데이터를 다시 불러온다.
         */
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

        /**
         * 계정 설정 모달을 바인딩한다. 탭 전환(계정/지갑/비밀번호/판매), 계정 정보 저장,
         * 지갑 목록·활성화, 비밀번호 변경, 즉시 언어 전환을 처리한다.
         */
        function bindAccountSettings() {
            var modal = byId("account-modal");
            var open = byId("account-menu-button");
            var form = byId("account-settings-form");
            if (!modal || !open || !form) { return; }
            /**
             * 계정 설정 모달의 탭을 전환한다. 판매 탭은 vp:sales-tab-open, 지갑 탭은 loadWalletMonitor를 트리거한다.
             */
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

            /**
             * /accounts/wallets/ 에서 지갑 목록을 불러와 설정 모달에 렌더링한다. 각 지갑의 활성화 버튼을 연결한다.
             */
            function loadWalletMonitor() {
                var list = byId("settings-wallet-list");
                if (!list) { return; }
                list.replaceChildren();
                requestJson("/accounts/wallets/").then(function (result) {
                    if (!result.ok) { list.textContent = t("shell.status.settings_failed"); return; }
                    var wallets = result.body.items || [];
                    wallets.forEach(function (wallet) {
                        var row = document.createElement("article");
                        var details = document.createElement("div");
                        var title = document.createElement("strong");
                        var address = document.createElement("code");
                        var actions = document.createElement("div");
                        var edit = document.createElement("button");
                        var remove = document.createElement("button");
                        var cancelRemove = document.createElement("button");
                        row.className = "vp-wallet-row" + (wallet.is_active ? " is-active" : "");
                        title.textContent = wallet.label;
                        address.textContent = wallet.address;
                        edit.type = "button";
                        edit.className = "vp-wallet-row__edit";
                        edit.textContent = t("account.wallet_edit");
                        edit.addEventListener("click", function () {
                            walletForm.hidden = false;
                            walletForm.classList.add("is-editing");
                            walletForm.dataset.editing = "true";
                            byId("account-wallet-label").value = wallet.label;
                            byId("account-creator-wallet").value = wallet.address;
                            privateAddress.value = "";
                            privateAddress.required = false;
                            privateHint.textContent = t("account.wallet_private_edit_hint");
                            setPrivateAddressState(wallet.has_private_address);
                            walletCancel.removeAttribute("hidden");
                            byId("account-wallet-label").focus();
                        });
                        remove.type = "button";
                        remove.className = "vp-wallet-row__delete";
                        remove.setAttribute("aria-label", t("account.wallet_delete"));
                        remove.setAttribute("title", t("account.wallet_delete"));
                        remove.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M4 7h16M9 7V4h6v3m-8 0 1 13h8l1-13M10 11v5m4-5v5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
                        remove.addEventListener("click", function () {
                            if (!actions.classList.contains("is-confirming")) {
                                actions.classList.add("is-confirming");
                                remove.setAttribute("aria-label", t("account.wallet_delete_confirm"));
                                remove.setAttribute("title", t("account.wallet_delete_confirm"));
                                cancelRemove.hidden = false;
                                return;
                            }
                            requestJson("/accounts/wallets/" + wallet.id + "/", { method: "DELETE", headers: { "X-CSRFToken": csrfToken() } }).then(function (deleteResult) {
                                if (!deleteResult.ok) {
                                    var walletStatus = byId("account-wallet-status");
                                    if (walletStatus) {
                                        walletStatus.textContent = deleteResult.body.detail || t("shell.status.settings_failed");
                                        walletStatus.classList.add("is-error");
                                    }
                                    return;
                                }
                                setCreatorWallet(deleteResult.body.creator_wallet);
                                loadWalletMonitor();
                            }).catch(function () {
                                var walletStatus = byId("account-wallet-status");
                                if (walletStatus) {
                                    walletStatus.textContent = t("shell.status.settings_network");
                                    walletStatus.classList.add("is-error");
                                }
                            });
                        });
                        cancelRemove.type = "button";
                        cancelRemove.className = "vp-wallet-row__cancel-delete";
                        cancelRemove.textContent = t("account.wallet_delete_cancel");
                        cancelRemove.hidden = true;
                        cancelRemove.addEventListener("click", function () {
                            actions.classList.remove("is-confirming");
                            remove.setAttribute("aria-label", t("account.wallet_delete"));
                            remove.setAttribute("title", t("account.wallet_delete"));
                            cancelRemove.hidden = true;
                        });
                        details.append(title, address); actions.append(edit, cancelRemove); row.append(details, actions, remove); list.appendChild(row);
                    });
                    walletForm.hidden = wallets.length > 0;
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
                var privateAddress = byId("account-wallet-private-address");
                var privateToggle = byId("account-wallet-private-toggle");
                var privateHint = byId("account-wallet-private-hint");
                var privateState = byId("account-wallet-private-state");
                var walletCancel = byId("account-wallet-cancel");
                function setPrivateAddressState(hasPrivateAddress) {
                    privateState.hidden = false;
                    privateState.className = "vp-wallet-private-state " + (hasPrivateAddress ? "is-registered" : "is-missing");
                    privateState.textContent = hasPrivateAddress ? "✓ " + t("account.wallet_private_registered") : "× " + t("account.wallet_private_missing");
                }
                function resetWalletForm() {
                    walletForm.reset();
                    walletForm.classList.remove("is-editing");
                    walletForm.dataset.editing = "";
                    privateAddress.required = true;
                    privateAddress.type = "password";
                    privateHint.textContent = t("account.wallet_private_hint");
                    privateState.hidden = true;
                    privateToggle.setAttribute("aria-pressed", "false");
                    privateToggle.textContent = t("account.wallet_private_show");
                    walletCancel.setAttribute("hidden", "");
                }
                privateToggle.addEventListener("click", function () {
                    var isVisible = privateAddress.type === "text";
                    privateAddress.type = isVisible ? "password" : "text";
                    privateToggle.setAttribute("aria-pressed", String(!isVisible));
                    privateToggle.textContent = t(isVisible ? "account.wallet_private_show" : "account.wallet_private_hide");
                });
                walletCancel.addEventListener("click", function () {
                    resetWalletForm();
                    walletForm.hidden = true;
                });
                walletForm.addEventListener("submit", function (event) {
                    event.preventDefault();
                    var status = byId("account-wallet-status");
                    requestJson("/accounts/wallets/save/", {
                        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
                        body: JSON.stringify({ label: byId("account-wallet-label").value.trim(), address: byId("account-creator-wallet").value.trim(), private_address: privateAddress.value.trim() })
                    }).then(function (result) {
                        if (!result.ok) { status.textContent = result.body.detail || t("shell.status.settings_failed"); status.classList.add("is-error"); return; }
                        resetWalletForm();
                        walletForm.hidden = true;
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

        /**
         * 창작자 지갑을 셸 data 속성과 지갑 입력/모달 필드에 동기식으로 반영한다(서버 저장은 별도).
         */
        function setCreatorWallet(wallet) {
            shell.dataset.creatorWallet = wallet || "";
            if (walletInput) { walletInput.value = wallet || ""; }
            var modalWallet = byId("account-creator-wallet");
            if (modalWallet) { modalWallet.value = wallet || ""; }
        }

        /**
         * 지갑 변경 후 개요/지시문/액션/판매를 모두 다시 불러오고 vp:wallet-changed 이벤트를 발생시킨다.
         */
        function refreshAccountData() {
            loadOverview();
            loadDirectives();
            loadActions();
            loadSales();
            global.dispatchEvent(new CustomEvent("vp:wallet-changed", { detail: { wallet: getWallet() } }));
        }

        /**
         * 비용 입력 폼을 바인딩한다. 메모·금액을 /api/v1/assistant/expenses 로 기록하고 개요·판매를 갱신한다.
         */
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

        /**
         * 에이전트 지시문 폼을 바인딩한다. 제목·내용을 /api/v1/assistant/directives 로 저장하고 목록을 갱신한다.
         */
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

        /**
         * 구독 플랜 목록을 로드한다. 활성화는 실제 결제 연동 전까지 서버가 거부한다.
         */
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
                if (!wallet || !select.value) { setStatus("Connect a wallet and choose a plan.", true); return; }
                requestJson("/api/v1/subscriptions/activate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ creator_wallet: wallet, plan_code: select.value }) }).then(function (result) {
                    if (!result.ok) { setStatus(result.body.detail || result.body.error || "Subscription could not be activated.", true); return; }
                    setStatus("Registration subscription activated.");
                    loadOverview();
                });
            });
        }

        /**
         * /api/v1/assistant/overview 에서 창작자 개요(작품/공유/수입/지출/순이익)를 불러와 사이드바에 렌더링한다.
         */
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

        /**
         * /api/v1/assistant/directives 에서 창작자 지시문 목록을 불러와 사이드바에 렌더링한다.
         */
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

        /**
         * /api/v1/assistant/actions 에서 검증 액션 목록을 불러와 최근 8개만 사이드바에 렌더링한다.
         */
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

        /**
         * /api/v1/assistant/sales 에서 판매 요약과 최근 내역을 불러와 사이드바에 렌더링한다.
         */
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
                    ["Gross", formatSol(s.gross_sol || 0)],
                    ["Fee", formatSol(s.platform_fee_sol || 0)],
                    ["Proceeds", formatSol(s.creator_proceeds_sol || 0)]
                ]);
                if (list) {
                    list.replaceChildren();
                    (result.body.items || []).slice(0, 6).forEach(function (sale) {
                        var item = document.createElement("li");
                        var title = document.createElement("strong");
                        var detail = document.createElement("p");
                        title.textContent = sale.asset_title || sale.asset_id || "sale";
                        detail.textContent = formatSol(sale.price_sol || 0) + (sale.usage_type ? " · " + sale.usage_type : "");
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

        /**
         * 단일 액션(이름 + 상태/검증 여부) <li> 요소를 생성해 반환한다.
         */
        function actionListItem(action) {
            var item = document.createElement("li");
            var title = document.createElement("strong");
            var detail = document.createElement("p");
            title.textContent = action.action_name || "action";
            detail.textContent = (action.status || "") + (action.verification_passed ? " · verified" : " · not verified");
            item.append(title, detail);
            return item;
        }

        /**
         * 빈 상태 안내 텍스트를 담은 <li> 요소를 생성해 반환한다.
         */
        function emptyItem(text) {
            var li = document.createElement("li");
            li.className = "vp-list-empty";
            li.textContent = text;
            return li;
        }

        /**
         * [라벨, 값] 쌍 배열을 <dl>의 <dt>/<dd> 쌍으로 target에 렌더링한다.
         */
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
