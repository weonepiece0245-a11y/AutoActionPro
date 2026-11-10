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
let lastActivity = "";
function refreshMode() {
  // 告诉星期五窗口正在录音/朗读,唤醒功能这时暂停,免得听到自己的声音
  const activity = JSON.stringify({ listening: !!recorder, speaking: !!speaking });
  if (activity !== lastActivity) {
    lastActivity = activity;
    api("/api/activity", JSON.parse(activity)).catch(() => {});
  }
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
  if (text && (!source || source === "web" || source === "voice")) addChips(current);
  current = null;
  scrollDown();
}

// ---------- 快捷键:叫出窗口后直接开始听 ----------
const params = new URLSearchParams(location.search);
let listenOnOpen = params.get("listen") === "1";
if (listenOnOpen) {  // 只在这一次生效,刷新页面不会又开始录音
  params.delete("listen");
  history.replaceState(null, "", `${location.pathname}?${params}`);
}
function startListening() {
  window.focus();
  if (!recorder && !$("micBtn").disabled) listen();
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
      removeChips();
      addMessage("user", ev.text, ev.source);
      break;
    case "thinking":
      busy = true; refreshMode(); startAnswer(ev.source); break;
    case "delta":
      appendDelta(ev.text); break;
    case "status":
      addLine(ev.text); break;
    case "done":
      busy = false; finishAnswer(ev.text, ev.source); refreshMode(); loadState();
      if (ev.source === "voice") { followUpTurn = ev.turn ?? "none"; maybeFollowUp(); }
      break;
    case "audio_end":
      audioEndTurns.add(ev.turn);
      maybeFollowUp(); break;
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
      greet(); scrollDown();
      if (listenOnOpen) { listenOnOpen = false; setTimeout(startListening, 400); }
      break;
    case "listen":
      startListening(); break;
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
  fillList("memoryList", s.memories, "还没有记忆,多跟我聊聊你自己", (m) => li(m.text, `${m.category || "其他"} · ${m.added}`));
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
  if ($("setFollowUp")) $("setFollowUp").checked = loadPrefs("friday-followup", true);
  if ($("setBarge")) $("setBarge").checked = loadPrefs("friday-barge", true);
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
$("setFollowUp").onchange = (e) => { followUpOn = e.target.checked; savePrefs("friday-followup", followUpOn); };
$("setBarge").onchange = (e) => { bargeOn = e.target.checked; savePrefs("friday-barge", bargeOn); if (!bargeOn) stopBargeWatch(); };

async function sendText(text) {
  if (!text || busy) return;
  ensureAudio();
  stopSpeaking();
  followUpTurn = null;  // 打字问的,不用接着听
  removeChips();
  beep(660, 0.06);
  const r = await api("/api/ask", { text, speak: speakOn });
  if (r.error) toast(r.error);
}
$("form").onsubmit = (e) => {
  e.preventDefault();
  const text = $("input").value.trim();
  if (!text || busy) return;
  $("input").value = "";
  sendText(text);
};

// ---------- 回答下面的快捷按钮(点了才算一次提问) ----------
const CHIPS = [
  ["再详细一点", "再详细一点"],
  ["简单一点", "说简单一点"],
  ["换个说法", "换个说法再讲一次"],
  ["记住这个", "把刚才的重点记住"],
];
function removeChips() { log.querySelectorAll(".chips").forEach((el) => el.remove()); }
function addChips(el) {
  removeChips();
  const row = document.createElement("div");
  row.className = "chips";
  for (const [label, text] of CHIPS) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.textContent = label;
    chip.onclick = () => sendText(text);
    row.append(chip);
  }
  el.after(row);
}
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
  const w = s.wake;
  $("wakeRow").hidden = !w;
  $("wakeOptions").hidden = !w;
  if (w) {
    $("setWake").checked = w.enabled;
    const phrases = $("setWakePhrase");
    if (!phrases.options.length) {
      for (const p of w.phrases) {
        const opt = document.createElement("option");
        opt.value = p; opt.textContent = `“${p}”` + (p === "你好星期五" ? "(推荐)" : p === "嘿星期五" ? "(说 hey 星期五 也行)" : p === "星期五" ? "(最容易误判)" : "");
        phrases.append(opt);
      }
    }
    if (!phrases.querySelector('option[value="__custom"]')) {
      const opt = document.createElement("option");
      opt.value = "__custom"; opt.textContent = "自定义…";
      phrases.append(opt);
    }
    const custom = !w.phrases.includes(w.phrase);
    phrases.value = custom ? "__custom" : w.phrase;
    phrases.querySelector('option[value="__custom"]').textContent = custom ? `“${w.phrase}”(自定义)` : "自定义…";
    showWakeCustom(custom, custom ? w.phrase : "");
    $("setWakeSensitivity").value = w.sensitivity;
    const text = w.state === "error" ? "⚠ " + w.error
      : w.state === "preparing" ? "正在准备(第一次要下载约 42MB 的语音模型)……"
      : w.state === "listening" ? `✓ 正在听“${w.phrase}”。识别在电脑本地进行,声音不会上传;叫醒之后说的话才会交给 Claude。`
      : `打开后麦克风会一直听“${w.phrase}”(只在电脑本地识别,不花钱)。常被误叫醒就换长一点的唤醒词,或把灵敏度调成“严格”。`;
    $("wakeNote").hidden = false;
    $("wakeNote").textContent = text;
    $("wakeNote").className = "set-note" + (w.state === "error" ? " warn" : "");
  }
  const h = s.hotkey;
  $("hotkeyRow").hidden = !h;
  if (h) {
    const select = $("setHotkey");
    if (!select.options.length) {
      for (const [key, label] of [...h.options, ["off", "不用快捷键"]]) {
        const opt = document.createElement("option");
        opt.value = key; opt.textContent = label;
        select.append(opt);
      }
    }
    select.value = h.value;
    $("hotkeyNote").hidden = !h.error;
    $("hotkeyNote").textContent = h.error ? "⚠ " + h.error : "";
    $("hotkeyNote").className = "set-note warn";
    $("askHotkeyRow").hidden = !h.ask_options;
    const askSelect = $("setAskHotkey");
    if (h.ask_options && !askSelect.options.length) {
      for (const [key, label] of [...h.ask_options, ["off", "不用"]]) {
        const opt = document.createElement("option");
        opt.value = key; opt.textContent = label;
        askSelect.append(opt);
      }
    }
    if (h.ask_value) askSelect.value = h.ask_value;
    $("askHotkeyNote").hidden = !h.ask_error;
    $("askHotkeyNote").textContent = h.ask_error ? "⚠ " + h.ask_error : "";
    $("askHotkeyNote").className = "set-note warn";
  }
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
  const hl = s.health;
  $("healthGroup").hidden = !hl;
  if (hl) {
    $("setHealth").checked = hl.enabled;
    const parts = (hl.disks || []).map((d) => `${d.drive} 剩 ${d.free_gb}GB${d.low ? " ⚠" : ""}`);
    if (hl.memory != null) parts.push(`内存用了 ${Math.round(hl.memory)}%`);
    if (hl.uptime_days != null) parts.push(`开机 ${hl.uptime_days} 天`);
    $("healthNote").textContent = parts.join(" · ");
    const dl = hl.downloads;
    $("downloadsNote").textContent = dl ? `下载文件夹:${dl.files} 个文件,${dl.gb}GB${dl.messy ? " ⚠ 有点乱" : ""}` : "下载文件夹";
  }
  const sec = s.security;
  $("securityGroup").hidden = !sec;
  if (sec) {
    const owners = $("discordOwners");
    owners.replaceChildren();
    $("discordPair").hidden = !sec.discord;
    for (const o of sec.discord || []) {
      const row = document.createElement("div");
      row.className = "set-row sub";
      const name = document.createElement("span");
      name.textContent = `${o.name || "(没有名字)"} · id ${o.id}`;
      const btn = document.createElement("button");
      btn.type = "button"; btn.className = "mini-btn"; btn.textContent = "移除";
      btn.onclick = () => { if (confirm(`移除“${o.name || o.id}”?它以后不能再用 Discord 控制星期五。`)) saveSetting({ discord_remove: o.id }, 5000); };
      row.append(name, btn);
      owners.append(row);
    }
    if (sec.discord && !sec.discord.length) owners.textContent = "还没有配对任何账号";
    $("pairNote").hidden = !sec.pair_code;
    if (sec.pair_code) $("pairNote").textContent = `配对码 ${sec.pair_code}:10 分钟内,用要添加的账号私信机器人发送。只能私信配对,频道里发无效。`;
    const list = $("securityEvents");
    list.replaceChildren();
    for (const e of sec.events || []) {
      const line = document.createElement("div");
      line.className = "sec-event " + (e.severity || "info");
      line.textContent = `${(e.time || "").slice(5, 16)}  ${e.text}`;
      list.append(line);
    }
    if (!(sec.events || []).length) list.textContent = "没有可疑的事 ✓";
    const notes = ["钥匙和密码文件星期五不读不碰", "网页、邮件里的指令不照做", "Discord 配对码猜错 5 次就拉黑"];
    notes.push(sec.vendor_local ? "光球的程序库已存在本机" : "光球的程序库还在从网站读取(下次打开会存到本机)");
    if (sec.blocked) notes.push(`已拉黑 ${sec.blocked} 个乱猜配对码的账号`);
    $("securityNote").textContent = "✓ " + notes.join(" · ");
  }
  $("rainRow").hidden = !s.rain_alert;
  if (s.rain_alert) $("setRain").value = s.rain_alert;
  $("weeklyRow").hidden = s.weekly_report === null || s.weekly_report === undefined;
  $("setWeekly").checked = !!s.weekly_report;
  $("quickRow").hidden = s.quick === null || s.quick === undefined;
  $("setQuick").checked = !!s.quick;
  $("calendarRow").hidden = s.remind_calendar === null || s.remind_calendar === undefined;
  $("setRemindCalendar").checked = !!s.remind_calendar;
  $("pushRow").hidden = !s.push;
  if (s.push) $("setPush").value = s.push;
  const care = s.care;
  $("careGroup").hidden = !care;
  if (care) {
    $("setCareWelcome").checked = care.welcome;
    $("setCareBreak").value = String(care.break_minutes);
    $("setCareNight").checked = care.night;
  }
  $("gameGroup").hidden = !care;
  if (care) {
    $("setGameQuiet").checked = care.game_quiet !== false;
    if (document.activeElement !== $("setGameClose")) $("setGameClose").value = (care.game_close || []).join(", ");
  }
  const c = s.cloud;
  $("cloudGroup").hidden = !c;
  if (c) {
    $("setCloud").value = "";
    $("setCloud").placeholder = c.configured ? "已配对(要换就贴新的)" : "FRIDAY-…";
    $("dropCloud").hidden = !c.configured;
    $("cloudNote").textContent = !c.configured
      ? "在云端服务器装好星期五后,会显示一串 FRIDAY- 开头的配对码,贴到这里。电脑开着时电脑回答,关机 3 分钟后云端接手手机 Discord 和提醒。"
      : c.connected ? `✓ 已连上云端(${c.host}),正在待命。电脑开着时由电脑回答。`
      : c.error ? "⚠ " + c.error : "正在连接云端……";
    $("cloudNote").className = "set-note" + (c.configured && !c.connected && c.error ? " warn" : "");
  }
  const k = s.backup;
  $("backupGroup").hidden = !k;
  if (k) {
    $("setBackup").checked = k.enabled;
    $("backupLast").textContent = k.last ? new Date(k.last * 1000).toLocaleString("zh-CN", { hour12: false }).slice(0, -3) : "还没有备份过";
    $("backupLast").className = "google-state " + (k.last && Date.now() / 1000 - k.last < 8 * 86400 ? "ok" : "warn");
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
$("setWake").onchange = (e) => saveSetting({ wake: e.target.checked }, 4000);
function showWakeCustom(show, value) {
  $("setWakeCustom").hidden = $("saveWakeCustom").hidden = !show;
  if (value !== undefined) $("setWakeCustom").value = value;
}
$("setWakePhrase").onchange = (e) => {
  if (e.target.value === "__custom") { showWakeCustom(true); $("setWakeCustom").focus(); return; }
  showWakeCustom(false);
  saveSetting({ wake_phrase: e.target.value });
};
$("saveWakeCustom").onclick = async () => {
  const phrase = $("setWakeCustom").value.trim();
  if (!phrase) { toast("先打你想用的唤醒词,例如“小五在吗”"); return; }
  if (!(await saveSetting({ wake_phrase: phrase }, 4000))) {  // 不合格:留着输入框给你改
    $("setWakePhrase").value = "__custom";
    showWakeCustom(true, phrase);
  }
};
$("setWakeCustom").onkeydown = (e) => { if (e.key === "Enter") $("saveWakeCustom").click(); };
$("setWakeSensitivity").onchange = (e) => saveSetting({ wake_sensitivity: e.target.value });
$("setHotkey").onchange = (e) => saveSetting({ hotkey: e.target.value });
$("setAskHotkey").onchange = (e) => saveSetting({ ask_hotkey: e.target.value });
$("setPush").onchange = (e) => saveSetting({ push: e.target.value });
$("setCareWelcome").onchange = (e) => saveSetting({ care_welcome: e.target.checked });
$("setCareBreak").onchange = (e) => saveSetting({ care_break: Number(e.target.value) });
$("setCareNight").onchange = (e) => saveSetting({ care_night: e.target.checked });
$("setHealth").onchange = (e) => saveSetting({ health: e.target.checked });
$("tidyNow").onclick = () => { if (confirm("把下载文件夹最上层的文件按类型分进子文件夹(图片、文档、安装程序……)?整理错了可以撤销。")) saveSetting({ tidy_downloads: true }, 6000); };
$("tidyUndo").onclick = () => saveSetting({ undo_tidy: true }, 5000);
$("discordPair").onclick = () => saveSetting({ discord_pair: true }, 8000);
$("setRain").onchange = (e) => saveSetting({ rain_alert: e.target.value });
$("setWeekly").onchange = (e) => saveSetting({ weekly_report: e.target.checked });
$("setQuick").onchange = (e) => saveSetting({ quick: e.target.checked });
$("setRemindCalendar").onchange = (e) => saveSetting({ remind_calendar: e.target.checked });
$("setGameQuiet").onchange = (e) => saveSetting({ game_quiet: e.target.checked });
$("saveGameClose").onclick = () => saveSetting({ game_close: $("setGameClose").value });
$("setGameClose").addEventListener("keydown", (e) => { if (e.key === "Enter") $("saveGameClose").click(); });
$("gameCandidates").onclick = async () => {
  const btn = $("gameCandidates");
  btn.disabled = true; btn.textContent = "读取中…";
  const r = await api("/api/settings", { game_candidates: true }).catch(() => ({ error: "星期五没有响应" }));
  btn.disabled = false; btn.textContent = "列出程序";
  if (r.error) { toast(r.error, 4000); return; }
  const box = $("gameChips");
  box.replaceChildren();
  for (const p of r.candidates || []) {
    const chip = document.createElement("button");
    chip.type = "button"; chip.className = "chip";
    chip.textContent = `${p.name} ${p.mb >= 1024 ? (p.mb / 1024).toFixed(1) + "GB" : p.mb + "MB"}`;
    chip.onclick = () => {
      const names = $("setGameClose").value.split(/[,，、]/).map((n) => n.trim()).filter(Boolean);
      if (!names.some((n) => n.toLowerCase() === p.name.toLowerCase())) names.push(p.name);
      $("setGameClose").value = names.join(", ");
      chip.disabled = true;
    };
    box.append(chip);
  }
  box.hidden = !box.children.length;
  if (!box.children.length) toast("没有找到开着窗口的程序");
  else toast("点程序名加进去,再按保存", 3000);
};
$("saveCloud").onclick = async () => {
  const code = $("setCloud").value.trim();
  if (!code) { toast("先把配对码贴进来"); return; }
  if (await saveSetting({ cloud_code: code }, 4000)) setTimeout(() => loadState().then(fillSettings), 5000);
};
$("dropCloud").onclick = () => {
  if (confirm("断开后,电脑关机时就没有人接手手机 Discord 和提醒了。确定吗?")) saveSetting({ cloud_code: "" });
};
$("setBackup").onchange = (e) => saveSetting({ backup_auto: e.target.checked });
$("backupNow").onclick = () => saveSetting({ backup_now: true }, 4000);
$("backupRestore").onclick = () => {
  if (!confirm("要用 Google Drive 里最新的备份,换掉现在的记忆、提醒、待办和聊天记录吗?\n\n现在的资料会先在电脑上另存一份,星期五会自动重启。")) return;
  saveSetting({ backup_restore: true }, 5000);
};
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
  stopBargeWatch();
  if (audioTurn !== null) ignoreTurn = Math.max(ignoreTurn, audioTurn);
  audioQueue.length = 0;
  audioEl.pause();
  speaking = false; orbState.level = 0; refreshMode();
}
function playNext() {
  const url = audioQueue.shift();
  if (!url) { speaking = false; orbState.level = 0; stopBargeWatch(); refreshMode(); maybeFollowUp(); return; }
  ensureAudio();
  speaking = true; refreshMode();
  audioEl.src = `${url}?t=${encodeURIComponent(token)}`;
  audioEl.play().then(() => {
    startBargeWatch();
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
// followUp:回答完自动再听(没说话就安静地结束);stream/spoke:说话打断她时,沿用已经打开的麦克风
let recorder = null;
async function listen({ followUp = false, stream = null, spoke = false } = {}) {
  if (recorder) return;
  followUpTurn = null;
  ensureAudio();
  if (!spoke) stopSpeaking();
  if (!stream) {
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch {
      if (!followUp) toast("无法打开麦克风,请检查麦克风权限");
      return;
    }
  }
  const micAnalyser = audioCtx.createAnalyser();
  micAnalyser.fftSize = 1024;
  audioCtx.createMediaStreamSource(stream).connect(micAnalyser);
  const chunks = [];
  const type = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
  recorder = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
  recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  let quietSince = performance.now();
  const started = performance.now();
  const noSpeechLimit = followUp ? 6000 : 10000;
  const btn = $("micBtn");
  btn.classList.add("on");
  btn.classList.toggle("follow", followUp);
  beep(followUp ? 760 : 990, followUp ? 0.05 : 0.08);
  refreshMode();

  const watch = () => {
    const timer = setInterval(() => {
      if (!recorder) { clearInterval(timer); return; }
      const level = rms(micAnalyser);
      orbState.level = Math.min(1, level * 6);
      btn.style.setProperty("--mic", orbState.level.toFixed(2));
      const now = performance.now();
      if (level > 0.035) { spoke = true; quietSince = now; }
      if ((spoke && now - quietSince > 2000) || (!spoke && now - started > noSpeechLimit) || now - started > 60000) {
        clearInterval(timer);
        recorder.stop();
      }
    }, 30);
  };
  recorder.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    btn.classList.remove("on", "follow");
    btn.style.removeProperty("--mic");
    recorder = null;
    orbState.level = 0;
    refreshMode();
    if (!spoke) { if (!followUp) toast("没听到声音"); return; }
    addLine("识别中……");
    const r = await api("/api/voice", new Blob(chunks, { type: type || "audio/webm" }), { "X-Speak": speakOn ? "1" : "0" });
    if (!r.text) toast(r.error ? `语音识别失败:${r.error}` : "没听清,再说一次?", 3500);
  };
  recorder.start(250);
  watch();
}
$("micBtn").onclick = () => (recorder ? recorder.stop() : listen());

// ---------- 说话打断她:她在说话时你一开口,她就停下来听你说 ----------
let bargeOn = loadPrefs("friday-barge", true);
let bargeStream = null;
async function startBargeWatch() {
  if (!bargeOn || bargeStream || recorder || !audioCtx) return;
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  } catch { return; }
  if (!speaking || recorder || bargeStream) { stream.getTracks().forEach((t) => t.stop()); return; }
  bargeStream = stream;
  const an = audioCtx.createAnalyser();
  an.fftSize = 1024;
  audioCtx.createMediaStreamSource(stream).connect(an);
  const started = performance.now();
  let baseline = 0.008, loud = 0;
  // 用定时器而不是动画帧:窗口被挡住时动画帧会暂停,定时器还会继续
  const timer = setInterval(() => {
    if (bargeStream !== stream) { clearInterval(timer); return; }
    if (!speaking) { clearInterval(timer); stopBargeWatch(); return; }
    const level = rms(an);
    if (performance.now() - started < 400) {
      baseline = Math.max(baseline, level);  // 先量一下她自己的声音从喇叭漏回麦克风有多大
    } else if (level > Math.max(BARGE_MIN, baseline * BARGE_RATIO)) {
      if (++loud >= BARGE_FRAMES) {  // 大约 0.2 秒都比她的声音大:是你在说话
        clearInterval(timer);
        bargeStream = null;
        stopSpeaking();
        listen({ stream, spoke: true });
      }
    } else {
      loud = Math.max(0, loud - 1);
    }
  }, 30);
}
// 门槛:开了回声消除和降噪后,正常说话的音量常常只有 0.03~0.1
const BARGE_MIN = 0.025, BARGE_RATIO = 1.8, BARGE_FRAMES = 6;
function stopBargeWatch() {
  if (bargeStream) { bargeStream.getTracks().forEach((t) => t.stop()); bargeStream = null; }
}

// ---------- 点光球:开始说话(再点一下结束) ----------
orbState.onOrbClick = () => (recorder ? recorder.stop() : listen());
$("cssOrb").addEventListener("click", () => orbState.onOrbClick());

// ---------- 连续对话:用语音问的,她说完后自动再听几秒,你可以直接接着说 ----------
let followUpOn = loadPrefs("friday-followup", true);
let followUpTurn = null;  // 等这一轮说完
let audioEndTurns = new Set();
function maybeFollowUp() {
  if (!followUpOn || followUpTurn === null || busy || recorder || speaking || audioQueue.length) return;
  if (followUpTurn !== "none" && speakOn && !audioEndTurns.has(followUpTurn)) return;  // 还有话没送来
  followUpTurn = null;
  setTimeout(() => { if (!busy && !recorder && !speaking) listen({ followUp: true }); }, 350);
}

// ---------- 授权弹窗 ----------
let approvalId = null;
function showApproval(ev) {
  approvalId = ev.id;
  $("approvalText").textContent = ev.text;
  $("approveAll").hidden = !!ev.always;
  $("approvalNote").textContent = ev.always ? "这一步每次都要确认。" : "5 分钟内不处理会自动拒绝。";
  $("approval").hidden = false;
  beep(520, 0.2);
  $("approveNo").focus(); // 默认停在“拒绝”:正在打字时按到 Enter / 空格,不会误点允许
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
