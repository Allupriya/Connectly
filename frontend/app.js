// ---------------------------------------------------------------------------
// ChatSystem frontend: login, chat list, real-time messaging over WebSocket.
// ---------------------------------------------------------------------------

const state = {
  me: null,            // { id, username }
  ws: null,
  chats: [],           // list of chat summaries
  activeChatId: null,
  onlineUsers: new Set(),
  typingTimers: {},    // chatId -> timeout id (for clearing typing state)
  editing: null,       // { id } when editing one of my messages
  replyingTo: null,    // { id, sender_username, preview } when composing a reply
  reads: {},           // chatId -> { userId: iso timestamp of last read }
  menuMsg: null,       // message the long-press / context menu is acting on
  unread: {},          // chatId -> number of unread messages
};

const BASE_TITLE = "ChatSystem";

const LONGPRESS_MS = 500;   // hold this long to open the message menu
const SWIPE_THRESHOLD = 55; // px to drag before a swipe becomes a reply

// ---- Small helpers ----
const $ = (sel) => document.querySelector(sel);
const api = (path) => path; // same-origin

function initials(name) {
  return (name || "?").trim().slice(0, 2).toUpperCase();
}

function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

async function request(path, options = {}) {
  const url = new URL(api(path), window.location.origin);
  // Auth: send the secret session token as a Bearer header (never the user id).
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.me && state.me.token && !path.startsWith("/api/login")) {
    headers["Authorization"] = `Bearer ${state.me.token}`;
  }
  const res = await fetch(url, {
    headers,
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Login
// ---------------------------------------------------------------------------
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = $("#username-input").value.trim();
  if (!username) return;
  try {
    const me = await request("/api/login", {
      method: "POST",
      body: JSON.stringify({ username }),
    });
    state.me = me;
    localStorage.setItem("chat_user", JSON.stringify(me));
    await startApp();
  } catch (err) {
    toast(err.message || "Login failed");
  }
});

$("#logout-btn").addEventListener("click", () => {
  localStorage.removeItem("chat_user");
  if (state.ws) state.ws.close();
  location.reload();
});

async function startApp() {
  $("#login-screen").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#my-name").textContent = state.me.username;
  $("#my-avatar").textContent = initials(state.me.username);
  connectWebSocket();
  await loadChats();
  setupPushNotifications();  // fire-and-forget; asks for permission once
}

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------
function connectWebSocket() {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(
    `${proto}://${window.location.host}/ws?token=${encodeURIComponent(state.me.token)}`
  );
  state.ws = ws;

  ws.onmessage = (event) => handleWsMessage(JSON.parse(event.data));
  ws.onclose = () => {
    // Try to reconnect after a short delay.
    setTimeout(() => { if (state.me) connectWebSocket(); }, 2000);
  };
}

function wsSend(payload) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify(payload));
  }
}

function handleWsMessage(data) {
  switch (data.type) {
    case "presence_snapshot":
      state.onlineUsers = new Set(data.online_users);
      renderChats();
      updatePeerStatus();
      break;
    case "presence":
      if (data.online) state.onlineUsers.add(data.user_id);
      else state.onlineUsers.delete(data.user_id);
      renderChats();
      updatePeerStatus();
      break;
    case "message":
      onIncomingMessage(data.message);
      break;
    case "message_edited":
      onMessageEdited(data.message);
      break;
    case "message_deleted":
      onMessageDeleted(data.message);
      break;
    case "read":
      onRead(data);
      break;
    case "typing":
      onTyping(data);
      break;
    case "chat_created":
      state.chats.unshift(data.chat);
      renderChats();
      break;
  }
}

// ---------------------------------------------------------------------------
// Chats
// ---------------------------------------------------------------------------
async function loadChats() {
  state.chats = await request("/api/chats");
  for (const chat of state.chats) {
    if (!state.reads[chat.id]) state.reads[chat.id] = { ...(chat.last_read || {}) };
    // Seed unread badges from the server so counts survive a reload. The
    // currently open chat is considered read.
    if (chat.id !== state.activeChatId && chat.unread_count > 0) {
      state.unread[chat.id] = chat.unread_count;
    }
  }
  renderChats();
  updateDocTitle();
}

