/* 대화, 첨부, 등록 초안을 분리하는 창작자 워크스페이스 컨트롤러. */
(function () {
    "use strict";

    var VP = window.VP || {};
    var DEFAULT_REGISTRATION_PRICE = "0.001";
    function byId(id) { return document.getElementById(id); }
    function wallet() { return (VP.getWallet ? VP.getWallet() : "") || ""; }
    function request(url, options) {
        if (VP.requestJson) { return VP.requestJson(url, options); }
        return fetch(url, options).then(function (r) { return r.json().catch(function () { return {}; }).then(function (b) { return { ok: r.ok, body: b }; }); });
    }
    // i18n helper — VP.i18n loads in <head> (messages.js + vp-i18n.js) and survives
    // dashboard.js's merge into window.VP, so it is ready before this init runs.
    function t(key, vars) { return (VP.i18n && VP.i18n.t) ? VP.i18n.t(key, vars) : key; }

    function init() {
        var shell = document.querySelector("[data-chat-shell]");
        var messages = byId("assistant-messages");
        var form = byId("assistant-form");
        var input = byId("assistant-message");
        var canvas = byId("registration-canvas");
        if (!shell || !messages || !form || !canvas) { return; }
        var state = {
            file: null,
            files: [],
            activeFileIndex: 0,
            fileProfiles: [],
            attachments: [],
            registrationWalletAddress: "",
            conversationId: new URLSearchParams(window.location.search).get("conversation") || null
        };

        bindChat();
        bindAttachmentMenu();
        bindComposerDrop();
        bindCanvas();
        window.addEventListener("vp:wallet-changed", loadHistory);
        window.addEventListener("vp:new-chat", resetConversation);
        window.addEventListener("vp:conversation-deleted", function (event) {
            if (event.detail && event.detail.conversationId === state.conversationId) { resetConversation(); }
        });
        if (wallet()) { loadHistory(); }

        /**
         * 채팅 폼 제출과 입력창 자동 확장을 바인딩한다. Enter(Shift 없음)로 제출하고
         * 입력 줄 수에 따라 textarea를 최대 4줄까지 높이를 키운다.
         */
        function bindChat() {
            var isComposing = false;
            var submitAfterComposition = false;
            form.addEventListener("submit", function (event) {
                event.preventDefault();
                var text = input.value.trim();
                if (!wallet()) { return setStatus(t("workspace.status.wallet_first"), true); }
                if (!text) { return; }
                sendConversation(text);
                input.value = "";
                input.style.height = "auto";
            });
            // Grow up to 4 lines (~106px), then keep height fixed and let the textarea scroll.
            input.addEventListener("input", function () { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 106) + "px"; });
            input.addEventListener("compositionstart", function () { isComposing = true; });
            input.addEventListener("compositionend", function () {
                isComposing = false;
                if (!submitAfterComposition) { return; }
                submitAfterComposition = false;
                window.setTimeout(function () { form.requestSubmit(); }, 0);
            });
            input.addEventListener("keydown", function (event) {
                if (event.key !== "Enter" || event.shiftKey) { return; }
                if (isComposing || event.isComposing || event.keyCode === 229) {
                    submitAfterComposition = true;
                    return;
                }
                event.preventDefault();
                form.requestSubmit();
            });
        }

        /**
         * 첨부(+) 버튼의 드롭다운 메뉴를 바인딩한다. 파일 첨부와 등록 패널 열기를 연결하고
         * 외부 클릭·Esc로 메뉴 닫힘을 처리한다.
         */
        function bindAttachmentMenu() {
            var add = byId("composer-add");
            var menu = byId("composer-add-menu");
            var fileInput = byId("conversation-file");
            // 첨부 메뉴는 명시적인 + 버튼 조작에서만 열리도록 초기 상태를 강제한다.
            function closeMenu() { menu.hidden = true; add.setAttribute("aria-expanded", "false"); }
            closeMenu();
            add.addEventListener("click", function (event) { event.stopPropagation(); menu.hidden = !menu.hidden; add.setAttribute("aria-expanded", String(!menu.hidden)); });
            menu.addEventListener("click", function (event) { event.stopPropagation(); });
            byId("attach-file").addEventListener("click", function () { closeMenu(); fileInput.click(); });
            byId("start-registration").addEventListener("click", function () { closeMenu(); openCanvas(); });
            // 메뉴 안을 누른 pointerdown이 먼저 메뉴를 숨기면 이어지는 click이
            // 취소될 수 있다. 바깥 클릭일 때만 닫아 버튼 동작을 보장한다.
            document.addEventListener("pointerdown", function (event) {
                if (!menu.hidden && !menu.contains(event.target) && event.target !== add) { closeMenu(); }
            });
            document.addEventListener("keydown", function (event) { if (event.key === "Escape" && !menu.hidden) { closeMenu(); add.focus(); } });
            fileInput.addEventListener("change", function () {
                var file = fileInput.files[0];
                if (file) { uploadConversationFile(file); }
                fileInput.value = "";
            });
        }

        function bindComposerDrop() {
            // Open the drop veil as soon as a file is dragged anywhere NEAR the
            // composer — i.e. anywhere over the chat panel — not only once it
            // reaches the input itself. The veil still lives on the composer, so
            // its look is unchanged; only the trigger/drop area is widened to the
            // whole panel. A depth counter keeps the veil stable while the pointer
            // moves between the panel's child elements.
            var composer = form;
            var zone = form.closest(".vp-chat") || form;
            var depth = 0;
            function isFileDrag(event) {
                var dt = event.dataTransfer;
                return !!dt && Array.prototype.indexOf.call(dt.types || [], "Files") !== -1;
            }
            function clear() { depth = 0; composer.classList.remove("is-composer-dragover"); }
            zone.addEventListener("dragenter", function (event) {
                if (!isFileDrag(event)) { return; }
                event.preventDefault();
                depth += 1;
                composer.classList.add("is-composer-dragover");
            });
            zone.addEventListener("dragover", function (event) {
                if (!isFileDrag(event)) { return; }
                event.preventDefault();
                if (event.dataTransfer) { event.dataTransfer.dropEffect = "copy"; }
            });
            zone.addEventListener("dragleave", function (event) {
                if (!isFileDrag(event)) { return; }
                depth -= 1;
                if (depth <= 0) { clear(); }
            });
            zone.addEventListener("drop", function (event) {
                if (!isFileDrag(event)) { return; }
                event.preventDefault();
                clear();
                var files = Array.prototype.slice.call(event.dataTransfer.files || []);
                if (!files.length) { return; }
                if (!wallet()) { return setStatus(t("workspace.status.wallet_first"), true); }
                files.forEach(function (file) { uploadConversationFile(file); });
                input.focus();
            });
            // Stop the browser from navigating to a file dropped outside a drop zone.
            ["dragover", "drop"].forEach(function (type) {
                window.addEventListener(type, function (event) { if (isFileDrag(event)) { event.preventDefault(); } });
            });
        }

        /**
         * 등록 캔버스의 드롭존·파일 입력·입력 필드·버튼을 바인딩한다.
         * 단위 변경 시 패키지 모드 활성화, 필드 입력 시 초안 캡처, 등록 상담/확정 버튼을 연결한다.
         */
        function bindCanvas() {
            var dropzone = byId("dropzone");
            var fileInput = byId("file-input");
            byId("close-registration").addEventListener("click", closeCanvas);
            dropzone.addEventListener("click", function () { fileInput.click(); });
            dropzone.addEventListener("keydown", function (event) { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); fileInput.click(); } });
            ["dragenter", "dragover"].forEach(function (type) { dropzone.addEventListener(type, function (event) { event.preventDefault(); dropzone.classList.add("is-dragover"); }); });
            ["dragleave", "drop"].forEach(function (type) { dropzone.addEventListener(type, function (event) { event.preventDefault(); dropzone.classList.remove("is-dragover"); }); });
            dropzone.addEventListener("drop", function (event) { chooseRegistrationFiles(event.dataTransfer.files); });
            fileInput.addEventListener("change", function () { chooseRegistrationFiles(fileInput.files); fileInput.value = ""; });
            byId("registration-unit").addEventListener("change", function () {
                if (state.files.length) { activateRegistrationFile(byId("registration-unit").value === "package" ? 0 : state.activeFileIndex); }
            });
            ["asset-type", "asset-title", "asset-description", "asset-tags", "min-price", "target-price", "asset-share"].forEach(function (id) {
                byId(id).addEventListener("input", captureActiveFields);
                byId(id).addEventListener("change", captureActiveFields);
            });
            byId("ask-registration-advice").addEventListener("click", requestRegistrationFeedback);
            byId("confirm-registration").addEventListener("click", confirmAndRegister);
        }

        function activeRegistrationWallet() {
            return request("/accounts/wallets/").then(function (result) {
                if (!result.ok) { throw new Error("wallet_status_unavailable"); }
                return (result.body.items || []).find(function (item) {
                    return item.is_active && item.address && item.has_private_address;
                }) || null;
            });
        }
        function requireRegistrationWallet(continueRegistration) {
            activeRegistrationWallet().then(function (activeWallet) {
                if (!activeWallet) { return setStatus(t("workspace.status.wallet_keys_register"), true); }
                state.registrationWalletAddress = activeWallet.address;
                continueRegistration(activeWallet.address);
            }).catch(function () {
                setStatus(t("workspace.status.wallet_check_failed"), true);
            });
        }
        function openCanvas() {
            canvas.hidden = false;
            document.body.classList.add("is-registration-canvas-open");
            canvas.querySelector("#file-input").focus();
            activeRegistrationWallet().then(function (activeWallet) {
                if (activeWallet) { state.registrationWalletAddress = activeWallet.address; }
            }).catch(function () {});
        }
        function closeCanvas() { canvas.hidden = true; document.body.classList.remove("is-registration-canvas-open"); }
        function resetRegisteredCanvas() {
            state.file = null;
            state.files = [];
            state.activeFileIndex = 0;
            state.fileProfiles = [];
            state.registrationWalletAddress = "";
            byId("registration-file-queue").replaceChildren();
            var dropzone = byId("dropzone");
            dropzone.classList.remove("has-file");
            dropzone.querySelector(".dropzone__hint").textContent = t("workspace.canvas.drop_hint");
        }
        /**
         * 선택/드롭된 파일 목록을 상태에 저장하고 첫 파일을 활성 파일로 세팅한 뒤 큐를 렌더링한다.
         */
        function chooseRegistrationFiles(files) {
            var selected = Array.from(files || []);
            if (!selected.length) { return; }
            state.files = selected;
            state.fileProfiles = selected.map(function () { return { fields: null, draft: null }; });
            state.activeFileIndex = 0;
            restoreActiveFields(selected[0]);
            renderFileQueue();
            chooseRegistrationFile(selected[0]);
        }
        function activeFileProfile() { return state.fileProfiles[state.activeFileIndex] || null; }
        /**
         * 현재 활성 파일 프로필에 현재 입력 필드 값을 스냅샷으로 저장한다(파일 전환 시 손실 방지).
         */
        function captureActiveFields() {
            var profile = activeFileProfile();
            if (profile) { profile.fields = fields(); }
        }
        /**
         * 활성 파일의 저장된 입력값을 폼에 복원한다. 저장값이 없으면 파일명에서 제목을 유추해 채운다.
         */
        function restoreActiveFields(file) {
            var saved = (activeFileProfile() || {}).fields || {};
            byId("asset-type").value = saved.asset_type || "image";
            byId("asset-title").value = saved.title || file.name.replace(/\.[^.]+$/, "");
            byId("asset-description").value = saved.description || "";
            byId("asset-tags").value = saved.tags || "";
            byId("min-price").value = saved.min_price || DEFAULT_REGISTRATION_PRICE;
            byId("target-price").value = saved.target_price || DEFAULT_REGISTRATION_PRICE;
            byId("asset-share").checked = saved.visibility === "public";
        }
        /**
         * 지정 인덱스의 파일을 활성 파일로 전환한다. 현재 입력값을 캡처하고, 패키지 모드면 항상 0번으로 강제한다.
         */
        function activateRegistrationFile(index) {
            captureActiveFields();
            if (byId("registration-unit").value === "package") { index = 0; }
            state.activeFileIndex = index;
            var file = state.files[index];
            restoreActiveFields(file);
            renderFileQueue();
            chooseRegistrationFile(file);
        }
        /**
         * 단일 파일을 선택해 드롭존에 표시하고 서버 초안(saveDraft)을 갱신한다.
         */
        function chooseRegistrationFile(file) {
            if (!file) { return; }
            state.file = file;
            var dropzone = byId("dropzone");
            dropzone.classList.add("has-file");
            dropzone.querySelector(".dropzone__hint").textContent = registrationFiles().length > 1 ? t("workspace.canvas.images_selected", { n: registrationFiles().length }) : file.name;
            if (state.registrationWalletAddress) {
                saveDraft(state.registrationWalletAddress).catch(function (error) {
                    renderDraftError(error.message || t("workspace.status.draft_save_failed"));
                });
            }
        }
        /**
         * 선택된 파일 목록을 큐 UI로 렌더링하고 활성 파일을 표시한다.
         */
        function renderFileQueue() {
            var queue = byId("registration-file-queue");
            queue.replaceChildren();
            state.files.forEach(function (file, index) {
                var item = document.createElement("li");
                var button = document.createElement("button");
                button.type = "button";
                button.textContent = file.name;
                button.className = index === state.activeFileIndex ? "is-active" : "";
                button.addEventListener("click", function () {
                    activateRegistrationFile(index);
                });
                item.appendChild(button); queue.appendChild(item);
            });
        }
        /**
         * 현재 등록 폼의 모든 입력값을 객체로 수집해 반환한다(자산 유형/제목/설명/태그/가격/공개여부).
         */
        function fields() {
            // Registration-canvas prices are submitted as USDC amounts.
            return { asset_type: byId("asset-type").value, title: byId("asset-title").value.trim(), description: byId("asset-description").value.trim(), tags: byId("asset-tags").value.trim(), min_price: byId("min-price").value, target_price: byId("target-price").value, visibility: byId("asset-share").checked ? "public" : "private" };
        }
        function registrationFiles() {
            if (byId("registration-unit").value === "package" && byId("asset-type").value === "image") {
                return state.files;
            }
            return state.file ? [state.file] : [];
        }
        function saveDraft(creatorWallet) {
            if (!creatorWallet) { return Promise.reject(new Error(t("workspace.status.wallet_keys_register"))); }
            var profile = activeFileProfile();
            if (!profile) { return Promise.reject(new Error(t("workspace.status.choose_first"))); }
            var data = new FormData();
            data.append("creator_wallet", creatorWallet);
            data.append("draft_id", profile.draft && profile.draft.draft_id || "");
            data.append("file_name", state.file.name);
            data.append("fields", JSON.stringify(fields()));
            registrationFiles().forEach(function (file) { data.append("files", file); });
            return request("/api/v1/assistant/registration-drafts", { method: "POST", body: data }).then(function (result) {
                if (!result.ok) {
                    throw new Error(result.body.detail || t("workspace.status.draft_save_failed"));
                }
                profile.draft = result.body;
                renderDraftMessage(t("workspace.status.draft_saved"));
            });
        }
        /**
         * 등록 확정 흐름을 실행한다. 파일 포함 초안 저장 → 확정 토큰 발급 →
         * 파일 업로드(uploadConfirmed) 순으로 진행하며 각 단계 실패를 에러로 표시한다.
         */
        function confirmAndRegister() {
            if (!state.file) { return renderDraftError(t("workspace.status.choose_first")); }
            requireRegistrationWallet(function (creatorWallet) {
                captureActiveFields();
                var profile = activeFileProfile();
                saveDraft(creatorWallet).then(function () {
                    if (!profile.draft) { throw new Error(t("workspace.status.draft_save_failed")); }
                    return request("/api/v1/assistant/registration-drafts/" + encodeURIComponent(profile.draft.draft_id) + "/confirm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ creator_wallet: creatorWallet }) }).then(function (confirmed) {
                        if (!confirmed || !confirmed.ok) { return renderDraftError((confirmed && confirmed.body.detail) || t("workspace.status.confirm_incomplete")); }
                        uploadConfirmed(confirmed.body.confirmation_token, profile.draft.draft_id, creatorWallet);
                    });
                }).catch(function (error) {
                    renderDraftError(error.message || t("workspace.status.draft_save_failed"));
                });
            });
        }
        /**
         * 확정 토큰과 함께 파일을 등록 엔드포인트로 업로드한다. 패키지 모드면 나머지 파일을 지원 파일로 첨부한다.
         */
        function uploadConfirmed(token, draftId, creatorWallet) {
            var data = new FormData();
            data.append("image", state.file);
            data.append("creator_wallet", creatorWallet);
            data.append("asset_type", fields().asset_type);
            data.append("min_price", fields().min_price);
            data.append("target_price", fields().target_price);
            data.append("draft_id", draftId);
            data.append("confirmation_token", token);
            if (byId("registration-unit").value === "package") {
                state.files.filter(function (file) { return file !== state.file; }).forEach(function (file) {
                    data.append(fields().asset_type === "image" ? "gallery_images" : "supporting_files", file);
                });
            }
            renderDraftMessage(t("workspace.status.confirming"));
            request(shell.dataset.registerUrl, { method: "POST", body: data }).then(function (result) {
                if (!result.ok) { return renderDraftError(result.body.detail || result.body.error || t("workspace.status.register_failed")); }
                var completionMessage = t(result.body.anchor_tx ? "workspace.status.registered_available" : "workspace.status.registered_pending");
                renderDraftMessage(completionMessage);
                if (VP.refreshSummary) { VP.refreshSummary(); }
                resetRegisteredCanvas();
                closeCanvas();
                setStatus(completionMessage);
            });
        }
        /**
         * 현재 등록 초안 내용을 채팅 메시지로 꾸려 AI 등록 상담을 요청한다(sendConversation에 위임).
         */
        function requestRegistrationFeedback() {
            if (!state.file) { return renderDraftError(t("workspace.status.choose_first")); }
            captureActiveFields();
            var registrationFields = fields();
            sendConversation(t("workspace.advice.message", {
                file_name: state.file.name,
                asset_type: byId("asset-type").selectedOptions[0].textContent,
                title: registrationFields.title || "—",
                description: registrationFields.description || "—",
                tags: registrationFields.tags || "—",
                min_price: registrationFields.min_price,
                target_price: registrationFields.target_price,
                visibility: registrationFields.visibility === "public" ? "yes" : "no"
            }));
        }
        /**
         * 사용자 메시지를 /api/v1/assistant/chat 로 전송한다. 타이핑 표시 후 응답을 화면에 추가하고,
         * 대화 ID를 갱신하며 첨부를 초기화한다. 실패 시 네트워크 에러를 상태로 표시한다.
         */
        function sendConversation(text) {
            var sentAttachments = state.attachments.slice();
            clearEmpty(); appendMessage("user", text, sentAttachments);
            state.attachments = []; renderComposerAttachments();
            var typing = appendTyping(); setStatus(t("workspace.status.thinking"));
            request("/api/v1/assistant/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ creator_wallet: wallet(), message: text, attachment_ids: sentAttachments.map(function (item) { return item.attachment_id; }), conversation_id: state.conversationId }) }).then(function (result) {
                typing.remove(); if (!result.ok) { return setStatus(result.body.detail || t("workspace.status.respond_failed"), true); }
                state.conversationId = result.body.conversation_id || state.conversationId;
                appendMessage("assistant", result.body.answer);
                applyRegistrationMetadata(result.body.registration_metadata, sentAttachments);
                setStatus("");
                window.dispatchEvent(new CustomEvent("vp:history-changed"));
            }).catch(function () { typing.remove(); setStatus(t("workspace.status.network"), true); });
        }
        /**
         * /api/v1/assistant/history 에서 현재 지갑(및 선택된 대화)의 메시지 이력을 불러와 화면에 다시 그린다.
         */
        function loadHistory() {
            if (!wallet()) { return; }
            var url = "/api/v1/assistant/history?creator=" + encodeURIComponent(wallet());
            if (state.conversationId) { url += "&conversation=" + encodeURIComponent(state.conversationId); }
            request(url).then(function (result) { if (!result.ok) { return; } messages.replaceChildren(); if (!result.body.items.length) { appendEmpty(); } else { result.body.items.forEach(function (item) { appendMessage(item.role, item.content); }); } });
        }
        /**
         * 현재 대화를 초기화한다. 메시지/첨부를 비우고 URL에서 conversation 파라미터를 제거한 뒤 빈 상태를 표시한다.
         */
        function resetConversation() {
            messages.replaceChildren(); state.attachments = []; renderComposerAttachments(); state.conversationId = null;
            var url = new URL(window.location.href); url.searchParams.delete("conversation"); window.history.replaceState({}, "", url);
            appendEmpty(); setStatus(""); input.focus();
        }
        /**
         * 대화 첨부 파일을 /api/v1/assistant/attachments 로 업로드한다. 이미지는 로컬 blob 미리보기를 덧붙인다.
         */
        function uploadConversationFile(file) {
            if (!wallet()) { return setStatus(t("workspace.status.wallet_first"), true); }
            var data = new FormData();
            data.append("creator_wallet", wallet()); data.append("file", file);
            setStatus(t("workspace.status.attachment_uploading"));
            request("/api/v1/assistant/attachments", { method: "POST", body: data }).then(function (result) {
                if (!result.ok) { return setStatus(result.body.detail || t("workspace.status.attachment_failed"), true); }
                var item = result.body;
                item.file = file;
                // Images get a local blob thumbnail — the API returns no preview URL.
                if (file.type && file.type.indexOf("image/") === 0) { item.preview_url = URL.createObjectURL(file); }
                state.attachments.push(item);
                renderComposerAttachments(); setStatus(t("workspace.status.attachment_ready"));
            }).catch(function () { setStatus(t("workspace.status.attachment_failed"), true); });
        }
        function applyRegistrationMetadata(metadata, sentAttachments) {
            if (!metadata) { return; }
            var attachment = sentAttachments.find(function (item) { return item.attachment_id === metadata.attachment_id; });
            registrationFile(attachment, metadata).then(function (file) {
                openCanvas();
                chooseRegistrationFiles([file]);
                byId("asset-title").value = metadata.title;
                byId("asset-description").value = metadata.description;
                byId("asset-tags").value = (metadata.tags || []).slice(0, 6).join(", ");
                captureActiveFields();
            }).catch(function () { setStatus(t("workspace.status.attachment_failed"), true); });
        }
        function registrationFile(attachment, metadata) {
            if (attachment && attachment.file) { return Promise.resolve(attachment.file); }
            var url = "/api/v1/assistant/attachments/" + encodeURIComponent(metadata.attachment_id)
                + "/file?creator_wallet=" + encodeURIComponent(wallet());
            return fetch(url).then(function (response) {
                if (!response.ok) { throw new Error("attachment_unavailable"); }
                return response.blob();
            }).then(function (blob) {
                return new File([blob], metadata.file_name, { type: metadata.content_mime_type });
            });
        }
        // Compact attachment chips: images show a thumbnail, everything else shows a
        // type icon derived from its MIME type / extension. Static SVGs (no user data).
        var ATTACHMENT_ICONS = {
            audio: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>',
            video: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="M10 9l5 3-5 3z"/></svg>',
            doc: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8M8 17h8M8 9h2"/></svg>',
            archive: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 8v12H3V8"/><rect x="1" y="3" width="22" height="5"/><path d="M10 12h4"/></svg>',
            image: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.6"/><path d="M21 15l-5-5L5 21"/></svg>',
            file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>'
        };
        /**
         * MIME 타입과 확장자로 첨부의 종류(image/audio/video/archive/doc/file)를 판별한다.
         */
        function attachmentKind(mime, fileName) {
            mime = String(mime || "").toLowerCase();
            var ext = (String(fileName || "").split(".").pop() || "").toLowerCase();
            if (mime.indexOf("image/") === 0 || ["png", "jpg", "jpeg", "webp", "gif", "svg", "bmp"].indexOf(ext) !== -1) { return "image"; }
            if (mime.indexOf("audio/") === 0 || ["mp3", "wav", "ogg", "m4a", "flac", "aac"].indexOf(ext) !== -1) { return "audio"; }
            if (mime.indexOf("video/") === 0 || ["mp4", "webm", "mov", "avi", "mkv"].indexOf(ext) !== -1) { return "video"; }
            if (mime.indexOf("zip") !== -1 || mime.indexOf("tar") !== -1 || mime.indexOf("compressed") !== -1 || ["zip", "tar", "gz", "rar", "7z"].indexOf(ext) !== -1) { return "archive"; }
            if (mime === "application/pdf" || mime.indexOf("text/") === 0 || mime.indexOf("word") !== -1 || mime.indexOf("document") !== -1 || ["pdf", "txt", "md", "doc", "docx", "rtf", "odt", "ppt", "pptx", "xls", "xlsx", "csv"].indexOf(ext) !== -1) { return "doc"; }
            return "file";
        }
        /**
         * 파일명에서 확장자(최대 4자, 대문자) 라벨을 추출한다. 확장자가 없으면 빈 문자열.
         */
        function attachmentExtLabel(fileName) {
            var s = String(fileName || "");
            var dot = s.lastIndexOf(".");
            if (dot <= 0 || dot === s.length - 1) { return ""; }
            return s.slice(dot + 1, dot + 5).toUpperCase();
        }
        function renderComposerAttachments() {
            var tray = byId("composer-attachments");
            tray.replaceChildren(); tray.hidden = state.attachments.length === 0;
            state.attachments.forEach(function (attachment) {
                var kind = attachmentKind(attachment.content_mime_type, attachment.file_name);
                var chip = document.createElement("div"); chip.className = "vp-composer-attachment"; chip.title = attachment.file_name;
                var thumb = document.createElement("span"); thumb.className = "vp-composer-attachment__thumb vp-composer-attachment__thumb--" + kind;
                if (kind === "image" && attachment.preview_url) {
                    var img = document.createElement("img"); img.src = attachment.preview_url; img.alt = ""; img.loading = "lazy";
                    thumb.appendChild(img);
                } else {
                    thumb.innerHTML = ATTACHMENT_ICONS[kind] || ATTACHMENT_ICONS.file;
                    var ext = attachmentExtLabel(attachment.file_name);
                    if (ext) { var extEl = document.createElement("span"); extEl.className = "vp-composer-attachment__ext"; extEl.textContent = ext; thumb.appendChild(extEl); }
                }
                var remove = document.createElement("button"); remove.type = "button"; remove.className = "vp-composer-attachment__remove"; remove.textContent = "×"; remove.setAttribute("aria-label", t("workspace.composer.remove_attachment"));
                remove.addEventListener("click", function () {
                    if (attachment.preview_url) { URL.revokeObjectURL(attachment.preview_url); }
                    state.attachments = state.attachments.filter(function (item) { return item.attachment_id !== attachment.attachment_id; });
                    renderComposerAttachments();
                });
                chip.append(thumb, remove); tray.appendChild(chip);
            });
        }
        function appendMessage(role, text, attachments) {
            var item = document.createElement("article");
            item.className = "vp-message vp-message--" + role;
            var body = document.createElement("div"); body.className = "vp-message__body"; body.textContent = text;
            item.appendChild(body);
            (attachments || []).filter(function (attachment) {
                return attachmentKind(attachment.content_mime_type, attachment.file_name) === "image" && attachment.preview_url;
            }).forEach(function (attachment) {
                var image = document.createElement("img");
                image.className = "vp-message__image";
                image.src = attachment.preview_url;
                image.alt = attachment.file_name || "";
                image.loading = "lazy";
                item.appendChild(image);
            });
            messages.appendChild(item); messages.scrollTop = messages.scrollHeight;
        }
        function appendTyping() { var item = document.createElement("div"); item.className = "vp-typing"; item.innerHTML = "<span></span><span></span><span></span>"; messages.appendChild(item); return item; }
        // Empty state built from the dictionary; data-i18n attrs let applyTranslations
        // re-translate it on a language change (e.g. after "New chat" resets the view).
        function appendEmpty() {
            var item = document.createElement("div");
            item.id = "chat-empty-state";
            item.className = "vp-empty-state";
            var mark = document.createElement("p"); mark.className = "vp-empty-state__mark"; mark.textContent = "✦";
            var h1 = document.createElement("h1"); h1.setAttribute("data-i18n", "workspace.empty.title"); h1.textContent = t("workspace.empty.title");
            var p = document.createElement("p"); p.setAttribute("data-i18n", "workspace.empty.body"); p.textContent = t("workspace.empty.body");
            item.append(mark, h1, p);
            messages.appendChild(item);
        }
        function clearEmpty() { var empty = byId("chat-empty-state"); if (empty) { empty.remove(); } }
        function renderDraftMessage(text) { byId("analysis-feed").replaceChildren(Object.assign(document.createElement("li"), { className: "analysis-card", textContent: text })); }
        function renderDraftError(text) { var item = document.createElement("li"); item.className = "analysis-card vp-registration-error"; item.textContent = text; byId("analysis-feed").replaceChildren(item); }
        function setStatus(text, error) { var target = byId("assistant-status"); target.textContent = text || ""; target.classList.toggle("is-error", Boolean(error)); }
    }
    if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", init); } else { init(); }
}());
