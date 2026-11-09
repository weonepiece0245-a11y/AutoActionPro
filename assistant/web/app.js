// 星期五界面:实时对话(SSE)、语音输入输出、授权弹窗、左右面板。
const token = new URLSearchParams(location.search).get("t") || "";
const $ = (id) => document.getElementById(id);
const orbState = { mode: "idle", level: 0 };

// ---------- 3D 全息球(CDN 打不开时用 CSS 版本) ----------
import("./orb.js")
  .then((m) => m.start($("orb"), orbState))
  .catch(() => $("cssOrb").classList.add("on"));

// 光球亮度(左边“系统”里的滑杆,记在本机)
function setBrightness(percent) {
  const filter = percent === 100 ? "" : `brightness(${percent / 100})`;
  $("orb").style.filter = filter;
  $("cssOrb").style.filter = filter;
  $("brightness").value = percent;
  $("brightOut").textContent = percent + "%";
}
let savedBright = 70;
try { savedBright = Number(localStorage.getItem("friday-brightness")) || 70; } catch {}
setBrightness(savedBright);
$("brightness").addEventListener("input", (e) => {
  const percent = Number(e.target.value);
  setBrightness(percent);
  try { localStorage.setItem("friday-brightness", String(percent)); } catch {}
});

// ---------- 状态 ----------
const MODE_TEXT = { idle: "待机 STANDBY", thinking: "思考中 PROCESSING", speaking: "说话中 SPEAKING", listening: "聆听中 LISTENING" };
let busy = false;
function setMode(mode) {
  orbState.mode = mode;
  document.body.dataset.mode = mode;
  $("modeText").textContent = MODE_TEXT[mode] || mode;
}
function refreshMode() {
  if (recorder) return setMode("listening");
  if (speaking) return setMode("speaking");
  setMode(busy ? "thinking" : "idle");
}

// ---------- 请求 ----------
async function api(path, body, extraHeaders = {}) {
  const isBlob = body instanceof Blob;
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "X-Friday-Token": token, ...(isBlob ? {} : { "Content-Type": "application/json" }), ...extraHeaders },
    body: body === undefined ? undefined : isBlob ? body : JSON.stringify(body),
  });
  return res.json().catch(() => ({}));
}