function renderChats() {
  const list = $("#chat-list");
  list.innerHTML = "";
  for (const chat of state.chats) {
    const item = document.createElement("div");
    item.className = "chat-item" + (chat.id === state.activeChatId ? " active" : "");
    item.onclick = () => openChat(chat.id);

    const online = chat.type === "dm" && isChatPeerOnline(chat);
    const preview = chat.last_message
      ? messagePreview(chat.last_message)
      : "No messages yet";
    const unread = state.unread[chat.id] || 0;
    const badge = unread > 0
      ? `<span class="unread-badge">${unread > 99 ? "99+" : unread}</span>`
      : "";

    item.innerHTML = `
      <span class="avatar ${online ? "online" : ""}">${initials(chat.name)}</span>
      <div class="chat-meta">
        <div class="chat-name">${escapeHtml(chat.name)}</div>
        <div class="chat-preview">${escapeHtml(preview)}</div>
      </div>
      ${badge}`;
    list.appendChild(item);
  }
}

function isChatPeerOnline(chat) {
  const peer = chat.members.find((m) => m.id !== state.me.id);
  return peer && state.onlineUsers.has(peer.id);
}

function messagePreview(msg) {
  if (msg.deleted) return "\uD83D\uDEAB Message deleted";
  if (msg.msg_type === "image") return "\uD83D\uDCF7 Photo";
  if (msg.msg_type === "file") return "\uD83D\uDCCE " + (msg.file_name || "File");
  return msg.content;
}

function getChat(chatId) {
  return state.chats.find((c) => c.id === chatId);
}

async function openChat(chatId) {
  if (state.editing) cancelEdit();
  state.activeChatId = chatId;
  clearUnread(chatId);
  $("#app").classList.add("chat-open");  // Mobile: slide in the conversation.
  renderChats();

  const chat = getChat(chatId);
  $("#empty-state").classList.add("hidden");
  $("#conversation").classList.remove("hidden");
  $("#peer-name").textContent = chat.name;
  $("#peer-avatar").textContent = initials(chat.name);
  updatePeerStatus();

  const messages = await request(`/api/chats/${chatId}/messages`);
  const container = $("#messages");
  container.innerHTML = "";
  for (const msg of messages) appendMessage(msg);
  scrollToBottom();
  refreshReceipts();

  // Tell the other members we've now seen this chat.
  wsSend({ type: "read", chat_id: chatId });
}

function updatePeerStatus() {
  if (!state.activeChatId) return;
  const chat = getChat(state.activeChatId);
  if (!chat) return;
  const statusEl = $("#peer-status");
  if (chat.type === "dm") {
    const online = isChatPeerOnline(chat);
    statusEl.textContent = online ? "online" : "offline";
    statusEl.classList.toggle("online", online);
  } else {
    const onlineCount = chat.members.filter((m) => state.onlineUsers.has(m.id)).length;
    statusEl.textContent = `${chat.members.length} members, ${onlineCount} online`;
    statusEl.classList.remove("online");
  }
  // Refresh peer avatar online dot.
  const avatar = $("#peer-avatar");
  avatar.classList.toggle("online", chat.type === "dm" && isChatPeerOnline(chat));
}

// ---------------------------------------------------------------------------
// Messages
// ---------------------------------------------------------------------------
function messageBodyHtml(msg, mine, showSender) {
  let body = `<div class="swipe-arrow">\u21A9</div>`;
  if (showSender) body += `<div class="sender">${escapeHtml(msg.sender_username)}</div>`;

  if (msg.reply_to) {
    const who = msg.reply_to.sender_username === state.me.username
      ? "You"
      : msg.reply_to.sender_username || "";
    body += `<div class="reply-quote">
      <span class="reply-quote-name">${escapeHtml(who)}</span>
      <span class="reply-quote-text">${escapeHtml(msg.reply_to.preview || "")}</span>
    </div>`;
  }

  if (msg.deleted) {
    body += `<div class="text deleted-text">\uD83D\uDEAB This message was deleted</div>`;
  } else {
    if (msg.msg_type === "image" && msg.file_url) {
      body += `<img src="${msg.file_url}" alt="image" />`;
    } else if (msg.msg_type === "file" && msg.file_url) {
      body += `<a class="file-link" href="${msg.file_url}" target="_blank" download>\uD83D\uDCCE ${escapeHtml(msg.file_name || "Download")}</a>`;
    }
    if (msg.content) body += `<div class="text">${escapeHtml(msg.content)}</div>`;
  }

  const editedMark = !msg.deleted && msg.edited_at ? `<span class="edited">edited</span>` : "";
  const receipt = mine && !msg.deleted ? `<span class="receipt">\u2713</span>` : "";
  body += `<div class="time">${editedMark}${fmtTime(msg.created_at)}${receipt}</div>`;
  return body;
}

