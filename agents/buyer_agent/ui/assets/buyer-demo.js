(() => {
  "use strict";

  const state = {
    sessionId: null,
    busy: false,
    pendingApproval: null,
    agentMessagesByCall: new Map(),
  };
  const elements = {
    form: document.querySelector("#chatForm"),
    input: document.querySelector("#chatInput"),
    send: document.querySelector("#sendButton"),
    intro: document.querySelector("#intro"),
    messages: document.querySelector("#messages"),
    connection: document.querySelector("#connectionStatus"),
    connectionLabel: document.querySelector("#connectionLabel"),
    modeInputs: document.querySelectorAll('[name="paymentMode"]'),
    paymentDialog: document.querySelector("#paymentDialog"),
    approvalMethod: document.querySelector("#approvalMethod"),
    approvalAsset: document.querySelector("#approvalAsset"),
    approvalWait: document.querySelector("#approvalWait"),
    approvePayment: document.querySelector("#approvePayment"),
    declinePayment: document.querySelector("#declinePayment"),
    turnTemplate: document.querySelector("#turnTemplate"),
    agentMessageTemplate: document.querySelector("#agentMessageTemplate"),
    deliveryTemplate: document.querySelector("#deliveryTemplate"),
    itemTemplate: document.querySelector("#executionItemTemplate"),
  };

  function setConnection(kind, text) {
    elements.connection.className = `connection ${kind}`;
    elements.connectionLabel.textContent = text;
  }

  function selectedPaymentMode() {
    return document.querySelector('[name="paymentMode"]:checked').value;
  }

  function setApprovalControlsEnabled(enabled) {
    elements.approvePayment.disabled = !enabled;
    elements.declinePayment.disabled = !enabled;
    elements.approvalWait.hidden = enabled;
  }

  function openPaymentDialog(event) {
    state.pendingApproval = {
      asset_id: event.asset_id,
      payment_method: event.payment_method,
    };
    elements.approvalMethod.textContent = event.payment_method === "USDC_X402"
      ? "USDC · x402"
      : "SOL · native";
    elements.approvalAsset.textContent = event.asset_id;
    setApprovalControlsEnabled(!state.busy);
    if (!elements.paymentDialog.open) elements.paymentDialog.showModal();
  }

  async function checkAgent() {
    try {
      const response = await fetch("/.well-known/agent-card.json", {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error("Agent unavailable");
      setConnection("connected", "Agent online");
    } catch (_) {
      setConnection("error", "Agent offline");
    }
  }

  function resizeInput() {
    elements.input.style.height = "auto";
    elements.input.style.height = `${Math.min(elements.input.scrollHeight, 150)}px`;
  }

  function createTurn(message) {
    const fragment = elements.turnTemplate.content.cloneNode(true);
    const turn = fragment.querySelector(".turn");
    renderMarkdown(turn.querySelector(".user-message"), message);
    elements.messages.append(fragment);
    return elements.messages.lastElementChild;
  }

  function executionTime() {
    return new Intl.DateTimeFormat("en", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }).format(new Date());
  }

  function detailText(value) {
    if (value === undefined || value === null) return "";
    if (typeof value === "string") return value;
    return JSON.stringify(value, null, 2);
  }

  function executionRoot(container) {
    if (container.classList.contains("turn")) {
      return container.querySelector(".assistant-content .execution");
    }
    return container.querySelector(".execution");
  }

  function addExecutionItem(container, { kind = "status", title, description, detail }) {
    const fragment = elements.itemTemplate.content.cloneNode(true);
    const item = fragment.querySelector(".execution-item");
    item.classList.add(kind);
    item.querySelector("strong").textContent = title;
    item.querySelector(".step-heading span").textContent = executionTime();
    const descriptionNode = item.querySelector(".step-description");
    descriptionNode.textContent = description;
    const pre = item.querySelector("pre");
    const serialized = detailText(detail);
    if (serialized) {
      item.classList.add("has-detail");
      pre.textContent = serialized;
      descriptionNode.addEventListener("click", () => {
        pre.hidden = !pre.hidden;
      });
    }
    executionRoot(container).querySelector(".execution-list").append(fragment);
  }

  function setExecutionStatus(container, text, kind = "") {
    const status = executionRoot(container).querySelector(".execution-status");
    status.textContent = text;
    status.className = `execution-status ${kind}`.trim();
  }

  function setExecutionLabel(container, text) {
    executionRoot(container).querySelector(".execution-label").textContent = text;
  }

  function resetExecution(container, label = "Excution") {
    const execution = executionRoot(container);
    execution.querySelector(".execution-list").replaceChildren();
    execution.querySelector(".execution-label").textContent = label;
    setExecutionStatus(container, "Working", "working");
  }

  function appendInlineMarkdown(parent, text) {
    const tokens = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\(https?:\/\/[^\s)]+\))/g;
    let cursor = 0;
    for (const match of text.matchAll(tokens)) {
      parent.append(document.createTextNode(text.slice(cursor, match.index)));
      const token = match[0];
      if (token.startsWith("`")) {
        const code = document.createElement("code");
        code.textContent = token.slice(1, -1);
        parent.append(code);
      } else if (token.startsWith("**")) {
        const strong = document.createElement("strong");
        strong.textContent = token.slice(2, -2);
        parent.append(strong);
      } else {
        const linkEnd = token.lastIndexOf("](");
        const link = document.createElement("a");
        link.href = token.slice(linkEnd + 2, -1);
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = token.slice(1, linkEnd);
        parent.append(link);
      }
      cursor = (match.index || 0) + token.length;
    }
    parent.append(document.createTextNode(text.slice(cursor)));
  }

  function renderMarkdown(target, markdown) {
    const fragment = document.createDocumentFragment();
    const lines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
    let paragraph = [];
    let list = null;
    let codeLines = null;

    const flushParagraph = () => {
      if (!paragraph.length) return;
      const node = document.createElement("p");
      node.className = "markdown-paragraph";
      appendInlineMarkdown(node, paragraph.join(" "));
      fragment.append(node);
      paragraph = [];
    };
    const flushList = () => {
      if (!list) return;
      fragment.append(list);
      list = null;
    };

    for (const line of lines) {
      if (line.startsWith("```")) {
        flushParagraph();
        flushList();
        if (codeLines === null) codeLines = [];
        else {
          const pre = document.createElement("pre");
          const code = document.createElement("code");
          code.textContent = codeLines.join("\n");
          pre.append(code);
          fragment.append(pre);
          codeLines = null;
        }
        continue;
      }
      if (codeLines !== null) {
        codeLines.push(line);
        continue;
      }
      const heading = line.match(/^(#{1,3})\s+(.+)$/);
      const listItem = line.match(/^\s*([-*]|\d+\.)\s+(.+)$/);
      if (heading) {
        flushParagraph();
        flushList();
        const node = document.createElement(`h${heading[1].length + 2}`);
        appendInlineMarkdown(node, heading[2]);
        fragment.append(node);
      } else if (listItem) {
        flushParagraph();
        const ordered = listItem[1].endsWith(".");
        if (!list || (list.tagName === "OL") !== ordered) {
          flushList();
          list = document.createElement(ordered ? "ol" : "ul");
        }
        const item = document.createElement("li");
        appendInlineMarkdown(item, listItem[2]);
        list.append(item);
      } else if (!line.trim()) {
        flushParagraph();
        flushList();
      } else {
        flushList();
        paragraph.push(line.trim());
      }
    }
    if (codeLines !== null) paragraph.push(codeLines.join("\n"));
    flushParagraph();
    flushList();
    target.replaceChildren(fragment);
  }

  function showAssistantMessage(turn, text, isError = false) {
    turn.querySelector(".assistant-row").hidden = false;
    const message = turn.querySelector(".assistant-message");
    message.classList.remove("is-loading");
    message.classList.toggle("error", isError);
    renderMarkdown(message, text);
  }

  function setAssistantLoading(turn, visible) {
    const assistantRow = turn.querySelector(".assistant-row");
    assistantRow.hidden = !visible;
    if (!visible) return;

    const message = turn.querySelector(".assistant-message");
    message.classList.remove("error");
    message.classList.add("is-loading");
    message.replaceChildren();
    const typing = document.createElement("span");
    typing.className = "typing";
    typing.setAttribute("aria-label", "Buyer Agent is preparing a response");
    for (let index = 0; index < 3; index += 1) typing.append(document.createElement("i"));
    message.append(typing);
  }

  function purchaseCompletionMarkdown(delivery) {
    return [
      "## 구매가 완료되었습니다",
      "",
      "**작품 ID**",
      `\`${delivery.asset_id}\``,
      "",
      "**트랜잭션**",
      `\`${delivery.transaction_signature}\``,
      "",
      `구매 증빙 및 [원본 다운로드](${delivery.download_url})를 확인해 주세요.`,
    ].join("\n");
  }

  function deliveryMarkdown(delivery) {
    const lines = [
      "## 구매 증빙",
      `### ${delivery.asset_title}`,
      "",
      `- **작품 ID**: \`${delivery.asset_id}\``,
      `- **라이선스 ID**: \`${delivery.license_id}\``,
      `- **결제 금액**: ${delivery.amount_usdc} ${delivery.currency}`,
      `- **네트워크 수수료**: ${delivery.network_fee_usdc} ${delivery.currency} · ${delivery.fee_sponsor} 부담`,
      `- **트랜잭션**: \`${delivery.transaction_signature}\``,
    ];
    if (delivery.download_expires_at) {
      lines.push(`- **다운로드 기한**: ${delivery.download_expires_at}`);
    }
    lines.push("", `[원본 다운로드](${delivery.download_url})`);
    return lines.join("\n");
  }

  function addAgentMessage(turn, event) {
    const fragment = elements.agentMessageTemplate.content.cloneNode(true);
    const exchange = fragment.querySelector(".agent-exchange");
    const avatar = fragment.querySelector(".exchange-avatar");
    const fromSeller = event.from === "seller_agent";
    const sender = fromSeller ? "SELLER AGENT" : "BUYER AGENT";
    const recipient = fromSeller ? "BUYER AGENT" : "SELLER AGENT";
    exchange.classList.add(fromSeller ? "seller-message" : "buyer-message");
    avatar.classList.add(fromSeller ? "seller-robot" : "buyer-robot");
    fragment.querySelector(".exchange-meta strong").textContent = sender;
    fragment.querySelector(".exchange-route").textContent = `→ ${recipient}`;
    fragment.querySelector(".exchange-meta b").textContent = event.protocol;
    const message = fragment.querySelector(".exchange-message");
    if (fromSeller && event.delivery) {
      exchange.classList.add("purchase-message");
      renderMarkdown(message, purchaseCompletionMarkdown(event.delivery));
    } else {
      renderMarkdown(message, event.text);
    }
    const existingExchange = event.call_id && state.agentMessagesByCall.get(event.call_id);
    if (fromSeller && existingExchange) {
      existingExchange.querySelector(".exchange-message").replaceChildren();
      existingExchange.classList.remove("is-loading");
      if (event.delivery) existingExchange.classList.add("purchase-message");
      existingExchange.querySelector(".exchange-meta strong").textContent = sender;
      existingExchange.querySelector(".exchange-route").textContent = `→ ${recipient}`;
      existingExchange.querySelector(".exchange-meta b").textContent = event.protocol;
      if (event.delivery) {
        renderMarkdown(existingExchange.querySelector(".exchange-message"), purchaseCompletionMarkdown(event.delivery));
      } else {
        renderMarkdown(existingExchange.querySelector(".exchange-message"), event.text);
      }
      addExecutionItem(existingExchange, {
        kind: "tool-result",
        title: "A2A response received",
        description: "Seller Agent returned the response shown above.",
        detail: event.text,
      });
      setExecutionStatus(existingExchange, "Completed");
      setExecutionLabel(existingExchange, "Excution");
      state.agentMessagesByCall.delete(event.call_id);
      setAssistantLoading(turn, true);
      return existingExchange;
    }
    turn.querySelector(".agent-dialogue").append(fragment);
    const exchangeElement = turn.querySelector(".agent-dialogue").lastElementChild;
    if (event.call_id) state.agentMessagesByCall.set(event.call_id, exchangeElement);
    if (!fromSeller) {
      addExecutionItem(exchangeElement, {
        kind: "tool-call",
        title: "A2A request prepared",
        description: "Buyer Agent prepared this request for Seller Agent.",
        detail: event.text,
      });
      setExecutionStatus(exchangeElement, "Queued", "queued");
    } else {
      setAssistantLoading(turn, true);
    }
    return exchangeElement;
  }

  function addSellerActivity(turn, event) {
    const fragment = elements.agentMessageTemplate.content.cloneNode(true);
    const exchange = fragment.querySelector(".agent-exchange");
    exchange.classList.add("seller-message", "is-loading");
    fragment.querySelector(".exchange-avatar").classList.add("seller-robot");
    fragment.querySelector(".exchange-meta strong").textContent = "SELLER AGENT";
    fragment.querySelector(".exchange-route").textContent = "→ BUYER AGENT";
    fragment.querySelector(".exchange-meta b").textContent = "A2A";
    const message = fragment.querySelector(".exchange-message");
    const typing = document.createElement("span");
    typing.className = "typing";
    typing.setAttribute("aria-label", "Seller Agent is processing");
    for (let index = 0; index < 3; index += 1) typing.append(document.createElement("i"));
    message.append(typing);
    turn.querySelector(".agent-dialogue").append(fragment);
    const exchangeElement = turn.querySelector(".agent-dialogue").lastElementChild;
    addExecutionItem(exchangeElement, {
      kind: "tool-call",
      title: event.tool,
      description: "Seller Agent is processing the A2A request.",
      detail: event.input,
    });
    setExecutionLabel(exchangeElement, event.tool);
    setExecutionStatus(exchangeElement, "Working", "working");
    if (event.call_id) state.agentMessagesByCall.set(event.call_id, exchangeElement);
    return exchangeElement;
  }

  function addLicenseDelivery(turn, delivery) {
    const fragment = elements.deliveryTemplate.content.cloneNode(true);
    const card = fragment.querySelector(".license-delivery");
    renderMarkdown(card.querySelector(".delivery-markdown"), deliveryMarkdown(delivery));
    turn.querySelector(".agent-dialogue").append(fragment);
  }

  function addNegotiationMessage(turn, event) {
    const fragment = elements.agentMessageTemplate.content.cloneNode(true);
    const exchange = fragment.querySelector(".agent-exchange");
    const avatar = fragment.querySelector(".exchange-avatar");
    const isOffer = event.type === "negotiation_offer";
    exchange.classList.add("negotiation-message", isOffer ? "buyer-message" : "seller-message");
    avatar.classList.add(isOffer ? "buyer-robot" : "seller-robot");
    fragment.querySelector(".exchange-meta strong").textContent = isOffer ? "구매 에이전트" : "판매 에이전트";
    fragment.querySelector(".exchange-route").textContent = isOffer ? "→ 판매 에이전트" : "→ 구매 에이전트";
    fragment.querySelector(".exchange-meta b").textContent = "가격 협상";
    const message = fragment.querySelector(".exchange-message");
    const statusLabels = {
      COUNTER_OFFER: "가격 제안",
      ACCEPT: "협상 수락",
      REJECT: "협상 거절",
    };
    const lines = isOffer
      ? ["**구매 제안**", `${event.offer_usdc} USDC`, "", `사용 목적: ${event.usage_type}`]
      : [
        `**${statusLabels[event.status] || event.status}**`,
        event.price_usdc ? `${event.price_usdc} USDC` : "",
        event.reason || "",
      ];
    renderMarkdown(message, lines.filter((line, index) => line || index < 2).join("\n"));
    turn.querySelector(".agent-dialogue").append(fragment);
    const exchangeElement = turn.querySelector(".agent-dialogue").lastElementChild;
    setExecutionLabel(exchangeElement, "가격 협상");
    setExecutionStatus(exchangeElement, isOffer ? "제안 전송" : "응답 수신");
    return exchangeElement;
  }

  function handleEvent(turn, event) {
    switch (event.type) {
      case "session":
        state.sessionId = event.session_id;
        addExecutionItem(turn, {
          title: "Request accepted",
          description: `Session ${event.session_id.slice(0, 8)}`,
        });
        break;
      case "status":
        if (event.status === "working") {
          setExecutionStatus(turn, "Working", "working");
        } else if (event.status === "completed") {
          setExecutionStatus(turn, "Completed");
          setExecutionLabel(turn, "Excution");
          addExecutionItem(turn, {
            kind: "tool-result",
            title: "Response completed",
            description: "The agent finished this turn.",
          });
        }
        break;
      case "tool_call":
        if (event.tool === "veriproof_seller_agent") {
          resetExecution(turn, event.tool);
          setAssistantLoading(turn, false);
          const buyerExchange = event.call_id && state.agentMessagesByCall.get(event.call_id);
          if (buyerExchange) {
            addExecutionItem(buyerExchange, {
              kind: "tool-call",
              title: event.tool,
              description: "Buyer Agent sent this A2A request to Seller Agent.",
              detail: event.input,
            });
            setExecutionLabel(buyerExchange, event.tool);
            setExecutionStatus(buyerExchange, "Sent");
          }
          addSellerActivity(turn, event);
          break;
        }
        if (event.tool === "negotiate_usdc_license" || event.tool === "negotiate_usdc_with_list_price_fallback") break;
        addExecutionItem(turn, {
          kind: "tool-call",
          title: event.tool,
          description: "Tool called with the runtime arguments shown below",
          detail: event.input,
        });
        setExecutionLabel(turn, event.tool);
        setExecutionStatus(turn, "Working", "working");
        break;
      case "tool_result":
        if (event.tool === "veriproof_seller_agent") {
          const sellerExchange = event.call_id && state.agentMessagesByCall.get(event.call_id);
          if (sellerExchange) {
            setExecutionStatus(sellerExchange, "Responding", "working");
          }
          break;
        }
        if (event.tool === "negotiate_usdc_license" || event.tool === "negotiate_usdc_with_list_price_fallback") break;
        addExecutionItem(turn, {
          kind: "tool-result",
          title: `${event.tool} returned`,
          description: "Tool execution completed",
          detail: event.output,
        });
        break;
      case "assistant_message":
        showAssistantMessage(turn, event.text);
        break;
      case "agent_message":
        addAgentMessage(turn, event);
        break;
      case "negotiation_offer":
      case "negotiation_result":
        addNegotiationMessage(turn, event);
        break;
      case "license_delivery":
        addLicenseDelivery(turn, event.delivery);
        break;
      case "payment_approval_required":
        setExecutionStatus(turn, "Approval required", "working");
        setExecutionLabel(turn, "Payment approval");
        addExecutionItem(turn, {
          kind: "tool-call",
          title: "Payment paused",
          description: "Waiting for explicit user approval before signing",
        });
        openPaymentDialog(event);
        break;
      case "error":
        showAssistantMessage(turn, event.message, true);
        setExecutionStatus(turn, "Failed", "failed");
        setExecutionLabel(turn, "Excution");
        addExecutionItem(turn, {
          kind: "failed",
          title: "Execution failed",
          description: event.message,
        });
        break;
      default:
        break;
    }
  }

  async function readEventStream(response, turn) {
    if (!response.body) throw new Error("The response stream is unavailable.");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let receivedFinalMessage = false;

    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() || "";
      for (const block of blocks) {
        const data = block.split(/\r?\n/)
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (!data) continue;
        const event = JSON.parse(data);
        if (event.type === "assistant_message") receivedFinalMessage = true;
        handleEvent(turn, event);
        window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "smooth" });
      }
      if (done) break;
    }
    if (!receivedFinalMessage && !turn.querySelector(".assistant-message.error")) {
      throw new Error("The agent completed without a user-facing response.");
    }
  }

  async function sendMessage(message, turn, paymentDecision = null) {
    const body = {
      message,
      session_id: state.sessionId,
      payment_mode: selectedPaymentMode(),
    };
    if (paymentDecision) body.payment_decision = paymentDecision;
    const response = await fetch("/demo/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      let text = `Buyer Agent returned HTTP ${response.status}.`;
      try {
        const payload = await response.json();
        if (payload.error) text = payload.error;
      } catch (_) {
        // The HTTP status remains the authoritative error when no JSON is returned.
      }
      throw new Error(text);
    }
    await readEventStream(response, turn);
  }

  async function submit(message, paymentDecision = null) {
    if (state.busy) return;
    state.busy = true;
    elements.send.disabled = true;
    elements.modeInputs.forEach((input) => { input.disabled = true; });
    elements.intro.classList.add("is-hidden");
    const turn = createTurn(message);
    addExecutionItem(turn, {
      title: "Preparing request",
      description: "Sending the buyer instruction to the live agent",
    });
    setExecutionLabel(turn, "Preparing request");
    setExecutionStatus(turn, "Working", "working");
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "smooth" });
    try {
      await sendMessage(message, turn, paymentDecision);
    } catch (error) {
      showAssistantMessage(turn, error.message || "The request failed.", true);
      setExecutionStatus(turn, "Failed", "failed");
      setExecutionLabel(turn, "Excution");
      addExecutionItem(turn, {
        kind: "failed",
        title: "Connection failed",
        description: error.message || "The request failed.",
      });
    } finally {
      state.busy = false;
      elements.send.disabled = false;
      elements.modeInputs.forEach((input) => { input.disabled = false; });
      if (state.pendingApproval && elements.paymentDialog.open) {
        setApprovalControlsEnabled(true);
      }
      elements.input.focus();
    }
  }

  function submitPaymentDecision(decision) {
    if (state.busy || !state.pendingApproval) return;
    const paymentDecision = { ...state.pendingApproval, decision };
    state.pendingApproval = null;
    elements.paymentDialog.close();
    const label = decision === "approved" ? "Payment approved" : "Payment declined";
    submit(label, paymentDecision);
  }

  elements.form.addEventListener("submit", (event) => {
    event.preventDefault();
    const message = elements.input.value.trim();
    if (!message) return;
    elements.input.value = "";
    resizeInput();
    submit(message);
  });

  elements.input.addEventListener("input", resizeInput);
  elements.input.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      elements.form.requestSubmit();
    }
  });

  document.querySelectorAll("[data-prompt]").forEach((button) => {
    button.addEventListener("click", () => {
      elements.input.value = button.dataset.prompt;
      resizeInput();
      elements.input.focus();
    });
  });

  elements.approvePayment.addEventListener("click", () => {
    submitPaymentDecision("approved");
  });
  elements.declinePayment.addEventListener("click", () => {
    submitPaymentDecision("declined");
  });
  elements.paymentDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
  });

  checkAgent();
})();
