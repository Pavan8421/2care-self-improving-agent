// Tiny web UI chat client — POST /api/chat with persistent session_id.

const messagesEl = document.getElementById("messages");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const statusEl = document.getElementById("status");
const sessionLabel = document.getElementById("session-label");
const newSessionBtn = document.getElementById("new-session");

const STORAGE_KEY = "clinic_scheduling_session_id";

function newId() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return `sess_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}

function getSessionId() {
  let id = localStorage.getItem(STORAGE_KEY);
  if (!id) {
    id = newId();
    localStorage.setItem(STORAGE_KEY, id);
  }
  return id;
}

function setSessionId(id) {
  localStorage.setItem(STORAGE_KEY, id);
  if (sessionLabel) {
    sessionLabel.textContent = id.slice(0, 8);
    sessionLabel.title = id;
  }
}

function setStatus(text) {
  if (statusEl) statusEl.textContent = text || "";
}

function clearMessages() {
  if (!messagesEl) return;
  messagesEl.innerHTML = "";
  const empty = document.createElement("p");
  empty.className = "empty";
  empty.id = "empty-hint";
  empty.textContent =
    "Say hello to start. The agent can verify a patient, offer real slots, and book after you confirm.";
  messagesEl.appendChild(empty);
}

function removeEmptyHint() {
  document.getElementById("empty-hint")?.remove();
}

function appendMessage(role, text) {
  if (!messagesEl) return;
  removeEmptyHint();
  const bubble = document.createElement("article");
  bubble.className = `msg ${role}`;
  const who = document.createElement("span");
  who.className = "who";
  who.textContent = role === "you" ? "You" : role === "error" ? "Error" : "Agent";
  const body = document.createElement("div");
  body.textContent = text;
  bubble.append(who, body);
  messagesEl.appendChild(bubble);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

async function sendMessage(message) {
  const session_id = getSessionId();
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id, message, debug: false }),
  });

  let data;
  try {
    data = await res.json();
  } catch {
    throw new Error(`Bad response (${res.status})`);
  }

  if (!res.ok) {
    throw new Error(data?.detail || data?.reply || `Request failed (${res.status})`);
  }

  if (data.session_id) setSessionId(data.session_id);
  return data;
}

async function startNewSession() {
  setStatus("Starting new session…");
  try {
    const res = await fetch("/api/session/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: getSessionId() }),
    });
    const data = await res.json();
    const id = data.session_id || newId();
    setSessionId(id);
  } catch {
    setSessionId(newId());
  }
  clearMessages();
  setStatus("New session ready.");
  input?.focus();
}

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = (input?.value || "").trim();
  if (!text || !input || !sendBtn) return;

  appendMessage("you", text);
  input.value = "";
  sendBtn.disabled = true;
  input.disabled = true;
  setStatus("Thinking…");

  try {
    const data = await sendMessage(text);
    appendMessage("agent", data.reply || "(empty reply)");
    setStatus("");
  } catch (err) {
    appendMessage("error", err?.message || "Could not reach the agent.");
    setStatus("Request failed — conversation state was kept.");
  } finally {
    sendBtn.disabled = false;
    input.disabled = false;
    input.focus();
  }
});

newSessionBtn?.addEventListener("click", () => {
  void startNewSession();
});

setSessionId(getSessionId());
clearMessages();
input?.focus();