function renderMessageEl(el, msg) {
  const mine = msg.sender_id === state.me.id;
  const chat = getChat(msg.chat_id) || getChat(state.activeChatId);
  const showSender = !mine && chat && chat.type === "group";

  el._msg = msg;
  el.dataset.id = msg.id;
  el.dataset.created = msg.created_at || "";
  el.classList.toggle("deleted", !!msg.deleted);
  el.innerHTML = messageBodyHtml(msg, mine, showSender);
}

function appendMessage(msg) {
  const container = $("#messages");
  const mine = msg.sender_id === state.me.id;
  const el = document.createElement("div");
  el.className = "message " + (mine ? "me" : "them");
  renderMessageEl(el, msg);
  attachMessageInteractions(el);
  container.appendChild(el);
}

// ---- Read receipts ----
function isSeenAt(chat, createdIso) {
  if (!chat || !createdIso) return false;
  const reads = state.reads[chat.id] || {};
  const others = chat.members.filter((m) => m.id !== state.me.id);
  if (!others.length) return false;
  return others.every((m) => reads[m.id] && reads[m.id] >= createdIso);
}

function refreshReceipts() {
  const chat = getChat(state.activeChatId);
  if (!chat) return;
  document.querySelectorAll("#messages .message.me").forEach((el) => {
    const mark = el.querySelector(".receipt");
    if (!mark) return;
    const seen = isSeenAt(chat, el.dataset.created);
    mark.textContent = seen ? "\u2713\u2713" : "\u2713";
    mark.classList.toggle("seen", seen);
  });
}

function onRead(data) {
  const map = state.reads[data.chat_id] || (state.reads[data.chat_id] = {});
  map[data.user_id] = data.at;
  if (data.chat_id === state.activeChatId) refreshReceipts();
}

function onMessageDeleted(msg) {
  const el = document.querySelector(`.message[data-id="${msg.id}"]`);
  if (el) renderMessageEl(el, msg);

  const chat = getChat(msg.chat_id);
  if (chat && chat.last_message && chat.last_message.id === msg.id) {
    chat.last_message = msg;
    renderChats();
  }
}

