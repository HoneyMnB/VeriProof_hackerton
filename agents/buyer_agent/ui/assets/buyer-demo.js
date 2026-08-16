(() => {
  "use strict";

  const state = { sessionId: null, busy: false, pendingApproval: null };
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
    turn.querySelector(".user-message").textContent = message;
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

  function addExecutionItem(turn, { kind = "status", title, description, detail }) {
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
    turn.querySelector(".execution-list").append(fragment);
  }

  function setExecutionStatus(turn, text, kind = "") {
    const status = turn.querySelector(".execution-status");
    status.textContent = text;
    status.className = `execution-status ${kind}`.trim();
  }

  function showAssistantMessage(turn, text, isError = false) {
    const message = turn.querySelector(".assistant-message");
    message.classList.remove("is-loading");
    message.classList.toggle("error", isError);
    message.textContent = text;
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
    fragment.querySelector(".exchange-message").textContent = event.text;
    turn.querySelector(".agent-dialogue").append(fragment);
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
          addExecutionItem(turn, {
            kind: "tool-result",
            title: "Response completed",
            description: "The agent finished this turn.",
          });
        }
        break;
      case "tool_call":
        addExecutionItem(turn, {
          kind: "tool-call",
          title: event.tool,
          description: "Tool called with the runtime arguments shown below",
          detail: event.input,
        });
        break;
      case "tool_result":
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
      case "payment_approval_required":
        setExecutionStatus(turn, "Approval required", "working");
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
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "smooth" });
    try {
      await sendMessage(message, turn, paymentDecision);
    } catch (error) {
      showAssistantMessage(turn, error.message || "The request failed.", true);
      setExecutionStatus(turn, "Failed", "failed");
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