function toast(text, ms = 2600) {
  const el = $("toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), ms);
}

// ---------- 时钟 ----------
const WEEK = "日一二三四五六";
function tick() {
  const d = new Date();
  $("clockTime").textContent = d.toLocaleTimeString("zh-CN", { hour12: false });
  $("clockDate").textContent = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} 星期${WEEK[d.getDay()]}`;
}
setInterval(tick, 1000);
tick();

// ---------- 对话记录 ----------
const log = $("log");
let current = null; // 正在生成的回答气泡

function scrollDown() { log.scrollTop = log.scrollHeight; }
function addMessage(kind, text, source) {
  const el = document.createElement("div");
  el.className = `msg ${kind}${source && source !== "web" ? " source-" + source : ""}`;
  const who = document.createElement("span");
  who.className = "who";
  who.textContent = kind === "user" ? "YOU" : "FRIDAY";
  const body = document.createElement("span");
  body.className = "text";
  body.textContent = text;
  el.append(who, body);
  log.append(el);
  scrollDown();
  return el;
}
function addLine(text, cls = "") {
  const el = document.createElement("div");
  el.className = `status-line ${cls}`;
  el.textContent = text;
  if (current) log.insertBefore(el, current); else log.append(el);
  scrollDown();
}
function startAnswer(source) {
  current = addMessage("friday", "", source);
  current.classList.add("typing");
  current.querySelector(".text").innerHTML = '<span class="thinking"><b></b><b></b><b></b></span>';
  current.dataset.empty = "1";
}
function appendDelta(text) {
  if (!current) startAnswer();
  const body = current.querySelector(".text");
  if (current.dataset.empty) { body.textContent = ""; delete current.dataset.empty; }
  body.textContent += text;
  scrollDown();
}
function finishAnswer(text, source) {
  if (!current) current = addMessage("friday", "", source);
  current.classList.remove("typing");
  current.querySelector(".text").textContent = text;
  delete current.dataset.empty;
  current = null;
  scrollDown();
}

// ---------- 打招呼:只在窗口里还没有对话时(第一次打开或点了新话题之后) ----------
let greeting = "";
let greeted = false;
async function greet() {
  if (greeted || !greeting || log.querySelector(".msg")) return;
  greeted = true;
  const el = addMessage("friday", greeting);
  el.classList.add("greeting");
  if (speakOn) {
    const r = await api("/api/speak", { text: greeting.split("\n")[0] }).catch(() => ({}));
    if (r.url) enqueueAudio(r.url);
  }
}

// ---------- 实时事件 ----------
function handle(ev) {
  switch (ev.type) {
    case "user":
      addMessage("user", ev.text, ev.source);
      break;
    case "thinking":
      busy = true; refreshMode(); startAnswer(ev.source); break;
    case "delta":
      appendDelta(ev.text); break;
    case "status":
      addLine(ev.text); break;
    case "done":
      busy = false; finishAnswer(ev.text, ev.source); refreshMode(); loadState(); break;
    case "error":
      busy = false;
      if (current) { current.remove(); current = null; }
      addLine(ev.text, "error"); refreshMode(); break;
    case "audio":
      enqueueAudio(ev.url, ev.turn); break;
    case "reminder":
      addLine(ev.text, "reminder"); beep(880, 0.15); loadState(); break;
    case "approval":
      showApproval(ev); break;
    case "approval_done":
      if (approvalId === ev.id) hideApproval(); break;
    case "model":
      showModel(ev.model, ev.mode); break;
    case "update_done":
      resetUpdateBtn(); break;
    case "restarting":
      waitForRestart(); break;
    case "ready":
      greet(); scrollDown(); break;
    case "cleared":
      log.innerHTML = ""; current = null; addLine("已开始新话题"); break;
  }
  $("sendBtn").disabled = busy;
}

function connect() {
  const source = new EventSource(`/events?t=${encodeURIComponent(token)}`);
  // 每次连上都会先收到之前的对话;先清空,断线重连时才不会重复
  source.onopen = () => { log.innerHTML = ""; current = null; };
  source.onmessage = (e) => { try { handle(JSON.parse(e.data)); } catch (err) { console.error(err); } };
  source.onerror = () => { setMode("idle"); };
}

// ---------- 面板 ----------
function fillList(id, items, empty, render) {
  const ul = $(id);
  ul.innerHTML = "";
  if (!items || !items.length) {
    const li = document.createElement("li"); li.className = "empty"; li.textContent = empty; ul.append(li); return;
  }
  for (const item of items) ul.append(render(item));
}
function li(text, meta) {
  const el = document.createElement("li");
  el.textContent = text;
  if (meta) { const m = document.createElement("span"); m.className = "meta"; m.textContent = meta; el.append(m); }
  return el;
}
async function loadState() {
  const s = await api("/api/state");
  if (!s || s.error) return;
  $("versionText").textContent = s.version || "未记录";
  if (s.settings) settingsState = s.settings;
  if (s.model) showModel(s.model.model, s.model.mode);
  if (s.startup_error) { $("banner").hidden = false; $("banner").textContent = s.startup_error; }
  fillList("memoryList", s.memories, "还没有记忆,多跟我聊聊你自己", (m) => li(m.text, m.added));
  fillList("reminderList", s.reminders, "没有提醒", (r) => li(r.text, r.when));
  fillList("todoList", s.todos, "没有待办", (t) => li(t.text, t.due || ""));
  setCount("memory", (s.memories || []).length);
  setCount("reminders", (s.reminders || []).length);
  setCount("tasks", (s.todos || []).length);
  const sys = $("sysList");
  sys.innerHTML = "";
  for (const [name, on, label] of s.system || []) {
    const item = document.createElement("li");
    const tag = document.createElement("span");
    tag.className = `tag ${on === "warn" ? "warn" : on ? "on" : "off"}`;
    tag.textContent = label || (on ? "ONLINE" : "OFF");
    item.append(document.createTextNode(name), tag);
    sys.append(item);
  }
  return s;
}

// ---------- 发送 ----------
let speakOn = localStorage.getItem("friday-speak") !== "0";
function updateSpeakBtn() {
  $("speakBtn").setAttribute("aria-pressed", String(speakOn));
  $("setSpeak").checked = speakOn;
}
updateSpeakBtn();
function setSpeak(on) {
  speakOn = on;
  try { localStorage.setItem("friday-speak", speakOn ? "1" : "0"); } catch {}
  updateSpeakBtn();
  ensureAudio();
  if (!speakOn) stopSpeaking();
}
$("speakBtn").onclick = () => setSpeak(!speakOn);
$("setSpeak").onchange = (e) => setSpeak(e.target.checked);

$("form").onsubmit = async (e) => {
  e.preventDefault();
  const text = $("input").value.trim();
  if (!text || busy) return;
  ensureAudio();
  stopSpeaking();
  beep(660, 0.06);
  $("input").value = "";
  const r = await api("/api/ask", { text, speak: speakOn });
  if (r.error) toast(r.error);
};
// ---------- 面板:点标题折叠;右上角按钮隐藏两边(都记在本机) ----------
function loadPrefs(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}
function savePrefs(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch {}
}
const collapsed = new Set(loadPrefs("friday-collapsed", []));
for (const panel of document.querySelectorAll(".panel[data-panel]")) {
  const h2 = panel.querySelector("h2");
  h2.tabIndex = 0;
  h2.setAttribute("role", "button");
  const count = document.createElement("span");
  count.className = "count";
  h2.querySelector("span").after(count);
  const apply = () => {
    panel.classList.toggle("collapsed", collapsed.has(panel.dataset.panel));
    h2.setAttribute("aria-expanded", String(!collapsed.has(panel.dataset.panel)));
  };
  const toggle = () => {
    const name = panel.dataset.panel;
    collapsed.has(name) ? collapsed.delete(name) : collapsed.add(name);
    savePrefs("friday-collapsed", [...collapsed]);
    apply();
  };
  h2.onclick = toggle;
  h2.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } };
  apply();
}
function setCount(name, n) {
  const el = document.querySelector(`.panel[data-panel="${name}"] h2 .count`);
  if (el) el.textContent = n ? `(${n})` : "";
}
let panelsOn = loadPrefs("friday-panels", true);
function applyPanels() {
  document.body.classList.toggle("focus", !panelsOn);
  $("panelsBtn").setAttribute("aria-pressed", String(panelsOn));
  $("panelsBtn").title = panelsOn ? "隐藏两边的面板" : "显示两边的面板";
  $("setPanels").checked = panelsOn;
}
function setPanels(on) { panelsOn = on; savePrefs("friday-panels", panelsOn); applyPanels(); }
$("panelsBtn").onclick = () => setPanels(!panelsOn);
$("setPanels").onchange = (e) => setPanels(e.target.checked);
applyPanels();

// ---------- 模型:自动 / 只用 Opus / 只用 Sonnet ----------
function showModel(label, mode) {
  $("modelChip").hidden = !label;
  $("modelChip").textContent = (label || "").toUpperCase();
  $("modelRow").hidden = false;
  if (mode) $("modelMode").value = mode;
}
$("modelMode").onchange = async (e) => {
  const r = await api("/api/command", { command: "model:" + e.target.value }).catch(() => ({ error: "星期五没有响应" }));
  if (r.error) toast(r.error);
  else toast(e.target.value === "auto" ? "自动选模型:日常用 Sonnet,难题用 Opus" : "已固定使用 " + e.target.selectedOptions[0].textContent.replace("只用 ", ""));
};

// ---------- 齿轮设定 ----------
let settingsState = null;
function fillSettings() {
  const s = settingsState;
  if (!s) return;
  $("setCallMe").value = s.call_me || "";
  const voice = $("setVoice");
  if (!voice.options.length) {
    for (const [key, desc] of s.voices || []) {
      const opt = document.createElement("option");
      opt.value = key; opt.textContent = desc;
      voice.append(opt);
    }
  }
  voice.value = s.voice;
  $("setRate").value = s.rate ?? "";
  $("autostartRow").hidden = s.autostart === null || s.autostart === undefined;
  const b = s.budget;
  $("budgetGroup").hidden = !b;
  if (b) {
    $("setBudget").value = b.budget_myr || 0;
    $("setCredit").value = b.credit_usd || "";
    $("setCreditSince").value = b.credit_since || "";
    $("setCreditSince").max = new Date().toISOString().slice(0, 10);
    const src = b.source === "console" ? "Console 实际花费" : "星期五估算";
    let note = `这个月已用 RM ${b.spent_myr.toFixed(2)}(${src})`;
    if (b.budget_myr) note += `,预算的 ${Math.round(b.ratio * 100)}%。用到 80% 提醒,90% 起只用 Sonnet,用完就暂停。`;
    if (b.remaining_usd !== null && b.remaining_usd !== undefined)
      note += b.remaining_usd > 0 ? ` 余额约 US$ ${b.remaining_usd.toFixed(2)}。` : " 余额可能用完了,去 Console 看看。";
    $("budgetNote").textContent = note;
    $("budgetNote").className = "set-note" + (b.ratio >= 0.8 ? " warn" : "");
    $("setAdminKey").value = "";
    $("setAdminKey").placeholder = b.admin_key_set ? "已保存(要换就重新填)" : "sk-ant-admin…";
    $("consoleNote").textContent = b.console_error ? "⚠ " + b.console_error
      : b.admin_key_set ? (b.source === "console" ? "✓ 已连接 Console,花费是真实数字" : "正在读取 Console……")
      : "没有 Admin Key 时用星期五自己的估算。Admin Key 只在你的电脑上保存,只用来读花费报表。";
    $("consoleNote").className = "set-note" + (b.console_error ? " warn" : "");
  }
  const g = s.google;
  $("googleRow").hidden = !g || g.state === "off";
  if (g) {
    const left = g.state === "ok" && g.days_left != null ? `(还剩约 ${Math.max(1, Math.ceil(g.days_left))} 天)` : "";
    $("googleState").textContent = (g.state === "ok" ? "正常" : g.label) + left;
    $("googleState").className = "google-state " + (g.state === "ok" ? "ok" : "warn");
  }
  $("setAutostart").checked = !!s.autostart;
}
async function saveSetting(change, ms = 3200) {
  const r = await api("/api/settings", change).catch(() => ({ error: "星期五没有响应" }));
  if (r.error) { toast(r.error, 4000); fillSettings(); return false; }
  toast(r.message || "已保存", ms);
  await loadState();
  fillSettings();
  return true;
}
function openSettings() { fillSettings(); $("settings").hidden = false; $("settingsClose").focus(); }
function closeSettings() { $("settings").hidden = true; }
$("settingsBtn").onclick = openSettings;
$("settingsClose").onclick = closeSettings;
$("settings").addEventListener("click", (e) => { if (e.target.id === "settings") closeSettings(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("settings").hidden) closeSettings(); });
$("saveCallMe").onclick = () => saveSetting({ call_me: $("setCallMe").value.trim() });
$("setCallMe").onkeydown = (e) => { if (e.key === "Enter") $("saveCallMe").click(); };
$("setVoice").onchange = (e) => saveSetting({ voice: e.target.value });
$("previewVoice").onclick = async () => {
  ensureAudio();
  const name = ($("setCallMe").value || "老板").trim();
  const r = await api("/api/speak", { text: `${name},你好,我是星期五,这是我现在的声音。` }).catch(() => ({}));
  if (!r.url) { toast("试听失败,检查一下网络"); return; }
  stopSpeaking();
  audioQueue.push(r.url);
  playNext();
};
$("saveRate").onclick = () => saveSetting({ rate: $("setRate").value.trim() });
$("setAutostart").onchange = (e) => saveSetting({ autostart: e.target.checked });
$("googleLogin").onclick = () => saveSetting({ google_login: true }, 9000);
$("saveBudget").onclick = () => saveSetting({ budget_myr: $("setBudget").value.trim() || 0 });
$("saveCredit").onclick = () => saveSetting({ credit_usd: $("setCredit").value.trim() || 0, credit_since: $("setCreditSince").value });
$("saveAdminKey").onclick = async () => {
  const key = $("setAdminKey").value.trim();
  if (!key && !confirm("要删除已保存的 Admin Key,改回星期五自己估算吗?")) return;
  if (await saveSetting({ admin_key: key }, 4000)) setTimeout(() => loadState().then(fillSettings), 4000);
};

// ---------- 检查更新:下载新版本后星期五自动重启,这个窗口自己刷新 ----------
function resetUpdateBtn() { $("updateBtn").disabled = false; $("updateBtn").textContent = "检查更新"; }
$("updateBtn").onclick = async () => {
  $("updateBtn").disabled = true;
  $("updateBtn").textContent = "检查中…";
  const r = await api("/api/command", { command: "update" }).catch(() => ({ error: "星期五没有响应" }));
  if (r.error) { toast(r.error); resetUpdateBtn(); }
};
function waitForRestart() {
  $("updateBtn").textContent = "重启中…";
  setMode("thinking");
  const started = Date.now();
  let wentDown = false;
  const poll = async () => {
    try {
      const s = await api("/api/state");
      if (s && !s.error && (wentDown || Date.now() - started > 10000)) { location.reload(); return; }
    } catch { wentDown = true; }
    if (Date.now() - started > 120000) { addLine("重启好像没成功,请从桌面重新打开星期五", "error"); resetUpdateBtn(); return; }
    setTimeout(poll, 1500);
  };
  setTimeout(poll, 1500);
}
$("clearBtn").onclick = () => api("/api/command", { command: "clear" });
$("powerBtn").onclick = async () => {
  if (!confirm("要完全关闭星期五吗?关闭后手机 Discord 和提醒都会停止。")) return;
  await api("/api/shutdown", {});
  document.body.innerHTML = '<div style="display:grid;place-items:center;height:100vh;font-family:Orbitron,sans-serif;letter-spacing:.3em;color:#22d3ee">FRIDAY OFFLINE · 星期五已关闭</div>';
};

// ---------- 声音:播放回答、保持音频设备唤醒、提示音 ----------
let audioCtx = null;
let analyser = null;
let speaking = false;
const audioEl = new Audio();
function ensureAudio() {
  if (audioCtx) { audioCtx.resume(); return audioCtx; }
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 512;
  const src = audioCtx.createMediaElementSource(audioEl);
  src.connect(analyser);
  analyser.connect(audioCtx.destination);
  // 一直播放听不见的极小声音,防止耳机/音箱休眠导致开头被吞
  const keep = audioCtx.createOscillator();
  const gain = audioCtx.createGain();
  gain.gain.value = 0.0004;
  keep.frequency.value = 30;
  keep.connect(gain).connect(audioCtx.destination);
  keep.start();
  return audioCtx;
}
function rms(an) {
  const data = new Uint8Array(an.fftSize);
  an.getByteTimeDomainData(data);
  let sum = 0;
  for (const v of data) { const x = (v - 128) / 128; sum += x * x; }
  return Math.sqrt(sum / data.length);
}
// 回答是一句一句合成的,按顺序排队播放;新问题的语音来了,旧的就不读了
const audioQueue = [];
let audioTurn = null;
let ignoreTurn = -1;  // 被打断的那次回答,后面陆续到的语音也不读了
let levelLoop = false;
function enqueueAudio(url, turn) {
  if (!speakOn) return;
  if (turn !== undefined && turn <= ignoreTurn) return;
  if (turn !== undefined && turn !== audioTurn) { stopSpeaking(); audioTurn = turn; }
  audioQueue.push(url);
  if (!speaking) playNext();
}
function stopSpeaking() {
  if (audioTurn !== null) ignoreTurn = Math.max(ignoreTurn, audioTurn);
  audioQueue.length = 0;
  audioEl.pause();
  speaking = false; orbState.level = 0; refreshMode();
}
function playNext() {
  const url = audioQueue.shift();
  if (!url) { speaking = false; orbState.level = 0; refreshMode(); return; }
  ensureAudio();
  speaking = true; refreshMode();
  audioEl.src = `${url}?t=${encodeURIComponent(token)}`;
  audioEl.play().then(() => {
    if (levelLoop) return;
    levelLoop = true;
    const loop = () => {
      if (!speaking) { levelLoop = false; return; }
      orbState.level = Math.min(1, rms(analyser) * 4.5);
      requestAnimationFrame(loop);
    };
    loop();
  }).catch(() => { audioQueue.length = 0; speaking = false; refreshMode(); toast("点一下页面,才能播放声音"); });
}
audioEl.onended = audioEl.onerror = () => { if (speaking) playNext(); };
function beep(freq, dur) {
  if (!audioCtx) return;
  const o = audioCtx.createOscillator();
  const g = audioCtx.createGain();
  o.type = "sine"; o.frequency.value = freq;
  g.gain.setValueAtTime(0.05, audioCtx.currentTime);
  g.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + dur);
  o.connect(g).connect(audioCtx.destination);
  o.start(); o.stop(audioCtx.currentTime + dur);
}

// ---------- 语音输入:说完停顿 2 秒自动结束 ----------
let recorder = null;
$("micBtn").onclick = async () => {
  if (recorder) { recorder.stop(); return; }
  ensureAudio();
  stopSpeaking();
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  } catch {
    toast("无法打开麦克风,请检查麦克风权限");
    return;
  }
  const micAnalyser = audioCtx.createAnalyser();
  micAnalyser.fftSize = 1024;
  audioCtx.createMediaStreamSource(stream).connect(micAnalyser);
  const chunks = [];
  const type = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
  recorder = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
  recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  let spoke = false, quietSince = performance.now();
  const started = performance.now();
  const btn = $("micBtn");
  btn.classList.add("on");
  beep(990, 0.08);
  refreshMode();

  const watch = () => {
    if (!recorder) return;
    const level = rms(micAnalyser);
    orbState.level = Math.min(1, level * 6);
    btn.style.setProperty("--mic", orbState.level.toFixed(2));
    const now = performance.now();
    if (level > 0.035) { spoke = true; quietSince = now; }
    if ((spoke && now - quietSince > 2000) || (!spoke && now - started > 10000) || now - started > 60000) {
      recorder.stop();
      return;
    }
    requestAnimationFrame(watch);
  };
  recorder.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    btn.classList.remove("on");
    btn.style.removeProperty("--mic");
    recorder = null;
    orbState.level = 0;
    refreshMode();
    if (!spoke) { toast("没听到声音"); return; }
    addLine("识别中……");
    const r = await api("/api/voice", new Blob(chunks, { type: type || "audio/webm" }), { "X-Speak": speakOn ? "1" : "0" });
    if (!r.text) toast(r.error ? `语音识别失败:${r.error}` : "没听清,再说一次?", 3500);
  };
  recorder.start(250);
  watch();
};

// ---------- 授权弹窗 ----------
let approvalId = null;
function showApproval(ev) {
  approvalId = ev.id;
  $("approvalText").textContent = ev.text;
  $("approveAll").hidden = !!ev.always;
  $("approvalNote").textContent = ev.always ? "这一步每次都要确认。" : "5 分钟内不处理会自动拒绝。";
  $("approval").hidden = false;
  beep(520, 0.2);
  $("approveYes").focus();
}
function hideApproval() { $("approval").hidden = true; approvalId = null; }
async function answerApproval(answer) {
  if (!approvalId) return;
  const id = approvalId;
  hideApproval();
  await api("/api/approve", { id, answer });
}
$("approveYes").onclick = () => answerApproval("yes");
$("approveNo").onclick = () => answerApproval("no");
$("approveAll").onclick = () => answerApproval("all");
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && approvalId) answerApproval("no"); });

// ---------- 启动 ----------
(async () => {
  setMode("idle");
  const s = await loadState();
  greeting = (s && s.greeting) || "";
  connect();
  setInterval(loadState, 30000);
  $("input").focus();
})();