// ---- Message gestures: swipe-to-reply + long-press menu ----
function attachMessageInteractions(el) {
  let startX = 0, startY = 0, dx = 0, dragging = false, lpTimer = null, startOnImage = false;

  const clearLongPress = () => {
    if (lpTimer) { clearTimeout(lpTimer); lpTimer = null; }
  };

  // Route to the image menu when the gesture starts on an image, else the
  // regular message menu.
  const openMenuFor = (point) => {
    if (startOnImage) openImgMenu(el._msg, point);
    else openMsgMenu(el._msg, point);
  };

  el.addEventListener("touchstart", (e) => {
    if (el._msg && el._msg.deleted) return;
    const t = e.touches[0];
    startX = t.clientX; startY = t.clientY; dx = 0; dragging = true;
    startOnImage = e.target.tagName === "IMG";
    el.style.transition = "none";
    clearLongPress();
    lpTimer = setTimeout(() => {
      dragging = false;
      openMenuFor({ x: startX, y: startY });
    }, LONGPRESS_MS);
  }, { passive: true });

  el.addEventListener("touchmove", (e) => {
    if (!dragging) return;
    const t = e.touches[0];
    const mx = t.clientX - startX;
    const my = t.clientY - startY;
    if (Math.abs(mx) > Math.abs(my) && Math.abs(mx) > 8) {
      clearLongPress();               // it's a horizontal swipe, not a hold
      dx = Math.max(0, Math.min(mx, 90));
      el.style.transform = `translateX(${dx}px)`;
      el.classList.toggle("swipe-active", dx > SWIPE_THRESHOLD);
      if (e.cancelable) e.preventDefault();
    } else if (Math.abs(my) > 10) {
      clearLongPress();               // vertical scroll cancels the hold
    }
  }, { passive: false });

  const endSwipe = () => {
    clearLongPress();
    dragging = false;
    el.style.transition = "";
    if (dx > SWIPE_THRESHOLD && el._msg) startReply(el._msg);
    el.style.transform = "";
    el.classList.remove("swipe-active");
    dx = 0;
  };
  el.addEventListener("touchend", endSwipe);
  el.addEventListener("touchcancel", endSwipe);

  // Desktop: right-click opens the matching menu (image menu over an image).
  el.addEventListener("contextmenu", (e) => {
    if (el._msg && el._msg.deleted) return;
    e.preventDefault();
    if (e.target.tagName === "IMG") openImgMenu(el._msg, { x: e.clientX, y: e.clientY });
    else openMsgMenu(el._msg, { x: e.clientX, y: e.clientY });
  });
}

function onMessageEdited(msg) {
  const el = document.querySelector(`.message[data-id="${msg.id}"]`);
  if (el) renderMessageEl(el, msg);

  // Keep the chat-list preview in sync if this was the last message.
  const chat = getChat(msg.chat_id);
  if (chat && chat.last_message && chat.last_message.id === msg.id) {
    chat.last_message = msg;
    renderChats();
  }
}

// ---- Editing my own messages ----
function startEdit(id, content) {
  state.editing = { id };
  const input = $("#message-input");
  input.value = content;
  input.focus();
  $("#edit-banner").classList.remove("hidden");
}

function cancelEdit() {
  state.editing = null;
  $("#message-input").value = "";
  $("#edit-banner").classList.add("hidden");
}

$("#edit-cancel").addEventListener("click", cancelEdit);

// ---- Replying to a message ----
function previewOf(msg) {
  if (msg.deleted) return "Message deleted";
  if (msg.msg_type === "image") return "\uD83D\uDCF7 Photo";
  if (msg.msg_type === "file") return "\uD83D\uDCCE " + (msg.file_name || "File");
  return msg.content || "";
}

function startReply(msg) {
  if (state.editing) cancelEdit();
  state.replyingTo = {
    id: msg.id,
    sender_username: msg.sender_username,
    preview: previewOf(msg),
  };
  const who = msg.sender_id === state.me.id ? "You" : msg.sender_username;
  $("#reply-banner-name").textContent = who;
  $("#reply-banner-text").textContent = state.replyingTo.preview;
  $("#reply-banner").classList.remove("hidden");
  $("#message-input").focus();
}

function cancelReply() {
  state.replyingTo = null;
  $("#reply-banner").classList.add("hidden");
}

$("#reply-cancel").addEventListener("click", cancelReply);

// ---- Deleting a message ----
function deleteMessage(id) {
  if (!confirm("Delete this message?")) return;
  wsSend({ type: "delete", chat_id: state.activeChatId, message_id: id });
}

// ---- Long-press / right-click message menu ----
let menuJustOpened = false;

function openMsgMenu(msg, point) {
  if (!msg || msg.deleted) return;
  state.menuMsg = msg;
  // Ignore the synthetic click that follows a long-press touchend.
  menuJustOpened = true;
  setTimeout(() => { menuJustOpened = false; }, 350);
  const mine = msg.sender_id === state.me.id;
  const menu = $("#msg-menu");

  menu.querySelector('[data-action="copy"]').classList.toggle("hidden", msg.msg_type !== "text" || !msg.content);
  menu.querySelector('[data-action="edit"]').classList.toggle("hidden", !(mine && msg.msg_type === "text"));
  menu.querySelector('[data-action="delete"]').classList.toggle("hidden", !mine);

  menu.classList.remove("hidden");
  const rect = menu.getBoundingClientRect();
  const x = Math.min(point.x, window.innerWidth - rect.width - 8);
  const y = Math.min(point.y, window.innerHeight - rect.height - 8);
  menu.style.left = Math.max(8, x) + "px";
  menu.style.top = Math.max(8, y) + "px";
}

