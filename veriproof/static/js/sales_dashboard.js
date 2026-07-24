/*! Seller sales dashboard for the account settings modal. */
(function (global) {
    "use strict";

    function byId(id) { return document.getElementById(id); }
    function t(key) {
        return (global.VP && global.VP.i18n && global.VP.i18n.t ? global.VP.i18n.t(key) : key);
    }
    function money(value) {
        var amount = Number(value);
        return (isFinite(amount) ? new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(amount) : value) + " USDC";
    }
    function dateTime(value) {
        var parsed = new Date(value);
        return isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(parsed);
    }
    function shortWallet(value) {
        return value && value.length > 14 ? value.slice(0, 7) + "…" + value.slice(-5) : value;
    }

    /**
     * 단일 메트릭 카드(라벨 + 숫자) DOM 요소를 생성해 반환한다.
     */
    function createMetric(label, value) {
        var item = document.createElement("div");
        var title = document.createElement("span");
        var number = document.createElement("strong");
        title.textContent = label;
        number.textContent = value;
        item.append(title, number);
        return item;
    }

    /**
     * 판매 대시보드 컨트롤러 생성자. 상태(페이지/abort 컨트롤러)와 주요 DOM 참조를 초기화한다.
     */
    function SalesDashboard() {
        this.abortController = null;
        this.page = 1;
        this.workPage = 1;
        this.form = byId("settings-sales-filters");
        this.status = byId("settings-sales-status");
        this.recent = byId("settings-sales-recent");
        this.walletSelect = byId("settings-sales-wallet-select");
    }

    /**
     * 탭이 열릴 때 호출된다. 페이지를 1로 초기화하고 지갑 옵션을 로드한 뒤 본문 데이터를 불러온다.
     */
    SalesDashboard.prototype.open = function () {
        if (!this.form) { return; }
        this.page = 1;
        this.workPage = 1;
        this.loadWalletOptions().then(function () { this.load(); }.bind(this));
    };

    /**
     * 필터 폼 제출·초기화 버튼·지갑 선택 변경 이벤트를 바인딩한다. 변경 시 페이지를 1로 리셋하고 다시 불러온다.
     */
    SalesDashboard.prototype.bind = function () {
        var self = this;
        if (!this.form) { return; }
        this.form.addEventListener("submit", function (event) { event.preventDefault(); self.page = 1; self.workPage = 1; self.load(); });
        byId("settings-sales-reset").addEventListener("click", function () { self.form.reset(); self.page = 1; self.workPage = 1; self.load(); });
        this.walletSelect.addEventListener("change", function () { self.page = 1; self.workPage = 1; self.load(); });
    };

    /**
     * 현재 필터와 선택 지갑·페이지를 쿼리 파라미터(URLSearchParams)로 조립해 반환한다.
     */
    SalesDashboard.prototype.params = function () {
        var formData = new FormData(this.form);
        var params = new URLSearchParams();
        var wallet = this.selectedWallet();
        if (wallet) { params.set("creator", wallet); }
        formData.forEach(function (value, key) { if (value) { params.set(key, value); } });
        params.set("page", this.page);
        params.set("page_size", "20");
        params.set("work_page", this.workPage);
        params.set("work_page_size", "10");
        return params;
    };

    /**
     * /api/v1/assistant/sales 에서 필터링된 판매 데이터를 불러와 렌더링한다.
     * 진행 중인 이전 요청은 AbortController로 취소하고, 로딩 스켈레톤을 표시한 뒤 결과를 render로 넘긴다.
     */
    SalesDashboard.prototype.load = function () {
        var self = this;
        var wallet = this.selectedWallet();
        this.clearDetail();
        if (!wallet) { this.status.textContent = t("dashboard.wallet_required"); return; }
        if (this.abortController) { this.abortController.abort(); }
        this.abortController = new AbortController();
        this.status.textContent = "";
        this.renderLoading();
        fetch("/api/v1/assistant/sales?" + this.params().toString(), { signal: this.abortController.signal })
            .then(function (response) { return response.json().then(function (body) { return { ok: response.ok, body: body }; }); })
            .then(function (result) {
                if (!result.ok) {
                    self.clearResults();
                    self.status.textContent = t("shell.status.settings_failed");
                    return;
                }
                self.render(result.body, wallet);
            })
            .catch(function (error) {
                if (error.name === "AbortError") { return; }
                self.clearResults();
                self.status.textContent = t("shell.status.settings_network");
            });
    };

    /**
     * 메트릭과 표 영역에 스켈레톤 로딩 애니메이션을 표시한다.
     */
    SalesDashboard.prototype.renderLoading = function () {
        var metrics = byId("settings-sales-metrics");
        var list = byId("settings-sales-list");
        metrics.replaceChildren();
        for (var i = 0; i < 4; i += 1) { var block = document.createElement("div"); block.className = "vp-sales-skeleton"; metrics.appendChild(block); }
        list.replaceChildren();
        this.recent.hidden = false;
        for (var j = 0; j < 4; j += 1) { var row = document.createElement("tr"); var cell = document.createElement("td"); var line = document.createElement("span"); cell.colSpan = 6; line.className = "vp-sales-skeleton vp-sales-skeleton--row"; cell.appendChild(line); row.appendChild(cell); list.appendChild(row); }
    };

    /**
     * 불러온 데이터로 메트릭·필터 옵션·작품별 통계·판매 표를 모두 갱신한다.
     */
    SalesDashboard.prototype.render = function (data, wallet) {
        this.renderMetrics(data.summary || {});
        this.renderFilterOptions(data.filters || {});
        this.renderByWork(data.dashboard || {}, data.summary || {});
        this.renderTable(data.items || [], data.pagination || {}, data.summary || {});
    };

    /**
     * 현재 선택된 지갑 주소를 반환한다. 드롭다운 값이 비어 있으면 워크스페이스 기본 지갑으로 폴백한다.
     */
    SalesDashboard.prototype.selectedWallet = function () {
        return (this.walletSelect.value || (global.VP && global.VP.getWallet ? global.VP.getWallet() : "")).trim();
    };

    /**
     * /accounts/wallets/ 에서 지갑 목록을 불러와 드롭다운 옵션을 채운다. 실패 시 빈 목록으로 폴백한다.
     */
    SalesDashboard.prototype.loadWalletOptions = function () {
        var self = this;
        var workspaceWallet = global.VP && global.VP.getWallet ? global.VP.getWallet() : "";
        return fetch("/accounts/wallets/")
            .then(function (response) { return response.ok ? response.json() : { items: [] }; })
            .then(function (data) { self.renderWalletOptions(data.items || [], workspaceWallet); })
            .catch(function () { self.renderWalletOptions([], workspaceWallet); });
    };

    /**
     * 결제 수령/활성 지갑만 드롭다운에 표시한다. 워크스페이스 기본 지갑이 목록에 없으면 맨 앞에 추가한다.
     */
    SalesDashboard.prototype.renderWalletOptions = function (wallets, workspaceWallet) {
        var selected = this.walletSelect.value || workspaceWallet;
        var available = wallets.filter(function (wallet) {
            return wallet.receives_payouts || wallet.is_active || wallet.address === workspaceWallet;
        });
        if (workspaceWallet && !available.some(function (wallet) { return wallet.address === workspaceWallet; })) {
            available.unshift({ address: workspaceWallet, label: t("dashboard.current_workspace_wallet"), is_active: true });
        }
        this.walletSelect.replaceChildren();
        available.forEach(function (wallet) {
            var option = document.createElement("option");
            option.value = wallet.address;
            option.textContent = wallet.label + " · " + shortWallet(wallet.address);
            option.selected = wallet.address === selected;
            this.walletSelect.appendChild(option);
        }, this);
    };

    /**
     * 총판매액/정산액/건수/수수료 메트릭 카드를 렌더링한다.
     */
    SalesDashboard.prototype.renderMetrics = function (summary) {
        var metrics = byId("settings-sales-metrics");
        metrics.replaceChildren();
        [["dashboard.gross", money(summary.gross_usdc || "0")], ["dashboard.proceeds", money(summary.creator_proceeds_usdc || "0")], ["dashboard.sales", summary.sale_count || 0], ["dashboard.fee", money(summary.platform_fee_usdc || "0")]].forEach(function (entry) { metrics.appendChild(createMetric(t(entry[0]), entry[1])); });
    };

    /**
     * 응답에 포함된 자산/이용유형 필터 옵션으로 <select>를 다시 채운다.
     */
    SalesDashboard.prototype.renderFilterOptions = function (filters) {
        var asset = byId("settings-sales-asset");
        var usage = byId("settings-sales-usage");
        this.replaceOptions(asset, filters.assets || [], "asset_id", "asset_title", t("dashboard.all_works"));
        this.replaceOptions(usage, (filters.usage_types || []).map(function (value) { return { value: value, label: value }; }), "value", "label", t("dashboard.all_usage"));
    };

    /**
     * <select>의 옵션을 주어진 항목으로 교체한다. 맨 앞에 "전체"용 빈 옵션을 추가하고 기존 선택값을 보존한다.
     */
    SalesDashboard.prototype.replaceOptions = function (select, items, valueKey, labelKey, emptyLabel) {
        var selected = select.value;
        select.replaceChildren();
        var empty = document.createElement("option"); empty.value = ""; empty.textContent = emptyLabel; select.appendChild(empty);
        items.forEach(function (item) { var option = document.createElement("option"); option.value = item[valueKey]; option.textContent = item[labelKey] || item[valueKey]; option.selected = option.value === selected; select.appendChild(option); });
    };

    /**
     * 작품별 집계 표(건수/총액/평균/최근판매)를 렌더링하고 페이지네이션과 건수 라벨을 갱신한다.
     */
    SalesDashboard.prototype.renderByWork = function (dashboard, summary) {
        var section = byId("settings-sales-by-work");
        var list = byId("settings-sales-work-list");
        var count = byId("settings-sales-work-count");
        var pagination = dashboard.by_work_pagination || {};
        list.replaceChildren();
        (dashboard.by_work || []).forEach(function (item) {
            var row = document.createElement("tr");
            [
                item.asset_title || item.asset_id,
                item.sale_count,
                money(item.gross_usdc || "0"),
                money(item.average_usdc || "0"),
                dateTime(item.last_sold_at),
            ].forEach(function (value, index) {
                var cell = document.createElement("td");
                cell.textContent = value;
                if (index > 0) { cell.className = "vp-sales-table__amount"; }
                row.appendChild(cell);
            });
            list.appendChild(row);
        });
        count.textContent = this.workTotalLabel(summary, pagination.total_count || 0);
        this.renderWorkPagination(pagination);
        section.hidden = !list.children.length;
    };

    /**
     * 판매 내역 표를 렌더링한다. 비어 있으면 빈 상태 행을 표시하고 페이지네이션을 갱신한다.
     */
    SalesDashboard.prototype.renderTable = function (sales, pagination, summary) {
        var self = this;
        var list = byId("settings-sales-list");
        var count = byId("settings-sales-count");
        list.replaceChildren(); this.recent.hidden = false;
        count.textContent = this.totalLabel(summary);
        sales.forEach(function (sale) { list.appendChild(self.saleRow(sale)); });
        if (!sales.length) { var row = document.createElement("tr"); var cell = document.createElement("td"); cell.colSpan = 6; cell.className = "vp-sales-table__empty"; cell.textContent = t("dashboard.no_sales"); row.appendChild(cell); list.appendChild(row); }
        this.renderPagination(pagination);
    };

    /**
     * 단일 판매 행(<tr>)을 생성한다. 상세 보기 토글 버튼을 포함한다.
     */
    SalesDashboard.prototype.saleRow = function (sale) {
        var self = this;
        var row = document.createElement("tr");
        [[sale.asset_title || sale.asset_id, ""], [dateTime(sale.granted_at), ""], [shortWallet(sale.buyer_wallet), "vp-sales-table__wallet"], [sale.usage_type, ""], [money(sale.price_usdc), "vp-sales-table__amount"]].forEach(function (entry) { var cell = document.createElement("td"); cell.textContent = entry[0]; if (entry[1]) { cell.className = entry[1]; } row.appendChild(cell); });
        var action = document.createElement("td"); var button = document.createElement("button"); button.type = "button"; button.className = "vp-sales-table__detail"; button.setAttribute("aria-expanded", "false"); button.textContent = t("dashboard.view_details"); button.addEventListener("click", function () { self.toggleDetail(row, sale, button); }); action.appendChild(button); row.appendChild(action);
        return row;
    };

    /**
     * 판매 내역 표의 페이지네이션 컨트롤(이전/다음 + 현재 페이지 표시)을 렌더링한다.
     */
    SalesDashboard.prototype.renderPagination = function (pagination) {
        var self = this;
        var target = byId("settings-sales-pagination"); target.replaceChildren();
        if ((pagination.page_count || 1) < 2) { return; }
        [["‹", pagination.page - 1, pagination.page <= 1], ["›", pagination.page + 1, pagination.page >= pagination.page_count]].forEach(function (item) { var button = document.createElement("button"); button.type = "button"; button.textContent = item[0]; button.disabled = item[2]; button.addEventListener("click", function () { self.page = item[1]; self.load(); }); target.appendChild(button); });
        var label = document.createElement("span"); label.textContent = pagination.page + " / " + pagination.page_count; target.appendChild(label);
    };

    SalesDashboard.prototype.totalLabel = function (summary) {
        return t("dashboard.filtered_total")
            .replace("{count}", summary.sale_count || 0)
            .replace("{gross}", money(summary.gross_usdc || "0"));
    };

    SalesDashboard.prototype.workTotalLabel = function (summary, workCount) {
        return t("dashboard.filtered_work_total")
            .replace("{works}", workCount)
            .replace("{count}", summary.sale_count || 0)
            .replace("{gross}", money(summary.gross_usdc || "0"));
    };

    /**
     * 작품별 표의 페이지네이션 컨트롤(이전/다음 + 현재 페이지 표시)을 렌더링한다.
     */
    SalesDashboard.prototype.renderWorkPagination = function (pagination) {
        var self = this;
        var target = byId("settings-sales-work-pagination");
        target.replaceChildren();
        if ((pagination.page_count || 1) < 2) { return; }
        [["‹", pagination.page - 1, pagination.page <= 1], ["›", pagination.page + 1, pagination.page >= pagination.page_count]].forEach(function (item) {
            var button = document.createElement("button");
            button.type = "button";
            button.textContent = item[0];
            button.disabled = item[2];
            button.addEventListener("click", function () { self.workPage = item[1]; self.load(); });
            target.appendChild(button);
        });
        var label = document.createElement("span");
        label.textContent = pagination.page + " / " + pagination.page_count;
        target.appendChild(label);
    };

    /**
     * 판매 행 아래에 인라인 상세 패널(구매자/라이선스/결제/인증서 ID)을 토글한다.
     * 이미 열린 상세가 있으면 제거하고, 없으면 기존 상세를 모두 닫은 뒤 새로 삽입한다.
     */
    SalesDashboard.prototype.toggleDetail = function (row, sale, button) {
        var existing = row.nextElementSibling;
        if (existing && existing.classList.contains("vp-sales-table__detail-row")) {
            existing.remove();
            button.setAttribute("aria-expanded", "false");
            return;
        }
        this.clearDetail();
        var detailRow = document.createElement("tr");
        detailRow.className = "vp-sales-table__detail-row";
        var cell = document.createElement("td");
        cell.colSpan = 6;
        var panel = document.createElement("section");
        panel.className = "vp-sales-inline-detail";
        var heading = document.createElement("h5");
        heading.textContent = t("dashboard.detail");
        var list = document.createElement("dl");
        [["dashboard.table.buyer", sale.buyer_wallet, ""], ["dashboard.license_id", sale.license_id, ""], ["dashboard.payment_id", sale.payment_tx_sig, "is-wide"], ["dashboard.certificate_id", sale.certificate_tx_sig || "—", "is-wide"]].forEach(function (entry) { var item = document.createElement("div"); var term = document.createElement("dt"); var value = document.createElement("dd"); item.className = "vp-sales-inline-detail__item " + entry[2]; term.textContent = t(entry[0]); value.textContent = entry[1]; item.append(term, value); list.appendChild(item); });
        panel.append(heading, list);
        cell.appendChild(panel);
        detailRow.appendChild(cell);
        row.after(detailRow);
        button.setAttribute("aria-expanded", "true");
        detailRow.scrollIntoView({ block: "nearest", behavior: "smooth" });
    };

    /**
     * 열려 있는 모든 인라인 상세 행을 제거하고 해당 버튼의 aria-expanded를 false로 되돌린다.
     */
    SalesDashboard.prototype.clearDetail = function () {
        document.querySelectorAll(".vp-sales-table__detail-row").forEach(function (row) { row.remove(); });
        document.querySelectorAll(".vp-sales-table__detail[aria-expanded='true']").forEach(function (button) { button.setAttribute("aria-expanded", "false"); });
    };
    SalesDashboard.prototype.clearResults = function () { byId("settings-sales-metrics").replaceChildren(); byId("settings-sales-list").replaceChildren(); this.recent.hidden = true; };

    document.addEventListener("DOMContentLoaded", function () {
        var dashboard = new SalesDashboard(); dashboard.bind();
        global.addEventListener("vp:sales-tab-open", function () { dashboard.open(); });
    });
}(window));