function closeMsgMenu() {
  $("#msg-menu").classList.add("hidden");
  state.menuMsg = null;
}

$("#msg-menu").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  const action = btn.dataset.action;
  const msg = state.menuMsg;
  closeMsgMenu();
  if (!msg) return;

  if (action === "copy") {
    if (navigator.clipboard) navigator.clipboard.writeText(msg.content || "");
  } else if (action === "reply") {
    startReply(msg);
  } else if (action === "edit") {
    startEdit(msg.id, msg.content || "");
  } else if (action === "delete") {
    deleteMessage(msg.id);
  }
});

// ---- Image action menu (View / Download / Copy / Delete) ----
function openImgMenu(msg, point) {
  if (!msg || msg.deleted || msg.msg_type !== "image" || !msg.file_url) return;
  state.menuMsg = msg;
  // Ignore the synthetic click that follows a long-press touchend.
  menuJustOpened = true;
  setTimeout(() => { menuJustOpened = false; }, 350);

  const menu = $("#img-menu");
  // Both the sender and the receiver can delete an image message.
  menu.querySelector('[data-action="delete"]').classList.remove("hidden");

  menu.classList.remove("hidden");
  const rect = menu.getBoundingClientRect();
  const x = Math.min(point.x, window.innerWidth - rect.width - 8);
  const y = Math.min(point.y, window.innerHeight - rect.height - 8);
  menu.style.left = Math.max(8, x) + "px";
  menu.style.top = Math.max(8, y) + "px";
}

function closeImgMenu() {
  $("#img-menu").classList.add("hidden");
  state.menuMsg = null;
}

$("#img-menu").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  const action = btn.dataset.action;
  const msg = state.menuMsg;
  closeImgMenu();
  if (!msg) return;

  if (action === "view") {
    openLightbox(msg.file_url);
  } else if (action === "download") {
    downloadFile(msg.file_url, msg.file_name);
  } else if (action === "copy") {
    copyImage(msg.file_url);
  } else if (action === "delete") {
    deleteMessage(msg.id);
  }
});

// Download the image/file to the device (works on both mobile and desktop).
async function downloadFile(url, name) {
  const fallbackName = (url.split("/").pop() || "download").split("?")[0];
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = await res.blob();
    const objUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = objUrl;
    a.download = name || fallbackName;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(objUrl), 2000);
    toast("Downloading...");
  } catch (err) {
    // Fallback: open in a new tab so the user can save it manually.
    toast("Opening image...");
    window.open(url, "_blank");
  }
}

// Copy the actual image to the clipboard; fall back to copying its link.
async function copyImage(url) {
  try {
    if (!navigator.clipboard || !window.ClipboardItem) throw new Error("no clipboard");
    // Convert to PNG via canvas: browsers only reliably accept image/png writes.
    const blob = await imageToPngBlob(url);
    await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
    toast("Image copied");
  } catch (err) {
    try {
      const abs = new URL(url, window.location.origin).href;
      await navigator.clipboard.writeText(abs);
      toast("Image link copied");
    } catch (_) {
      toast("Copy not supported");
    }
  }
}

function imageToPngBlob(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = "anonymous";  // same-origin /uploads, so this is safe
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      canvas.getContext("2d").drawImage(img, 0, 0);
      canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("toBlob failed"))), "image/png");
    };
    img.onerror = () => reject(new Error("image load failed"));
    img.src = url;
  });
}

// ---- Tiny transient toast for quick feedback ----
let toastTimer = null;
function toast(text) {
  const el = $("#toast");
  el.textContent = text;
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 1800);
}

// Dismiss the menu on any outside interaction.
document.addEventListener("click", (e) => {
  if (menuJustOpened) return;
  if (!e.target.closest("#msg-menu")) closeMsgMenu();
  if (!e.target.closest("#img-menu")) closeImgMenu();
});
document.addEventListener("scroll", () => { closeMsgMenu(); closeImgMenu(); }, true);

// Mobile: back button returns from a conversation to the chat list.
$("#back-btn").addEventListener("click", () => {
  $("#app").classList.remove("chat-open");
  state.activeChatId = null;
  renderChats();
});

// ---- Image tap -> action menu (View / Download / Copy / Delete) ----
$("#messages").addEventListener("click", (e) => {
  if (menuJustOpened) return;  // ignore the synthetic click after a long-press
  if (e.target.tagName !== "IMG") return;
  const el = e.target.closest(".message");
  if (el && el._msg) openImgMenu(el._msg, imgMenuPoint(e, e.target));
});

// Anchor the image menu near the tap on desktop, or below the image on mobile.
function imgMenuPoint(e, img) {
  if (typeof e.clientX === "number" && e.clientX) {
    return { x: e.clientX, y: e.clientY };
  }
  const r = img.getBoundingClientRect();
  return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
}

function openLightbox(src) {
  $("#lightbox-img").src = src;
  $("#lightbox").classList.remove("hidden");
}

function closeLightbox() {
  $("#lightbox").classList.add("hidden");
  $("#lightbox-img").src = "";
}

$("#lightbox").addEventListener("click", closeLightbox);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeLightbox();
    closeMsgMenu();
    closeImgMenu();
    if (state.editing) cancelEdit();
    if (state.replyingTo) cancelReply();
  }
});

function onIncomingMessage(msg) {
  // Update the chat list preview / ordering.
  const chat = getChat(msg.chat_id);
  if (chat) {
    chat.last_message = msg;
    state.chats = [chat, ...state.chats.filter((c) => c.id !== chat.id)];
    renderChats();
  } else {
    // We may not have this chat locally yet (e.g. brand new DM); reload list.
    loadChats();
  }

  const mine = msg.sender_id === state.me.id;
  const activeAndVisible = msg.chat_id === state.activeChatId && !document.hidden;

  if (msg.chat_id === state.activeChatId) {
    appendMessage(msg);
    scrollToBottom();
    refreshReceipts();
    // The chat is open in front of me, so mark it read for the sender.
    if (!mine && !document.hidden) {
      wsSend({ type: "read", chat_id: msg.chat_id });
    }
  }

  // Notify for messages from others that I'm not actively looking at.
  if (!mine && !activeAndVisible) {
    if (msg.chat_id !== state.activeChatId) bumpUnread(msg.chat_id);
    playPing();
    if (document.hidden) showLocalNotification(getChat(msg.chat_id), msg);
  }
}

$("#message-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("#message-input");
  const content = input.value.trim();
  if (!content || !state.activeChatId) return;

  if (state.editing) {
    wsSend({
      type: "edit",
      chat_id: state.activeChatId,
      message_id: state.editing.id,
      content,
    });
    cancelEdit();
  } else {
    wsSend({
      type: "message",
      chat_id: state.activeChatId,
      msg_type: "text",
      content,
      reply_to: state.replyingTo || undefined,
    });
    input.value = "";
    cancelReply();
  }
  sendTyping(false);
});

// Typing indicator (throttled).
let typingSent = false;
$("#message-input").addEventListener("input", () => {
  if (!state.activeChatId) return;
  if (!typingSent) {
    sendTyping(true);
    typingSent = true;
    setTimeout(() => { typingSent = false; }, 2000);
  }
});

function sendTyping(isTyping) {
  if (!state.activeChatId) return;
  wsSend({ type: "typing", chat_id: state.activeChatId, is_typing: isTyping });
}

function onTyping(data) {
  if (data.chat_id !== state.activeChatId) return;
  const el = $("#typing-indicator");
  if (data.is_typing) {
    el.textContent = `${data.username} is typing...`;
    clearTimeout(state.typingTimers[data.chat_id]);
    state.typingTimers[data.chat_id] = setTimeout(() => { el.textContent = ""; }, 3000);
  } else {
    el.textContent = "";
  }
}

// ---- File upload ----
$("#file-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file || !state.activeChatId) return;

  const form = new FormData();
  form.append("file", file);
  const url = new URL("/api/upload", window.location.origin);
  const res = await fetch(url, {
    method: "POST",
    body: form,
    headers: { Authorization: `Bearer ${state.me.token}` },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    toast(err.detail || "Upload failed");
    e.target.value = "";
    return;
  }
  const uploaded = await res.json();

  const isImage = (file.type || "").startsWith("image/");
  wsSend({
    type: "message",
    chat_id: state.activeChatId,
    msg_type: isImage ? "image" : "file",
    content: "",
    file_url: uploaded.url,
    file_name: uploaded.name,
    reply_to: state.replyingTo || undefined,
  });
  cancelReply();
  e.target.value = "";
});

function scrollToBottom() {
  const c = $("#messages");
  c.scrollTop = c.scrollHeight;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

// ---------------------------------------------------------------------------
// Notifications
// ---------------------------------------------------------------------------

// ---- Web Push (works even when the app/tab is closed) ----
let swRegistration = null;

async function setupPushNotifications() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return;
  try {
    swRegistration = await navigator.serviceWorker.register("/sw.js");

    const info = await request("/api/push/public-key");
    if (!info.enabled || !info.public_key) return;  // push disabled server-side

    if (Notification.permission === "default") {
      const perm = await Notification.requestPermission();
      if (perm !== "granted") return;
    }
    if (Notification.permission !== "granted") return;

    let sub = await swRegistration.pushManager.getSubscription();
    if (!sub) {
      sub = await swRegistration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(info.public_key),
      });
    }
    await request("/api/push/subscribe", {
      method: "POST",
      body: JSON.stringify(sub),
    });
  } catch (err) {
    console.warn("Push setup skipped:", err);
  }
}

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

// ---- In-app notifications: unread badges, tab title, sound ----
function totalUnread() {
  return Object.values(state.unread).reduce((a, b) => a + b, 0);
}

function updateDocTitle() {
  const n = totalUnread();
  document.title = n > 0 ? `(${n}) ${BASE_TITLE}` : BASE_TITLE;
}

function bumpUnread(chatId) {
  state.unread[chatId] = (state.unread[chatId] || 0) + 1;
  renderChats();
  updateDocTitle();
}

function clearUnread(chatId) {
  if (state.unread[chatId]) {
    delete state.unread[chatId];
    updateDocTitle();
  }
}

// Notification sound. If a custom audio file is present in the frontend folder
// (see NOTIFICATION_SOUND_URL) we play that; otherwise we fall back to a short
// synthesized "ping" via the Web Audio API (no file needed).
const NOTIFICATION_SOUND_URL = "/notification.mp3";
let notificationAudio = null;
let customSoundOk = true; // flips to false if the file is missing/unplayable

function playPing() {
  if (customSoundOk && tryCustomSound()) return;
  playSynthPing();
}

function tryCustomSound() {
  try {
    if (!notificationAudio) {
      notificationAudio = new Audio(NOTIFICATION_SOUND_URL);
      notificationAudio.preload = "auto";
      // If the file can't be loaded (e.g. not added yet), stop trying it.
      notificationAudio.addEventListener("error", () => {
        customSoundOk = false;
      });
    }
    notificationAudio.currentTime = 0;
    const p = notificationAudio.play();
    if (p && typeof p.catch === "function") {
      p.catch(() => {
        // Missing file or autoplay blocked: fall back to the synth ping.
        customSoundOk = false;
        playSynthPing();
      });
    }
    return true;
  } catch (_) {
    customSoundOk = false;
    return false;
  }
}

let audioCtx = null;
function playSynthPing() {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.connect(gain);
    gain.connect(audioCtx.destination);
    osc.type = "sine";
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.001, audioCtx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.15, audioCtx.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 0.25);
    osc.start();
    osc.stop(audioCtx.currentTime + 0.26);
  } catch (_) {
    // Autoplay may be blocked until the user interacts; ignore.
  }
}

// When the app is open but in a background tab, still show a system notification.
function showLocalNotification(chat, msg) {
  if (Notification.permission !== "granted") return;
  const isGroup = chat && chat.type === "group";
  const who = msg.sender_username || "New message";
  const title = isGroup ? (chat.name || "New message") : who;
  const body = isGroup ? `${who}: ${previewOf(msg)}` : previewOf(msg);
  try {
    if (swRegistration) {
      swRegistration.showNotification(title, { body, tag: msg.chat_id, renotify: true });
    } else {
      new Notification(title, { body });
    }
  } catch (_) { /* ignore */ }
}

// Refresh the tab title when the user comes back to the tab.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) updateDocTitle();
});

// ---------------------------------------------------------------------------
// New chat / group modal
// ---------------------------------------------------------------------------
const modal = {
  el: $("#modal"),
  mode: null,            // "dm" | "group"
  selected: new Set(),
};

$("#new-dm-btn").addEventListener("click", () => openModal("dm"));
$("#new-group-btn").addEventListener("click", () => openModal("group"));
$("#modal-cancel").addEventListener("click", closeModal);
$("#modal-confirm").addEventListener("click", confirmModal);

async function openModal(mode) {
  modal.mode = mode;
  modal.selected = new Set();
  $("#modal-title").textContent = mode === "dm" ? "Start a new chat" : "Create a group";
  $("#group-name-input").classList.toggle("hidden", mode !== "group");
  $("#group-name-input").value = "";

  const users = await request("/api/users");
  const picker = $("#user-picker");
  picker.innerHTML = "";
  for (const u of users) {
    const opt = document.createElement("div");
    opt.className = "user-option";
    const online = state.onlineUsers.has(u.id);
    opt.innerHTML = `<span class="avatar ${online ? "online" : ""}">${initials(u.username)}</span> ${escapeHtml(u.username)}`;
    opt.onclick = () => {
      if (mode === "dm") {
        modal.selected = new Set([u.id]);
        confirmModal();
      } else {
        opt.classList.toggle("selected");
        if (modal.selected.has(u.id)) modal.selected.delete(u.id);
        else modal.selected.add(u.id);
      }
    };
    picker.appendChild(opt);
  }
  modal.el.classList.remove("hidden");
}

function closeModal() {
  modal.el.classList.add("hidden");
}

async function confirmModal() {
  try {
    if (modal.mode === "dm") {
      const otherId = [...modal.selected][0];
      if (!otherId) return;
      const chat = await request("/api/chats/dm", {
        method: "POST",
        body: JSON.stringify({ other_user_id: otherId }),
      });
      upsertChat(chat);
      openChat(chat.id);
    } else {
      const name = $("#group-name-input").value.trim();
      if (!name) { alert("Please enter a group name"); return; }
      const chat = await request("/api/chats/group", {
        method: "POST",
        body: JSON.stringify({ name, member_ids: [...modal.selected] }),
      });
      upsertChat(chat);
      openChat(chat.id);
    }
    closeModal();
  } catch (err) {
    alert(err.message);
  }
}

function upsertChat(chat) {
  const existing = state.chats.find((c) => c.id === chat.id);
  if (!existing) state.chats.unshift(chat);
  renderChats();
}

// ---------------------------------------------------------------------------
// Auto-login if we have a saved user.
// ---------------------------------------------------------------------------
(async function init() {
  const saved = localStorage.getItem("chat_user");
  if (!saved) return;
  let me;
  try {
    me = JSON.parse(saved);
  } catch {
    localStorage.removeItem("chat_user");
    return;
  }
  if (!me || !me.token) {
    // Old sessions had no token; force a fresh, secure login.
    localStorage.removeItem("chat_user");
    return;
  }
  try {
    // Validate the stored token before showing the app.
    const fresh = await request("/api/login", {
      method: "POST",
      body: JSON.stringify({ token: me.token }),
    });
    state.me = { ...fresh, token: me.token };
    localStorage.setItem("chat_user", JSON.stringify(state.me));
    await startApp();
  } catch {
    // Token no longer valid (e.g. server data cleared) -> back to login.
    localStorage.removeItem("chat_user");
  }
})();
