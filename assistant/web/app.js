// 星期五界面:实时对话(SSE)、语音输入输出、授权弹窗、左右面板。
const token = new URLSearchParams(location.search).get("t") || "";
const $ = (id) => document.getElementById(id);
const orbState = { mode: "idle", level: 0 };

// ---------- 3D 全息球(CDN 打不开时用 CSS 版本) ----------
import("./orb.js")
  .then((m) => m.start($("orb"), orbState))
  .catch(() => { $("cssOrb").classList.add("on"); $("orbTools").hidden = true; });

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

// 光球的样子、大小(齿轮设定 → 外观 / 光球旁边的按钮,记在本机)
const ORB_STYLES = [["classic", "经典"], ["reactor", "反应堆"], ["galaxy", "星系"], ["wave", "声波环"], ["atom", "原子"],
  ["dna", "双螺旋"], ["planet", "星球"], ["cube", "超立方体"], ["plasma", "等离子"]];
function setOrbStyle(name, announce = false) {
  if (!ORB_STYLES.some(([k]) => k === name)) name = "classic";
  orbState.style = name;
  if (orbState.setStyle) orbState.setStyle(name);
  $("setOrbStyle").value = name;
  try { localStorage.setItem("friday-orb-style", name); } catch {}
  if (announce) {
    const label = $("orbStyleName");
    label.textContent = ORB_STYLES.find(([k]) => k === name)[1];
    label.classList.add("show");
    clearTimeout(setOrbStyle.timer);
    setOrbStyle.timer = setTimeout(() => label.classList.remove("show"), 1400);
  }
}
try { orbState.style = localStorage.getItem("friday-orb-style") || "classic"; } catch { orbState.style = "classic"; }
try { orbState.zoom = Number(localStorage.getItem("friday-orb-zoom")) || 1; } catch { orbState.zoom = 1; }
orbState.onView = (zoom) => { try { localStorage.setItem("friday-orb-zoom", zoom.toFixed(2)); } catch {} };
$("setOrbStyle").value = orbState.style;
$("setOrbStyle").addEventListener("change", (e) => setOrbStyle(e.target.value));
// 🎨:打开样子列表,点一个就换(Esc 或点外面关掉)
function buildOrbMenu() {
  const menu = $("orbMenu");
  menu.replaceChildren(...ORB_STYLES.map(([key, name]) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "orb-pick" + (key === orbState.style ? " on" : "");
    b.textContent = name; b.setAttribute("aria-pressed", String(key === orbState.style));
    b.onclick = (e) => { e.stopPropagation(); setOrbStyle(key, true); orbState.pulse && orbState.pulse(0.8); buildOrbMenu(); };
    return b;
  }));
}
$("orbStyleBtn").onclick = (e) => {
  e.stopPropagation();
  const menu = $("orbMenu");
  menu.hidden = !menu.hidden;
  if (!menu.hidden) buildOrbMenu();
};
document.addEventListener("click", (e) => { if (!$("orbMenu").hidden && !$("orbMenu").contains(e.target)) $("orbMenu").hidden = true; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("orbMenu").hidden = true; });

// 自动换样子:每 10 分钟 / 每小时 / 每天换一个(齿轮设定 → 外观)
let orbAuto = loadPrefs("friday-orb-auto", "off");
function orbAutoTick() {
  const every = { "10m": 10 * 60e3, "1h": 3600e3, "1d": 86400e3 }[orbAuto];
  if (!every) return;
  const slot = Math.floor((Date.now() - new Date().getTimezoneOffset() * 60e3) / every);
  const name = ORB_STYLES[slot % ORB_STYLES.length][0];
  if (name !== orbState.style) setOrbStyle(name, true);
}
$("setOrbAuto").value = orbAuto;
$("setOrbAuto").onchange = (e) => { orbAuto = e.target.value; savePrefs("friday-orb-auto", orbAuto); orbAutoTick(); };
setInterval(orbAutoTick, 30e3);
setTimeout(orbAutoTick, 1500);
$("orbZoomIn").onclick = () => orbState.zoomBy && orbState.zoomBy(1.15);
$("orbZoomOut").onclick = () => orbState.zoomBy && orbState.zoomBy(1 / 1.15);
$("orbReset").onclick = () => orbState.resetView && orbState.resetView();

// 颜色主题(齿轮设定 → 外观,记在本机)
const THEMES = {
  cyan: { idle: "#22d3ee", thinking: "#3b82f6", speaking: "#2bc4e6", listening: "#d946ef" },
  ironman: { idle: "#fbbf24", thinking: "#ef4444", speaking: "#f59e0b", listening: "#f97316" },
  violet: { idle: "#a78bfa", thinking: "#6366f1", speaking: "#c4b5fd", listening: "#f472b6" },
  matrix: { idle: "#34d399", thinking: "#10b981", speaking: "#6ee7b7", listening: "#a3e635" },
};
function setTheme(name) {
  if (!THEMES[name]) name = "cyan";
  if (name === "cyan") delete document.body.dataset.theme;
  else document.body.dataset.theme = name;
  orbState.palette = THEMES[name];
  $("setTheme").value = name;
}
let savedTheme = "cyan";
try { savedTheme = localStorage.getItem("friday-theme") || "cyan"; } catch {}
setTheme(savedTheme);
$("setTheme").addEventListener("change", (e) => {
  setTheme(e.target.value);
  try { localStorage.setItem("friday-theme", e.target.value); } catch {}
});

// ---------- 状态 ----------
const MODE_TEXT = { idle: "待机 STANDBY", thinking: "思考中 PROCESSING", speaking: "说话中 SPEAKING", listening: "聆听中 LISTENING" };
let busy = false;
function setMode(mode) {
  const before = orbState.mode;
  if (orbState.pulse && before !== mode && (mode === "speaking" || (before === "speaking" && mode === "idle"))) orbState.pulse(mode === "speaking" ? 1 : 0.7);
  orbState.mode = mode;
  document.body.dataset.mode = mode;
  $("modeText").textContent = MODE_TEXT[mode] || mode;
  syncMini();
}

// ---------- 迷你悬浮窗:一个小光球浮在所有窗口最上面(浏览器的“画中画”窗口) ----------
let mini = null;
const MINI_TEXT = { idle: "待机", thinking: "思考中", speaking: "说话中", listening: "在听" };
const MINI_CSS = `
  html, body { height: 100%; }
  body { margin: 0; display: flex; align-items: center; justify-content: center; gap: clamp(10px, 4vw, 28px);
         padding: 8px 14px; box-sizing: border-box; overflow: hidden; user-select: none; color: #e6f6ff;
         font: 13px "Microsoft YaHei", system-ui, sans-serif;
         background: radial-gradient(ellipse at 30% 50%, var(--bg2, #061227) 0%, #02050c 70%); }
  .orb { flex: none; width: clamp(44px, 62vh, 220px); aspect-ratio: 1; border-radius: 50%; cursor: pointer; border: none; padding: 0;
         background: radial-gradient(circle at 35% 30%, #fff 0%, var(--c) 35%, transparent 72%);
         box-shadow: 0 0 calc(18px + 4vh) var(--c), inset 0 0 14px var(--c); animation: breathe 3s ease-in-out infinite; }
  body[data-mode="listening"] .orb { animation-duration: .7s; }
  body[data-mode="speaking"] .orb { animation-duration: 1.1s; }
  body[data-mode="thinking"] .orb { animation: spin 1.2s linear infinite; }
  @keyframes breathe { 50% { transform: scale(.9); opacity: .82; } }
  @keyframes spin { to { transform: rotate(360deg); } }
  .info { min-width: 0; max-width: min(60vw, 520px); }
  .mode { font-weight: 600; color: var(--c); letter-spacing: .05em; font-size: clamp(13px, 7vh, 26px); }
  .chips { font-size: clamp(12px, 5vh, 18px); opacity: .9; margin-top: 2px; }
  .last { font-size: clamp(11px, 4.5vh, 16px); opacity: .7; margin-top: 4px; line-height: 1.35;
          display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  @media (max-aspect-ratio: 1/1) { body { flex-direction: column; text-align: center; } .info { max-width: 90vw; } }`;
function miniColor() {
  return getComputedStyle(document.body).getPropertyValue("--accent").trim() || "#22d3ee";
}
function syncMini() {
  if (!mini || mini.closed) { mini = null; return; }
  const doc = mini.document;
  const mode = orbState.mode || "idle";
  doc.body.dataset.mode = mode;
  doc.body.style.setProperty("--c", miniColor());
  doc.body.style.setProperty("--bg2", getComputedStyle(document.body).getPropertyValue("--bg2").trim() || "#061227");
  doc.getElementById("miniMode").textContent = "星期五 · " + (MINI_TEXT[mode] || mode);
  const chips = [$("focusChip"), $("sleepChip")].filter((c) => !c.hidden).map((c) => c.textContent);
  doc.getElementById("miniChips").textContent = chips.join("  ");
}
function miniLast(text) {
  if (!mini || mini.closed) return;
  mini.document.getElementById("miniLast").textContent = (text || "").replace(/\s+/g, " ").slice(0, 80);
}
async function openMini() {
  if (mini && !mini.closed) { mini.close(); mini = null; return; }
  if (!("documentPictureInPicture" in window)) { toast("这个浏览器不支持悬浮窗,用最新的 Edge 或 Chrome 打开星期五就有", 4000); return; }
  try {
    // preferInitialWindowPlacement:每次都用这个小尺寸打开(不然浏览器会照上次拉大的尺寸开)
    mini = await documentPictureInPicture.requestWindow({ width: 300, height: 110, preferInitialWindowPlacement: true });
  } catch { toast("悬浮窗没打开"); return; }
  const doc = mini.document;
  doc.title = "星期五";
  const style = doc.createElement("style");
  style.textContent = MINI_CSS;
  doc.head.append(style);
  doc.body.innerHTML = '<button class="orb" id="miniOrb" title="点一下说话;再点一下停"></button>' +
    '<div class="info"><div class="mode" id="miniMode"></div><div class="chips" id="miniChips"></div><div class="last" id="miniLast"></div></div>';
  doc.getElementById("miniOrb").onclick = () => orbState.onOrbClick && orbState.onOrbClick();
  mini.addEventListener("pagehide", () => { mini = null; });
  syncMini();
  if (!miniTimer) miniTimer = setInterval(syncMini, 1000);  // 倒计时每秒更新
}
let miniTimer = null;
// ⧉:Windows 上开关“桌面小光球”(只有光球、背景透明);别的系统用浏览器的小窗口
$("miniBtn").onclick = () => {
  const orb = settingsState && settingsState.floatorb;
  if (orb) saveSetting({ floatorb: !orb.on }, 5000);
  else openMini();
};
let lastActivity = "";
let bargeStream = null;  // 读回答时等你开口打断用的麦克风
// 桌面小光球开着时,把声音大小告诉星期五(大约每秒 8 次),小光球跟着跳
let levelSentAt = 0, levelSent = -1;
function sendLevel(level) {
  if (!(settingsState && settingsState.floatorb && settingsState.floatorb.on)) return;
  const now = performance.now();
  if (now - levelSentAt < 120 || Math.abs(level - levelSent) < 0.04) return;
  levelSentAt = now; levelSent = level;
  api("/api/activity", { level }).catch(() => {});
}
function refreshMode() {
  // 告诉星期五窗口正在录音/朗读,唤醒功能这时暂停,免得听到自己的声音
  const accent = getComputedStyle(document.body).getPropertyValue("--cyan").trim();  // 桌面小光球跟着主题变色
  // mic:窗口自己在用麦克风(录音或等你开口打断)——不要当成你在通话
  const activity = JSON.stringify({ listening: !!recorder, speaking: !!speaking, mic: !!(recorder || bargeStream), accent });
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
// 对话里按日期分隔(“今天”“昨天”“11月10日 星期一”)
let lastDay = "";
function dayKey(ts) { const d = new Date(ts * 1000); return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`; }
function dayLabel(date) {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const day = new Date(date); day.setHours(0, 0, 0, 0);
  const diff = Math.round((today - day) / 86400000);
  if (diff === 0) return "今天";
  if (diff === 1) return "昨天";
  if (diff === 2) return "前天";
  const year = day.getFullYear() === today.getFullYear() ? "" : `${day.getFullYear()}年`;
  return `${year}${day.getMonth() + 1}月${day.getDate()}日 星期${WEEK[day.getDay()]}`;
}
function maybeDayDivider(ts) {
  if (!ts) return;
  const key = dayKey(ts);
  if (key === lastDay) return;
  const hadDay = lastDay !== "";
  lastDay = key;
  if (!hadDay && key === dayKey(Date.now() / 1000)) return;  // 全都是今天的:不用分隔
  const div = document.createElement("div");
  div.className = "day-divider";
  div.textContent = dayLabel(ts * 1000);
  log.append(div);
}
// 复制:窗口是本机网页,剪贴板一般能用;不能用时退回老方法
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch {}
  const area = document.createElement("textarea");
  area.value = text; area.style.position = "fixed"; area.style.opacity = "0";
  document.body.append(area); area.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch {}
  area.remove();
  return ok;
}
const LONG_ANSWER = 900;
function decorateAnswer(el, text) {
  el.querySelector(".msg-tools")?.remove();
  el.querySelector(".msg-more")?.remove();
  el.classList.remove("long", "open");
  if (!text) return;
  const tools = document.createElement("div");
  tools.className = "msg-tools";
  const copy = document.createElement("button");
  copy.type = "button"; copy.className = "msg-copy"; copy.title = "复制这个回答"; copy.textContent = "复制";
  copy.onclick = async () => { copy.textContent = (await copyText(text)) ? "已复制 ✓" : "复制失败"; setTimeout(() => { copy.textContent = "复制"; }, 1600); };
  tools.append(copy);
  el.append(tools);
  if (text.length > LONG_ANSWER || text.split("\n").length > 22) {
    el.classList.add("long");
    const more = document.createElement("button");
    more.type = "button"; more.className = "msg-more"; more.textContent = "展开全部 ▾";
    more.onclick = () => { const open = el.classList.toggle("open"); more.textContent = open ? "收起 ▴" : "展开全部 ▾"; };
    el.append(more);
  }
}
// 消息从哪里来(窗口打字的不标)
const SOURCE_LABELS = { discord: "📱 Discord", "discord-voice": "📱 Discord 语音", telegram: "✈ Telegram", voice: "🎙 语音",
  ptt: "🎙 按住说话", task: "⏱ 后台任务" };
function hhmm(ts) {
  const d = new Date((ts || Date.now() / 1000) * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
function addMessage(kind, text, source, ts) {
  maybeDayDivider(ts);
  const el = document.createElement("div");
  el.className = `msg ${kind}${source && source !== "web" ? " source-" + source : ""}`;
  const who = document.createElement("span");
  who.className = "who";
  const name = document.createElement("span");
  name.className = "who-name";
  name.textContent = kind === "user" ? "YOU" : "FRIDAY";
  who.append(name);
  if (source && source !== "web") {
    const tag = document.createElement("span");
    tag.className = "who-src";
    tag.textContent = SOURCE_LABELS[source] || source;
    who.append(tag);
  }
  const time = document.createElement("span");
  time.className = "who-time";
  time.textContent = hhmm(ts);
  who.append(time);
  const body = document.createElement("span");
  body.className = "text";
  if (kind === "friday" && text) setRich(body, text);
  else body.textContent = text;
  el.append(who, body);
  log.append(el);
  scrollDown();
  return el;
}

// ---------- 回答排版:分段、列表、粗体、代码、表格(只用安全的 DOM,不塞 HTML) ----------
function inlineRich(parent, text) {
  const re = /(\*\*[^*\n]+\*\*|`[^`\n]+`)/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) parent.append(text.slice(last, m.index));
    const bold = m[0].startsWith("**");
    const el = document.createElement(bold ? "strong" : "code");
    el.textContent = bold ? m[0].slice(2, -2) : m[0].slice(1, -1);
    parent.append(el);
    last = m.index + m[0].length;
  }
  if (last < text.length) parent.append(text.slice(last));
}
const LIST_ITEM = /^(\s*)(?:([-*•·])\s+|(\d{1,2})(?:[.)]\s+|、\s*))(.+)$/;
function renderRich(text) {
  const frag = document.createDocumentFragment();
  const lines = String(text || "").replace(/\r/g, "").split("\n");
  let para = null, list = null, listKind = "", code = null, table = null;
  const close = () => { para = null; list = null; listKind = ""; table = null; };
  for (const raw of lines) {
    if (code) {
      if (/^\s*```/.test(raw)) { code = null; continue; }
      code.textContent += (code.dataset.used ? "\n" : "") + raw;
      code.dataset.used = "1";
      continue;
    }
    if (/^\s*```/.test(raw)) {
      close();
      const pre = document.createElement("pre");
      pre.className = "md-code";
      code = document.createElement("code");
      pre.append(code);
      frag.append(pre);
      continue;
    }
    const line = raw.trimEnd();
    if (!line.trim()) { close(); continue; }
    let m;
    if ((m = line.match(/^\s{0,3}(#{1,4})\s+(.+)$/))) {
      close();
      const h = document.createElement("div");
      h.className = `md-h md-h${m[1].length}`;
      inlineRich(h, m[2].replace(/\s+#+$/, ""));
      frag.append(h);
      continue;
    }
    if (/^\s*\|.*\|$/.test(line)) {
      if (/^\s*\|[\s:|-]+\|$/.test(line) && line.includes("-")) continue;  // 表头下面那条分隔线
      if (!table) {
        close();
        table = document.createElement("table");
        table.className = "md-table";
        frag.append(table);
      }
      const tr = document.createElement("tr");
      for (const cell of line.trim().slice(1, -1).split("|")) {
        const td = document.createElement(table.rows.length ? "td" : "th");
        inlineRich(td, cell.trim());
        tr.append(td);
      }
      table.append(tr);
      continue;
    }
    if (/^\s*([-*_])(\s*\1){2,}$/.test(line)) {
      close();
      const hr = document.createElement("hr");
      hr.className = "md-hr";
      frag.append(hr);
      continue;
    }
    if ((m = line.match(LIST_ITEM))) {
      const kind = m[3] ? "ol" : "ul";
      if (!list || listKind !== kind) {
        close();
        list = document.createElement(kind);
        list.className = "md-list";
        listKind = kind;
        if (kind === "ol" && Number(m[3]) > 1) list.start = Number(m[3]);
        frag.append(list);
      }
      const li = document.createElement("li");
      if (m[1].length >= 2) li.className = "md-sub";
      inlineRich(li, m[4]);
      list.append(li);
      continue;
    }
    if (list && /^\s{2,}\S/.test(raw) && list.lastElementChild) {  // 上一点的续行
      list.lastElementChild.append(document.createElement("br"));
      inlineRich(list.lastElementChild, line.trim());
      continue;
    }
    if (!para) {
      close();
      para = document.createElement("p");
      para.className = "md-p";
      frag.append(para);
    } else {
      para.append(document.createElement("br"));
    }
    inlineRich(para, line);
  }
  return frag;
}
function setRich(body, text) {
  body.classList.add("rich");
  body.replaceChildren(renderRich(text));
}

// ---------- 一次回答里用了哪些工具:收成一行,点开才看 ----------
let turnOpen = false;    // 问了、还没答完
let steps = null;        // 这一轮的步骤
let heldNotices = [];    // 回答到一半来的提醒:答完再放,不插在问答中间
let heldTimer = null;
function placeSteps() {
  if (!steps) return;
  if (current) current.insertBefore(steps, current.querySelector(".text"));
  else if (!steps.isConnected) log.append(steps);
}
function stepsSummary(items, running) {
  if (running) return items[items.length - 1] + (items.length > 1 ? `(第 ${items.length} 步)` : "");
  return items.length === 1 ? items[0] : `${items.length} 个步骤:${items.join(" → ")}`;
}
function addStep(text) {
  if (!steps) {
    steps = document.createElement("details");
    steps.className = "msg-steps running";
    steps.append(document.createElement("summary"), document.createElement("ol"));
    placeSteps();
  }
  const item = document.createElement("li");
  item.textContent = text;
  steps.querySelector("ol").append(item);
  const items = [...steps.querySelectorAll("li")].map((el) => el.textContent);
  steps.querySelector("summary").textContent = stepsSummary(items, true);
  scrollDown();
}
function closeSteps() {
  if (!steps) return;
  const items = [...steps.querySelectorAll("li")].map((el) => el.textContent);
  steps.classList.remove("running");
  steps.querySelector("summary").textContent = stepsSummary(items, false);
  if (items.length === 1) steps.classList.add("single");
  steps = null;
}
function endTurn() {
  turnOpen = false;
  closeSteps();
  clearTimeout(heldTimer);
  const list = heldNotices;
  heldNotices = [];
  for (const ev of list) showNotice(ev);
}

// ---------- 不在问答里的小状态(游戏、备份、更新……):连着的收成一组,只露最近两条 ----------
function addBackground(text) {
  if (/^——.*——$/.test(text.trim())) {
    const div = document.createElement("div");
    div.className = "day-divider restart";
    div.textContent = text.replace(/——/g, "").trim();
    log.append(div);
    scrollDown();
    return;
  }
  const lastEl = log.lastElementChild;
  let group = lastEl && lastEl.classList.contains("bg-group") ? lastEl : null;
  if (!group) {
    group = document.createElement("div");
    group.className = "bg-group";
    log.append(group);
  }
  const el = document.createElement("div");
  el.className = "status-line";
  el.textContent = text;
  group.append(el);
  const count = group.querySelectorAll(".status-line").length;
  if (count > 2) {
    let more = group.querySelector(".bg-more");
    if (!more) {
      more = document.createElement("button");
      more.type = "button";
      more.className = "bg-more";
      more.onclick = () => { group.classList.toggle("folded"); foldLabel(group); };
      group.prepend(more);
      group.classList.add("folded");
    }
    foldLabel(group);
  }
  scrollDown();
}
function foldLabel(group) {
  const hidden = group.querySelectorAll(".status-line").length - 2;
  group.querySelector(".bg-more").textContent = group.classList.contains("folded") ? `还有 ${hidden} 条 ▸` : "收起 ▴";
}

// ---------- 星期五主动说的事(提醒、报告、警告):独立的小卡片 ----------
function showNotice(ev) {
  if (turnOpen) {  // 正在问答:先压着,答完再放;太久(例如跑代码)就先放在回答上面
    heldNotices.push(ev);
    clearTimeout(heldTimer);
    heldTimer = setTimeout(() => {
      const list = heldNotices;
      heldNotices = [];
      for (const held of list) placeNotice(held, true);
    }, 45000);
    return;
  }
  placeNotice(ev, false);
}
function placeNotice(ev, beforeAnswer) {
  if (!beforeAnswer || !current) maybeDayDivider(ev.ts);
  const card = document.createElement("div");
  card.className = "notice-card";
  const time = document.createElement("span");
  time.className = "notice-time";
  time.textContent = hhmm(ev.ts);
  const body = document.createElement("div");
  body.className = "notice-text rich";
  body.append(renderRich(ev.text));
  card.append(time, body);
  if (String(ev.text).length > LONG_ANSWER || String(ev.text).split("\n").length > 10) {
    card.classList.add("long");
    const more = document.createElement("button");
    more.type = "button"; more.className = "msg-more"; more.textContent = "展开全部 ▾";
    more.onclick = () => { const open = card.classList.toggle("open"); more.textContent = open ? "收起 ▴" : "展开全部 ▾"; };
    card.append(more);
  }
  if (beforeAnswer && current) log.insertBefore(card, current); else log.append(card);
  scrollDown();
}
function addLine(text, cls = "") {
  const el = document.createElement("div");
  el.className = `status-line ${cls}`;
  el.textContent = text;
  if (current) log.insertBefore(el, current); else log.append(el);
  scrollDown();
  return el;
}
// 找到的照片:缩图排一排,点一下用 Windows 的相片程序打开
function addPhotos(ev) {
  const box = document.createElement("div");
  box.className = "photo-grid";
  const head = document.createElement("div");
  head.className = "photo-head";
  head.textContent = `🖼 ${ev.title || "照片"} · ${ev.total} 张` + (ev.total > ev.items.length ? `(先列 ${ev.items.length} 张)` : "");
  box.append(head);
  for (const item of ev.items || []) {
    const btn = document.createElement("button");
    btn.type = "button"; btn.className = "photo-cell";
    btn.title = `${item.time}${item.place ? " · " + item.place : ""}\n${item.name}`;
    const img = document.createElement("img");
    img.loading = "lazy"; img.alt = item.name;
    img.src = `/api/photo_thumb?id=${encodeURIComponent(item.id)}&t=${encodeURIComponent(token)}`;
    img.onerror = () => { img.replaceWith(Object.assign(document.createElement("span"), { textContent: item.name })); };
    const cap = document.createElement("small");
    cap.textContent = item.time.slice(0, 10);
    btn.append(img, cap);
    btn.onclick = async () => {
      const r = await api("/api/photo_open", { id: item.id }).catch(() => ({ error: "星期五没有响应" }));
      if (r.error) addLine("⚠ " + r.error, "error");
    };
    box.append(btn);
  }
  if (current) log.insertBefore(box, current); else log.append(box);
  scrollDown();
}
// 代码实验室的实验窗口:代码、边跑边看输出、结果、跑出来的图和文件
const labCards = new Map();
function labCard(ev) {
  if (ev.phase === "install") {
    addLine(`🧪 正在安装 Python 库:${(ev.packages || []).join("、")}……`);
    return;
  }
  let card = labCards.get(ev.run);
  if (ev.phase === "start" || !card) {
    card = document.createElement("div");
    card.className = "lab-card running";
    const head = document.createElement("div");
    head.className = "lab-head";
    const title = document.createElement("span");
    title.className = "lab-title";
    title.textContent = `🧪 ${ev.title || "代码实验室"}`;
    const attempt = document.createElement("span");
    attempt.className = "lab-attempt";
    attempt.textContent = ev.attempt > 1 ? `第 ${ev.attempt}/${ev.max} 次` : "";
    const state = document.createElement("span");
    state.className = "lab-state";
    state.textContent = "运行中…";
    head.append(title, attempt, state);
    const code = document.createElement("details");
    code.className = "lab-code";
    const summary = document.createElement("summary");
    const lines = (ev.code || "").split("\n").length;
    summary.textContent = ev.kind === "self_test" ? `改了什么(${lines} 行;- 原来,+ 改成)`
      : `代码(${lines} 行)` + (ev.files && ev.files.length ? ` · 用了 ${ev.files.join("、")}` : "");
    if (ev.kind === "self_test") { title.textContent = `🧪 自我测试:${ev.title || ""}`; code.open = true; }
    const pre = document.createElement("pre");
    pre.textContent = ev.code || "";
    code.append(summary, pre);
    const out = document.createElement("pre");
    out.className = "lab-out";
    out.hidden = true;
    const files = document.createElement("div");
    files.className = "lab-files";
    card.append(head, code, out, files);
    if (current) log.insertBefore(card, current); else log.append(card);
    labCards.set(ev.run, card);
    if (labCards.size > 40) labCards.delete(labCards.keys().next().value);
    scrollDown();
    if (ev.phase === "start") return;
  }
  const out = card.querySelector(".lab-out");
  if (ev.phase === "out") {
    out.hidden = false;
    out.textContent = (out.textContent + ev.text).slice(-20000);
    out.scrollTop = out.scrollHeight;
    scrollDown();
    return;
  }
  if (ev.phase !== "end") return;
  card.classList.remove("running");
  card.classList.add(ev.ok ? (ev.uncertain ? "unsure" : "ok") : "fail");
  card.querySelector(".lab-state").textContent = ev.kind === "self_test"
    ? (ev.ok ? (ev.uncertain ? `⚠ 没法完全确认 · ${ev.seconds} 秒` : `✅ 通过 · ${ev.seconds} 秒`) : `❌ 没通过 · ${ev.seconds} 秒`)
    : (ev.ok ? `✅ 成功 · ${ev.seconds} 秒` : `❌ 失败 · ${ev.seconds} 秒`);
  if (!ev.ok && ev.error) {
    out.hidden = false;
    const err = document.createElement("span");
    err.className = "lab-err";
    err.textContent = (out.textContent ? "\n" : "") + ev.error.trim().split("\n").slice(-12).join("\n");
    out.append(err);
  }
  if (ev.summary) {
    const note = document.createElement("div");
    note.className = "lab-note" + (ev.ok && !ev.uncertain ? " good" : "");
    note.textContent = ev.summary + ((ev.failed || []).length
      ? (ev.uncertain ? "\n测不到的测试:" : "\n被改坏的测试:") + ev.failed.join("、") : "");
    card.append(note);
  } else if (!ev.ok) {
    const note = document.createElement("div");
    note.className = "lab-note";
    note.textContent = ev.installable ? `缺 ${ev.missing}:星期五会问你要不要装 ${ev.installable}`
      : ev.missing ? `缺 ${ev.missing},这个库不在可以装的名单里,星期五会换个写法`
      : ev.attempt >= ev.max ? "已经试了 3 次,星期五会告诉你卡在哪里" : "星期五会看错误、改好再试";
    card.append(note);
  }
  const files = card.querySelector(".lab-files");
  for (const f of ev.produced || []) {
    const item = document.createElement("div");
    item.className = "lab-file";
    if (f.image) {
      const img = document.createElement("img");
      img.alt = f.name; img.loading = "lazy";
      img.src = `/api/lab_file?run=${encodeURIComponent(f.run || ev.run)}&name=${encodeURIComponent(f.name)}&t=${encodeURIComponent(token)}`;
      img.onload = scrollDown;
      item.append(img);
    }
    const row = document.createElement("div");
    row.className = "lab-file-row";
    const label = document.createElement("span");
    label.textContent = "📄 " + f.name;
    row.append(label);
    if (f.openable) {
      for (const [how, text] of [["open", "打开"], ["save", "存到下载"]]) {
        const b = document.createElement("button");
        b.type = "button"; b.className = "mini-btn"; b.textContent = text;
        b.onclick = async () => {
          const r = await api("/api/lab_file", { run: f.run || ev.run, name: f.name, how }).catch(() => ({ error: "星期五没有响应" }));
          addLine(r.error ? "⚠ " + r.error : "🧪 " + r.message, r.error ? "error" : "");
        };
        row.append(b);
      }
    }
    item.append(row);
    files.append(item);
  }
  scrollDown();
}
function startAnswer(source, ts) {
  current = addMessage("friday", "", source, ts);
  current.classList.add("typing");
  current.querySelector(".text").innerHTML = '<span class="thinking"><b></b><b></b><b></b></span>';
  current.dataset.empty = "1";
  current.raw = "";
  placeSteps();
}
let drawPending = false;
function appendDelta(text) {
  if (!current) startAnswer();
  if (current.dataset.empty) { current.raw = ""; delete current.dataset.empty; }
  current.raw = (current.raw || "") + text;
  if (drawPending) return;
  drawPending = true;
  requestAnimationFrame(() => {  // 一帧排一次版,字很多也不卡
    drawPending = false;
    if (current && !current.dataset.empty) { setRich(current.querySelector(".text"), current.raw); scrollDown(); }
  });
}
function finishAnswer(text, source, ts) {
  if (!current) current = addMessage("friday", "", source, ts);
  current.classList.remove("typing");
  placeSteps();
  closeSteps();
  setRich(current.querySelector(".text"), text);
  delete current.dataset.empty;
  decorateAnswer(current, text);
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
      closeSteps();
      addMessage("user", ev.text, ev.source, ev.ts);
      turnOpen = true;
      break;
    case "thinking":
      busy = true; turnOpen = true; refreshMode(); startAnswer(ev.source, ev.ts); break;
    case "delta":
      appendDelta(ev.text); break;
    case "status":
      if (turnOpen) addStep(ev.text); else addBackground(ev.text);
      break;
    case "done":
      busy = false; finishAnswer(ev.text, ev.source, ev.ts); endTurn(); refreshMode(); loadState(); miniLast(ev.text);
      if (ev.source === "voice") { followUpTurn = ev.turn ?? "none"; maybeFollowUp(); }
      break;
    case "snip":
      showSnip(ev); break;
    case "photos":
      addPhotos(ev); break;
    case "lab":
      labCard(ev); break;
    case "metronome":
      metronome(ev); break;
    case "audio_end":
      audioEndTurns.add(ev.turn);
      maybeFollowUp(); break;
    case "error":
      busy = false;
      if (current) {
        if (steps && steps.parentElement === current) log.insertBefore(steps, current);
        current.remove(); current = null;
      }
      addLine(ev.text, "error"); endTurn(); refreshMode(); break;
    case "audio":
      enqueueAudio(ev.url, ev.turn); break;
    case "reminder":
      showNotice(ev);
      if (!ev.ts || Date.now() / 1000 - ev.ts < 30) beep(880, 0.15);  // 重新打开窗口补上来的旧提醒不响
      loadState(); break;
    case "focus":
      showFocus(ev); break;
    case "reader":
      readerEvent(ev); break;
    case "speech_rate":
      speechRate = ev.rate || 1;
      if (speaking && !readerPlaying) { audioEl.defaultPlaybackRate = speechRate; audioEl.playbackRate = speechRate; }
      break;
    case "audio_pause":
      if (speaking && !readerPlaying) { audioEl.pause(); audioPaused = true; speaking = false; stopBargeWatch(); refreshMode(); }
      break;
    case "audio_resume":
      resumeSpeaking(); break;
    case "stop_audio":
      audioPaused = false; stopSpeaking(); break;
    case "sleeptimer":
      showSleepTimer(ev); break;
    case "meeting":
      showMeeting(ev); break;
    case "startup":
    case "mailscan":
      loadState().then(() => fillSettings()); break;
    case "orb_click":  // 点了桌面小光球:开始听(在听就停)
      ensureAudio();
      if (orbState.onOrbClick) orbState.onOrbClick();
      break;
    case "floatorb":
      if (settingsState && settingsState.floatorb) settingsState.floatorb.on = ev.on;
      break;
    case "takeover":
      $("takeoverBar").hidden = !ev.active;
      if (ev.active) $("takeoverText").textContent = ev.text || "";
      else if (ev.text) addLine(`🖱 鼠标键盘交还给你了(${ev.text})`, "status");
      break;
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
      if (!busy && turnOpen) endTurn();  // 之前的对话补完了:没答完的那一轮(例如中途重启)不再等
      greet(); scrollDown();
      if (listenOnOpen) { listenOnOpen = false; setTimeout(startListening, 400); }
      break;
    case "listen":
      startListening(); break;
    case "locked":
      showLock(); break;
    case "cleared":
      log.innerHTML = ""; current = null; lastDay = ""; steps = null; heldNotices = []; turnOpen = false;
      addLine("已开始新话题"); break;
  }
  $("sendBtn").disabled = busy;
}

let source = null;
function connect() {
  if (source) source.close();
  source = new EventSource(`/events?t=${encodeURIComponent(token)}`);
  // 每次连上都会先收到之前的对话;先清空,断线重连时才不会重复
  source.onopen = () => { log.innerHTML = ""; current = null; lastDay = ""; steps = null; heldNotices = []; turnOpen = false; };
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
// 专注模式的倒计时(状态栏上的小标签)
let focusState = null, focusTimer = null;
function showFocus(f) {
  focusState = f && (f.until || f.break_until) ? f : null;
  clearInterval(focusTimer); focusTimer = null;
  renderFocus();
  if (focusState) focusTimer = setInterval(renderFocus, 1000);
}
function renderFocus() {
  const chip = $("focusChip");
  const end = focusState ? (focusState.until || focusState.break_until) : 0;
  const left = Math.max(0, Math.round(end - Date.now() / 1000));
  if (!focusState || !left) { chip.hidden = true; return; }
  const mm = String(Math.floor(left / 60)).padStart(2, "0"), ss = String(left % 60).padStart(2, "0");
  chip.hidden = false;
  chip.textContent = (focusState.until ? "🍅 专注 " : "☕ 休息 ") + `${mm}:${ss}`;
  chip.title = focusState.until ? (focusState.task || "专注中") + ",说“停止专注”可以提前结束" : "休息中";
}

// 睡前定时的倒计时(状态栏上的小标签)
let sleepState = null, sleepTicker = null;
function showSleepTimer(t) {
  sleepState = t && t.at ? t : null;
  clearInterval(sleepTicker); sleepTicker = null;
  renderSleepTimer();
  if (sleepState) sleepTicker = setInterval(renderSleepTimer, 1000);
}
function renderSleepTimer() {
  const chip = $("sleepChip");
  const left = sleepState ? Math.max(0, Math.round(sleepState.at - Date.now() / 1000)) : 0;
  if (!left) { chip.hidden = true; return; }
  const h = Math.floor(left / 3600), m = Math.floor((left % 3600) / 60), sec = left % 60;
  chip.hidden = false;
  chip.textContent = `🌙 ${sleepState.label} ` + (h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`);
  chip.title = "说“取消定时”就不停";
}

async function loadState() {
  const s = await api("/api/state");
  if (!s || s.error) return;
  if (s.locked) { showLock(); return s; }
  $("versionText").textContent = s.version || "未记录";
  const appFolder = (s.settings || {}).app_folder;
  if (appFolder) {
    $("appDirText").textContent = appFolder.path || "—";
    $("reviewCopyOpen").hidden = !appFolder.review_copy;
    if (appFolder.review_copy) $("reviewCopyNote").textContent = "上一份干净副本:" + appFolder.review_copy + "(把这个文件夹交给审查工具)";
  }
  if (s.settings) settingsState = s.settings;
  if (s.settings && "focus" in s.settings) showFocus(s.settings.focus);
  if (s.settings && "sleep_timer" in s.settings) showSleepTimer(s.settings.sleep_timer);
  if (s.settings && s.settings.meeting) showMeeting(s.settings.meeting);
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

// ---------- 附件:贴截图、拖文件、点回形针 ----------
const MAX_FILES = 4, MAX_FILE_MB = 20;
let pending = [];
function showAttachments() {
  const box = $("attachList");
  box.replaceChildren();
  pending.forEach((file, i) => {
    const chip = document.createElement("span");
    chip.className = "attach-chip";
    const size = file.size >= 1048576 ? (file.size / 1048576).toFixed(1) + "MB" : Math.max(1, Math.round(file.size / 1024)) + "KB";
    chip.textContent = `📎 ${file.name} · ${size}`;
    const x = document.createElement("button");
    x.type = "button"; x.textContent = "×"; x.title = "拿掉";
    x.onclick = () => { pending.splice(i, 1); showAttachments(); };
    chip.append(x);
    box.append(chip);
  });
  box.hidden = !pending.length;
}
function addFiles(list) {
  for (const file of list) {
    if (pending.length >= MAX_FILES) { toast(`一次最多 ${MAX_FILES} 个附件`); break; }
    if (file.size > MAX_FILE_MB * 1048576) { toast(`${file.name} 太大了(最多 ${MAX_FILE_MB}MB)`); continue; }
    const named = file.name ? file : new File([file], `截图_${new Date().toTimeString().slice(0, 8).replace(/:/g, "")}.png`, { type: file.type });
    pending.push(named);
  }
  showAttachments();
  $("input").focus();
}
$("attachBtn").onclick = () => $("attachInput").click();
$("snipBtn").onclick = () => api("/api/command", { command: "snip" }).catch(() => {});

// 框好的画面:挂在输入框上面,下一个问题带着它问;× 取消
function showSnip(ev) {
  const box = $("snipChip");
  box.replaceChildren();
  if (ev.clear) { box.hidden = true; return; }
  const chip = document.createElement("span");
  chip.className = "attach-chip snip-chip";
  if (ev.thumb) {
    const img = document.createElement("img");
    img.src = ev.thumb; img.alt = "框选的画面";
    chip.append(img);
  }
  chip.append(document.createTextNode("✂ 框选的画面 · 下一个问题会带着它"));
  const x = document.createElement("button");
  x.type = "button"; x.textContent = "×"; x.title = "不要这张";
  x.onclick = () => { box.hidden = true; api("/api/command", { command: "snip_cancel" }).catch(() => {}); };
  chip.append(x);
  box.append(chip);
  box.hidden = false;
  $("input").focus();
}
$("attachInput").onchange = (e) => { addFiles(e.target.files); e.target.value = ""; };
document.addEventListener("paste", (e) => {
  if (isLocked || !$("settings").hidden) return;
  const files = [...(e.clipboardData?.items || [])].filter((it) => it.kind === "file").map((it) => it.getAsFile()).filter(Boolean);
  if (files.length) { e.preventDefault(); addFiles(files); }
});
let dragDepth = 0;
window.addEventListener("dragenter", (e) => { if (isLocked || !e.dataTransfer?.types?.includes("Files")) return; e.preventDefault(); dragDepth++; $("dropZone").hidden = false; });
window.addEventListener("dragover", (e) => { if (e.dataTransfer?.types?.includes("Files")) e.preventDefault(); });
window.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("dropZone").hidden = true; } });
window.addEventListener("drop", (e) => {
  e.preventDefault(); dragDepth = 0; $("dropZone").hidden = true;
  if (!isLocked && e.dataTransfer?.files?.length) addFiles(e.dataTransfer.files);
});
async function uploadPending() {
  const ids = [];
  for (const file of pending) {
    const r = await api("/api/attach", file, { "X-File-Name": encodeURIComponent(file.name), "Content-Type": file.type || "application/octet-stream" })
      .catch(() => ({ error: "上传失败" }));
    if (r.error) { toast(`${file.name}:${r.error}`, 4000); return null; }
    ids.push(r.id);
  }
  return ids;
}

async function sendText(text) {
  if ((!text && !pending.length) || busy) return;
  ensureAudio();
  pauseSpeaking();
  followUpTurn = null;  // 打字问的,不用接着听
  removeChips();
  beep(660, 0.06);
  let attachments = [];
  if (pending.length) {
    const ids = await uploadPending();
    if (!ids) return;
    attachments = ids;
    pending = []; showAttachments();
  }
  const r = await api("/api/ask", { text, speak: speakOn, attachments });
  if (r.error) toast(r.error);
}
$("form").onsubmit = (e) => {
  e.preventDefault();
  const text = $("input").value.trim();
  if ((!text && !pending.length) || busy) return;
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
// ---------- 会议 / 上课记录:上面的红点计时、设定里的列表 ----------
let meetingTimer = null;
function showMeeting(m) {
  if (!m) return;
  const chip = $("meetingChip");
  clearInterval(meetingTimer);
  const pad = (n) => String(n).padStart(2, "0");
  if (m.active) {
    const started = Date.now() - (m.seconds || 0) * 1000;
    const tick = () => {
      const sec = Math.floor((Date.now() - started) / 1000);
      chip.textContent = `⏺ 记录中 ${pad(Math.floor(sec / 3600))}:${pad(Math.floor(sec / 60) % 60)}:${pad(sec % 60)}`;
    };
    tick();
    meetingTimer = setInterval(tick, 1000);
  } else if (m.finishing) {
    chip.textContent = "📝 整理总结中……";
  }
  chip.hidden = !m.active && !m.finishing;
  chip.disabled = !m.active;
  $("meetGroup").hidden = false;
  $("meetStart").hidden = !!(m.active || m.finishing);
  $("meetTitle").hidden = !!(m.active || m.finishing);
  $("meetStop").hidden = !m.active;
  $("meetNow").textContent = m.active ? `正在记录${m.title ? "“" + m.title + "”" : ""},已经转好 ${m.lines} 段文字。`
    : m.finishing ? "正在把最后一段转成文字、写总结……"
    : "说“开始记录”或按这里;我一直听、边听边转成文字,说“停止记录”后整理出重点、决定和待办。";
  const last = m.last;
  $("meetLast").hidden = !last;
  if (last) $("meetLast").textContent = `上次(${last.when},${last.minutes} 分钟)${last.title ? "“" + last.title + "”" : ""}:${(last.summary || "").split("\n")[0]}`;
  $("meetTodosRow").hidden = !(last && last.todos && last.todos.length && !last.todos_added);
  const list = $("meetList");
  list.replaceChildren();
  for (const r of m.recent || []) {
    const row = document.createElement("div");
    row.className = "set-row sub";
    const name = document.createElement("span");
    name.textContent = r.name;
    const ctl = document.createElement("span");
    ctl.className = "set-ctl";
    for (const [label, file] of [["文字记录", r.file], ["总结", r.summary]]) {
      if (!file) continue;
      const b = document.createElement("button");
      b.type = "button"; b.className = "mini-btn"; b.textContent = label;
      b.onclick = () => saveSetting({ meeting_open: file });
      ctl.append(b);
    }
    row.append(name, ctl);
    list.append(row);
  }
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
// 两边可以分开收起:收起时屏幕边上留一条小把手,鼠标移过去面板会“探出来”,移开自己缩回去;点把手就固定显示。
// 快捷键 Ctrl+[ 左边、Ctrl+] 右边、Ctrl+\ 两边。窗口太窄时两边自动收起,用把手打开(像抽屉)。
const oldPanels = loadPrefs("friday-panels", true);
const sides = { left: loadPrefs("friday-panel-left", oldPanels), right: loadPrefs("friday-panel-right", oldPanels) };
let panelPeekOn = loadPrefs("friday-panel-peek", true);
let peeking = null;
let peekTimer = null;
const narrow = window.matchMedia("(max-width: 1000px)");
const shown = (side) => sides[side] && !narrow.matches;
function applyPanels() {
  const body = document.body;
  body.classList.toggle("hide-left", !shown("left"));
  body.classList.toggle("hide-right", !shown("right"));
  body.classList.toggle("focus", !shown("left") && !shown("right"));
  for (const side of ["left", "right"]) {
    const tab = $("edge" + (side === "left" ? "Left" : "Right"));
    const on = shown(side);
    tab.classList.toggle("open", on);
    tab.setAttribute("aria-expanded", String(on || peeking === side));
    tab.title = on ? `收起${side === "left" ? "左" : "右"}边的面板(Ctrl+${side === "left" ? "[" : "]"})`
      : `显示${side === "left" ? "左" : "右"}边的面板(Ctrl+${side === "left" ? "[" : "]"})`;
    body.classList.toggle("peek-" + side, peeking === side && !on);
  }
  const any = shown("left") || shown("right");
  $("panelsBtn").setAttribute("aria-pressed", String(any));
  $("panelsBtn").title = any ? "隐藏两边的面板(Ctrl+\\)" : "显示两边的面板(Ctrl+\\)";
  $("setPanelLeft").checked = sides.left; $("setPanelRight").checked = sides.right; $("setPanelPeek").checked = panelPeekOn;
}
function setSide(side, on) {
  sides[side] = on;
  savePrefs("friday-panel-" + side, on);
  if (on && narrow.matches) { peek(side); return; }  // 窄窗口:当抽屉打开
  peeking = null;
  applyPanels();
  if (on) loadDash();
}
function setPanels(on) { sides.left = sides.right = on; savePrefs("friday-panel-left", on); savePrefs("friday-panel-right", on); peeking = null; applyPanels(); if (on) loadDash(); }
function peek(side) {
  clearTimeout(peekTimer);
  if (peeking === side) return;
  peeking = side;
  applyPanels();
  loadDash();
}
function unpeek(delay = 350) {
  clearTimeout(peekTimer);
  peekTimer = setTimeout(() => { peeking = null; applyPanels(); }, delay);
}
for (const [side, id] of [["left", "edgeLeft"], ["right", "edgeRight"]]) {
  const tab = $(id), hud = document.querySelector(".hud-" + side);
  tab.onclick = () => {
    if (narrow.matches) { peeking === side ? unpeek(0) : peek(side); return; }
    setSide(side, !sides[side]);
  };
  tab.addEventListener("mouseenter", () => { if (panelPeekOn && !narrow.matches && !shown(side)) peek(side); });
  tab.addEventListener("mouseleave", () => { if (peeking === side && !narrow.matches) unpeek(); });
  hud.addEventListener("mouseenter", () => { if (peeking === side) clearTimeout(peekTimer); });
  hud.addEventListener("mouseleave", () => { if (peeking === side && !narrow.matches) unpeek(); });
}
document.addEventListener("pointerdown", (e) => {  // 点面板外面:探出来的面板缩回去
  if (peeking && !e.target.closest(".hud, .edge-tab")) unpeek(0);
});
document.addEventListener("keydown", (e) => {
  if (!e.ctrlKey || e.altKey || e.metaKey) return;
  if (e.key === "[") { e.preventDefault(); narrow.matches ? (peeking === "left" ? unpeek(0) : peek("left")) : setSide("left", !sides.left); }
  else if (e.key === "]") { e.preventDefault(); narrow.matches ? (peeking === "right" ? unpeek(0) : peek("right")) : setSide("right", !sides.right); }
  else if (e.key === "\\") { e.preventDefault(); setPanels(!(shown("left") || shown("right"))); }
  else return;
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && peeking) unpeek(0); });
narrow.addEventListener("change", () => { peeking = null; applyPanels(); });
$("panelsBtn").onclick = () => setPanels(!(shown("left") || shown("right")));
$("setPanelLeft").onchange = (e) => setSide("left", e.target.checked);
$("setPanelRight").onchange = (e) => setSide("right", e.target.checked);
$("setPanelPeek").onchange = (e) => { panelPeekOn = e.target.checked; savePrefs("friday-panel-peek", panelPeekOn); };
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
  if (s.speak_max !== undefined) $("setSpeakMax").value = String(s.speak_max);
  const cq = s.call_quiet;
  $("callQuietRow").hidden = !cq || !cq.windows;
  $("speakWhenRow").hidden = !cq;
  $("callQuietNote").hidden = !cq;
  if (cq) {
    $("setCallQuiet").checked = cq.auto;
    $("setSpeakWhen").value = cq.mode;
    $("callQuietNote").textContent = (cq.now ? `📞 现在不出声:${cq.now}。` : "")
      + "用手机打电话时电脑测不到:跟星期五说“我在打电话”,打完说“打完了”。";
  }
  if (s.speech_rate) {
    speechRate = s.speech_rate;
    if (!$("setSpeechRate").options.length) for (const r of s.speech_rates || []) $("setSpeechRate").append(new Option(r === 1 ? "正常" : `${r} 倍`, String(r)));
    $("setSpeechRate").value = String(s.speech_rate);
  }
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
    $("snipHotkeyRow").hidden = !h.snip_options;
    const snipSelect = $("setSnipHotkey");
    if (h.snip_options && !snipSelect.options.length) {
      for (const [key, label] of [...h.snip_options, ["off", "不用"]]) {
        const opt = document.createElement("option");
        opt.value = key; opt.textContent = label;
        snipSelect.append(opt);
      }
    }
    if (h.snip_value) snipSelect.value = h.snip_value;
    $("snipHotkeyNote").hidden = !h.snip_error;
    $("snipHotkeyNote").textContent = h.snip_error ? "⚠ " + h.snip_error : "";
    $("snipHotkeyNote").className = "set-note warn";
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
  const stm2 = s.steam;
  if (stm2) {
    $("steamKey").placeholder = stm2.key ? `已设置 ${stm2.key}(要换才填)` : "32 位";
    if (document.activeElement !== $("steamId")) $("steamId").value = stm2.id || "";
    const parts = [];
    if (stm2.connected) parts.push(stm2.persona ? `✓ 已连上 Steam:${stm2.persona}` : "✓ Steam 钥匙和个人资料都填好了");
    else parts.push("还没连 Steam:填下面的钥匙和个人资料(开游戏不用钥匙也能用)");
    if (stm2.installed != null) parts.push(`这台电脑装了 ${stm2.installed} 个 Steam 游戏`);
    if (stm2.wishlist && stm2.wishlist.total) parts.push(`愿望单 ${stm2.wishlist.total} 个,在打折 ${stm2.wishlist.sales} 个`);
    $("steamStatus").textContent = parts.join(" · ");
    $("setWishlist").checked = !stm2.wishlist || stm2.wishlist.on;
    $("setWishlist").disabled = $("setWishlistMin").disabled = $("wishlistNow").disabled = !stm2.connected;
    if (stm2.wishlist) $("setWishlistMin").value = String(stm2.wishlist.min);
  }
  const ap = s.apps;
  $("appsGroup").hidden = !ap;
  if (ap) {
    if (document.activeElement !== $("spotifyClient")) $("spotifyClient").value = ap.spotify_client || "";
    $("spotifyNote").textContent = ap.spotify_connected
      ? "✓ Spotify 已连好:可以说“放周杰伦的晴天”“放我的歌单 开车”“现在放的是什么歌”。"
      : `在 developer.spotify.com/dashboard 建一个应用(免费),Redirect URI 填 ${ap.spotify_redirect},勾 Web API,把 Client ID 填在上面,再点“登录 Spotify”。只有 Premium 账号能直接播放。`;
    $("spotifyLogin").textContent = ap.spotify_connected ? "断开" : "登录 Spotify";
    $("nvidiaNote").hidden = !ap.nvidia;
    $("apexKey").placeholder = ap.apex_key ? `已设置 ${ap.apex_key}` : "钥匙";
    if (document.activeElement !== $("apexPlayer")) $("apexPlayer").value = ap.apex_player || "";
    $("apexPlatform").value = ap.apex_platform || "PC";
  }
  if (ap) {
    if (document.activeElement !== $("tapoUser")) $("tapoUser").value = ap.tapo_user || "";
    $("tapoPass").placeholder = ap.tapo_set ? "已设置(要换才填)" : "密码";
    if (ap.tapo_devices != null) $("tapoNote").textContent = `✓ 连上 Tapo,找到 ${ap.tapo_devices} 个设备:可以说“开灯”“关掉客厅灯”“灯调暗一点”“灯换成暖光”“家里哪些灯开着”。`;
    $("diaryRow").hidden = false;
    $("setDiary").value = ap.diary_time || "22:30";
  }
  $("orbRow").hidden = !s.floatorb;
  if (s.floatorb) $("setOrb").checked = s.floatorb.on;
  const tg = s.telegram;
  if (tg) {
    $("tgToken").placeholder = tg.set ? (tg.bot ? `已连接 @${tg.bot}(要换才填)` : "已设置(要换才填)") : "123456789:AA…";
    $("tgOwnersRow").hidden = !tg.set;
    const box = $("tgOwners");
    box.replaceChildren();
    for (const o of tg.owners || []) {
      const row = document.createElement("div");
      row.className = "set-row sub";
      const name = document.createElement("span");
      name.textContent = `${o.name || "(没有名字)"} · id ${o.id}`;
      const btn = document.createElement("button");
      btn.type = "button"; btn.className = "mini-btn"; btn.textContent = "移除";
      btn.onclick = () => { if (confirm(`移除“${o.name || o.id}”?它以后不能再用 Telegram 控制星期五。`)) saveSetting({ telegram_remove: o.id }, 5000); };
      row.append(name, btn);
      box.append(row);
    }
    if (tg.set && !(tg.owners || []).length) box.textContent = "还没有配对任何账号";
    $("tgPairNote").hidden = !tg.pair_code;
    if (tg.pair_code) $("tgPairNote").textContent = `配对码 ${tg.pair_code}:在 Telegram 里私聊你的机器人${tg.bot ? " @" + tg.bot : ""},发这 6 位数字。`;
  }
  const fb = s.folder_backup;
  $("fbGroup").hidden = !fb;
  if (fb) {
    if (document.activeElement !== $("fbSources")) $("fbSources").value = (fb.sources || []).join("\n");
    if (document.activeElement !== $("fbDest")) $("fbDest").value = fb.dest || "";
    $("fbStatus").textContent = fb.last || "";
    $("fbRun").disabled = !!fb.running;
    $("fbForce").hidden = !fb.blocked;
  }
  const cb = s.clipboard;
  $("clipRow").hidden = !cb;
  if (cb) { $("setClip").checked = cb.enabled; $("clipClear").textContent = `清空(${cb.count} 段)`; }
  const df = s.defender;
  $("virusQuick").parentElement.parentElement.hidden = !df;
  if (df) {
    const run = df.running;
    $("virusNote").textContent = run ? `正在${run.kind === "full" ? "全面" : "快速"}扫描(${run.minutes} 分钟了),扫完会告诉你`
      : "病毒扫描(Windows 自带的 Defender)";
    $("virusQuick").disabled = $("virusFull").disabled = !!run;
    $("virusLast").hidden = !df.last;
    $("virusLast").textContent = df.last ? "上次结果:" + df.last : "";
  }
  const sc = s.scenes;
  $("sceneGroup").hidden = !sc;
  if (sc && !$("sceneGroup").contains(document.activeElement)) {
    $("sceneHelp").textContent = "每行写一步,能用的写法:" + sc.help + "。说出任何一种“说法”就会执行(也可以说“开启XX模式”)。";
    const list = $("sceneList");
    list.replaceChildren();
    for (const scene of sc.list) list.append(sceneCard(scene));
  }
  if (s.meeting) showMeeting(s.meeting);
  const lb = s.lab;
  $("labGroup").hidden = !lb;
  const rv = s.selfreview;
  $("reviewRow").hidden = !rv;
  $("reviewNote").hidden = !rv || !(rv.busy || rv.last);
  if (rv) {
    $("setSelfReview").checked = rv.on;
    $("reviewNote").textContent = rv.busy ? "🧪 正在做自我检讨……" : rv.last ? `上次检讨:${rv.last.time} —— ${rv.last.head}` : "";
  }
  if (lb) {
    $("labNote").textContent = lb.busy ? `正在跑:${lb.busy}……` : lb.runs.length ? `最近 ${lb.runs.length} 次:` :
      "叫我“算一下……”“把这个 Excel 整理成……”“画个图”,我会写代码在这里跑;想改我自己的程序时,也会先在副本上跑全部测试。";
    const list = $("labList");
    list.replaceChildren();
    for (const r of lb.runs) {
      const box = document.createElement("details");
      box.className = "lab-run";
      const sum = document.createElement("summary");
      sum.textContent = `${r.ok ? "✅" : "❌"} ${r.time} ${r.kind === "self_test" ? "自我测试" : "跑代码"}「${r.title}」`
        + (r.kind === "self_test" ? `(${r.ran} 个测试${r.new_failures && r.new_failures.length ? `,改坏 ${r.new_failures.length} 个` : ""})` : ` ${r.seconds} 秒`);
      const pre = document.createElement("pre");
      pre.textContent = r.kind === "self_test"
        ? [r.error, (r.new_failures || []).join("\n"), r.report ? "报告:" + r.report : ""].filter(Boolean).join("\n\n")
        : [r.code, "—— 输出 ——", r.output, r.error ? "—— 错误 ——\n" + r.error : "", (r.produced || []).length ? "产生的文件:" + r.produced.join("、") : ""].filter(Boolean).join("\n");
      box.append(sum, pre);
      list.append(box);
    }
  }
  const st = s.startup;
  $("startGroup").hidden = !st || !st.windows;
  if (st && st.windows) {
    const on = st.items.filter((i) => i.enabled);
    const offSuggest = on.filter((i) => i.suggest === "off");
    $("startSummary").textContent = st.checking ? "正在检查……(大约半分钟)"
      : `开机会自动启动 ${on.length} 个程序` + (offSuggest.length ? `,其中 ${offSuggest.length} 个建议关掉` : "")
        + (st.boot_seconds ? `。上次开机花了约 ${st.boot_seconds} 秒` : "") + (st.time ? `(检查于 ${st.time})` : "");
    $("startCheck").disabled = !!st.checking;
    const order = { off: 0, maybe: 1, keep: 2 };
    const list = $("startList");
    list.replaceChildren();
    for (const item of [...st.items].sort((a, b) => order[a.suggest] - order[b.suggest] || b.slow_seconds - a.slow_seconds)) {
      const row = document.createElement("div");
      row.className = "set-row sub" + (item.can_toggle ? "" : " locked-row");
      const label = document.createElement("label");
      const tag = { off: "建议关", maybe: "看情况", keep: "建议留" }[item.suggest];
      label.textContent = `${item.name} · ${tag}` + (item.slow_seconds ? ` · 拖慢开机 ${item.slow_seconds} 秒` : "") + (item.can_toggle ? "" : " · 要在任务管理器改");
      label.title = item.why;
      const sw = document.createElement("input");
      sw.type = "checkbox"; sw.className = "switch"; sw.checked = item.enabled; sw.disabled = !item.can_toggle;
      sw.setAttribute("aria-label", `${item.name} 开机启动`);
      sw.onchange = () => saveSetting({ startup_toggle: { name: item.name, enabled: sw.checked } });
      row.append(label, sw);
      list.append(row);
    }
    const extra = [];
    if (st.reboot) extra.push("Windows 有更新等着重启才完成。");
    if (st.last_update) extra.push(`上次装 Windows 更新:${st.last_update}。`);
    const bad = st.disks.filter((d) => !["healthy", "", "0"].includes(String(d.health).toLowerCase()));
    if (st.disks.length) extra.push(bad.length ? `⚠ 硬盘健康有问题:${bad.map((d) => d.name).join("、")},重要文件先备份!` : "硬盘健康正常。");
    $("startExtra").hidden = !extra.length;
    $("startExtra").textContent = extra.join(" ");
    $("startCleanRow").hidden = !st.temp_bytes;
    $("startCleanText").textContent = `临时文件夹里有 ${(st.temp_bytes / 1024 ** 2).toFixed(0)} MB 超过 7 天没用的临时文件`;
  }
  const rc = s.receipts;
  $("rcptGroup").hidden = !rc;
  if (rc) {
    $("setReceipts").checked = rc.on;
    $("rcptNote").textContent = rc.scanning ? "正在看邮件……"
      : rc.pending.length ? `${rc.pending.length} 笔待确认` : "没有待确认的" + (rc.last_scan ? `(上次看邮件 ${rc.last_scan})` : "");
    $("rcptAll").hidden = $("rcptNone").hidden = !rc.pending.length;
    const list = $("rcptList");
    list.replaceChildren();
    for (const p of rc.pending) {
      const row = document.createElement("div");
      row.className = "set-row sub";
      const text = document.createElement("span");
      text.textContent = `${p.date.slice(5)} ${p.item} · ${p.currency === "MYR" ? "RM" : p.currency + " "}${p.amount.toFixed(2)} · ${p.category}` + (p.duplicate ? " · ⚠ 好像已经记过" : "");
      text.title = `${p.from}\n${p.subject}`;
      const ctl = document.createElement("span");
      ctl.className = "set-ctl";
      for (const [label, key] of [["记账", "receipt_confirm"], ["忽略", "receipt_dismiss"]]) {
        const b = document.createElement("button");
        b.type = "button"; b.className = "mini-btn"; b.textContent = label;
        b.onclick = () => saveSetting({ [key]: [p.id] });
        ctl.append(b);
      }
      row.append(text, ctl);
      list.append(row);
    }
  }
  const en = s.energy;
  $("energyGroup").hidden = !en;
  if (en) {
    if (document.activeElement !== $("electricRate")) $("electricRate").value = en.rate;
    $("energyNote").textContent = en.devices.length ? `这个月插座一共 ${en.month_total} 度电,约 RM${en.month_cost.toFixed(2)}`
      : "还没有资料:要有会计电的 Tapo 插座,而且已经在“连接的应用”里连上 Tapo。";
    const list = $("energyList");
    list.replaceChildren();
    for (const d of en.devices) {
      const row = document.createElement("div");
      row.className = "set-row sub";
      row.textContent = `${d.name} · 现在 ${d.power} 瓦 · 今天 ${d.today} 度 · 这星期 ${d.week} 度 · 这个月 ${d.month} 度(约 RM${(d.month * en.rate).toFixed(2)})`;
      list.append(row);
    }
  }
  const pl = s.planner;
  $("planGroup").hidden = !pl;
  if (pl) {
    if (document.activeElement !== $("planHours")) $("planHours").value = pl.hours;
    const fmt = (iso) => `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))} ${iso.slice(11, 16)}`;  // 照星期五排的时间显示
    const showing = pl.proposal.length ? pl.proposal : pl.entries;
    $("planNote").textContent = pl.proposal.length ? "这样安排好吗?(还没放进日历)"
      : pl.entries.length ? `已经排进日历 ${pl.entries.length} 件:` : "说“帮我安排这周的待办”,或按这里;先给你看安排,同意才放进 Google 日历。";
    $("planActions").hidden = !pl.proposal.length;
    const list = $("planList");
    list.replaceChildren();
    for (const p of showing) {
      const row = document.createElement("div");
      row.className = "set-row sub";
      row.textContent = `${fmt(p.start)}~${fmt(p.end).slice(-5)} ${p.title}` + (p.moves ? `(挪过 ${p.moves} 次)` : "");
      list.append(row);
    }
    if (pl.proposal.length && pl.left.length) {
      const row = document.createElement("div");
      row.className = "set-note";
      row.textContent = "排不下:" + pl.left.join("、");
      list.append(row);
    }
  }
  const pc = s.parcels;
  $("parcelGroup").hidden = !pc;
  if (pc) {
    const statusText = { ordered: "下单了", shipped: "出货了", in_transit: "运送中", out_for_delivery: "今天派送", delivered: "送到了", problem: "出问题了" };
    $("parcelNote").textContent = pc.scanning ? "正在看邮件……"
      : pc.items.length ? `在路上的包裹 ${pc.items.length} 个` : "现在没有在路上的包裹" + (pc.last_scan ? `(上次看邮件 ${pc.last_scan})` : "");
    const list = $("parcelList");
    list.replaceChildren();
    for (const i of pc.items) {
      const row = document.createElement("div");
      row.className = "set-row sub";
      const text = document.createElement("span");
      text.textContent = `${i.item || "包裹"}${i.shop ? "(" + i.shop + ")" : ""} · ${statusText[i.status] || ""}`
        + (i.eta && i.status !== "delivered" ? ` · 预计 ${i.eta.slice(5)}` : "") + (i.courier ? ` · ${i.courier}` : "") + (i.tracking ? ` · ${i.tracking}` : "");
      const b = document.createElement("button");
      b.type = "button"; b.className = "mini-btn"; b.textContent = "收到了";
      b.onclick = () => saveSetting({ parcel_received: i.id });
      row.append(text, b);
      list.append(row);
    }
  }
  const subs = s.subscriptions;
  $("subGroup").hidden = !subs;
  if (subs) {
    const live = subs.items.filter((i) => !i.cancelled);
    $("subTotal").textContent = subs.scanning ? "正在邮箱里找订阅……"
      : live.length ? `${live.length} 个订阅,一年大约 RM${subs.yearly.toLocaleString()}` + (subs.unknown.length ? ` + ${subs.unknown.join("、")}` : "")
        + (subs.last_scan ? `(上次找邮件 ${subs.last_scan})` : "")
      : "还没有记着的订阅。按“去邮箱找”,或者在下面自己加。";
    const cycleText = { monthly: "每月", yearly: "每年", weekly: "每周", quarterly: "每季" };
    const list = $("subList");
    list.replaceChildren();
    for (const item of subs.items) {
      const row = document.createElement("div");
      row.className = "set-row sub" + (item.cancelled ? " muted" : "");
      const text = document.createElement("span");
      const price = item.amount ? `${item.currency === "MYR" ? "RM" : item.currency + " "}${item.amount}` : "金额不明";
      text.textContent = `${item.name} · ${cycleText[item.cycle] || "每月"} ${price}` + (item.next ? ` · 下次 ${item.next.slice(5)}` : "")
        + (item.cancelled ? " · 已取消" : "") + (item.source === "email" ? " · 从邮件找到" : "");
      if (item.cancel_hint) text.title = "怎么取消:" + item.cancel_hint;
      const ctl = document.createElement("span");
      ctl.className = "set-ctl";
      const toggle = document.createElement("button");
      toggle.type = "button"; toggle.className = "mini-btn"; toggle.textContent = item.cancelled ? "恢复" : "已取消";
      toggle.onclick = () => saveSetting({ sub_cancel: { id: item.id, cancelled: !item.cancelled } });
      const del = document.createElement("button");
      del.type = "button"; del.className = "mini-btn"; del.textContent = "删掉";
      del.onclick = () => saveSetting({ sub_remove: item.id });
      ctl.append(toggle, del);
      row.append(text, ctl);
      list.append(row);
    }
  }
  const lib = s.library;
  $("libGroup").hidden = !lib;
  if (lib) {
    $("setLibOn").checked = lib.on;
    if (document.activeElement !== $("libFolders")) $("libFolders").value = lib.folders.join("\n");
    $("libStatus").textContent = lib.busy ? `正在整理…… ${lib.progress || ""}`
      : `有 ${lib.files} 个文件(共 ${lib.pages} 页/段)` + (lib.refreshed ? `,上次整理 ${lib.refreshed}` : ",还没整理过") + (lib.error ? `。出错:${lib.error}` : "");
    $("libBad").hidden = !lib.unreadable.length;
    $("libBad").textContent = `读不了的 ${lib.failed} 个:` + lib.unreadable.join("、") + (lib.failed > lib.unreadable.length ? "……" : "");
  }
  const bg = s.background;
  $("bgGroup").hidden = !bg;
  if (bg) {
    const statusText = { waiting: "排队中", running: "正在做", done: "做完了", failed: "没做成", cancelled: "取消了", interrupted: "中断了" };
    const list = $("bgList");
    list.replaceChildren();
    for (const j of [...bg.recent].reverse()) {
      const row = document.createElement("div");
      row.className = "set-row sub";
      const text = document.createElement("span");
      text.textContent = `${j.added || ""} ${j.goal}` + (j.summary ? ` —— ${j.summary}` : "");
      const tag = document.createElement("span");
      tag.className = "set-note";
      tag.textContent = statusText[j.status] || j.status;
      row.append(text, tag);
      list.append(row);
    }
    $("bgCancel").hidden = !bg.running && !bg.waiting;
  }
  const vc = s.vocab;
  $("learnGroup").hidden = !vc;
  if (vc) {
    $("setLearn").checked = vc.learn; $("setSttMixed").checked = vc.mixed;
    const stt = vc.stt || {};
    $("setSttEngine").value = stt.installing ? "whisper" : (stt.engine || "google");
    const sttText = stt.installing ? (stt.note || "🎙 正在装本机语音识别……")
      : stt.problem && stt.engine === "whisper" ? "⚠ 本机语音识别出问题,这次改用 Google 听:" + stt.problem
      : stt.engine === "whisper" && stt.info ? `🎙 正在用本机 Whisper(${stt.info})`
      : stt.note && stt.note.startsWith("⚠") ? stt.note : "";
    $("sttNote").hidden = !sttText;
    $("sttNote").textContent = sttText;
    if (document.activeElement !== $("vocabWords")) $("vocabWords").value = vc.words.join(", ");
    const list = $("vocabList");
    list.replaceChildren();
    for (const f of vc.fixes) {
      const row = document.createElement("div");
      row.className = "set-row sub";
      const text = document.createElement("span");
      text.textContent = `“${f.heard}” → ${f.meant}`;
      const del = document.createElement("button");
      del.type = "button"; del.className = "mini-btn"; del.textContent = "删掉";
      del.onclick = () => saveSetting({ vocab_remove: f.heard });
      row.append(text, del);
      list.append(row);
    }
    $("vocabNote").textContent = (vc.lessons ? `已经记下 ${vc.lessons} 条教训(在“记忆”里分类“教训”,可以改或删)。` : "")
      + "也可以直接说“以后听到米谷就是 Mid Valley”。被你纠正“你听错了,我说的是……”时,我也会自己加进来。";
  }
  const mt = s.memtidy;
  $("memtidyGroup").hidden = !mt;
  if (mt) {
    const list = $("memtidyList");
    list.replaceChildren();
    for (const p of mt.proposals) {
      const row = document.createElement("label");
      row.className = "tidy-item";
      const box = document.createElement("input");
      box.type = "checkbox"; box.checked = true; box.value = p.id;
      const text = document.createElement("span");
      if (p.kind === "merge") text.textContent = `合并:${p.from.join(" + ")} → “${p.text}”`;
      else if (p.kind === "outdated") text.textContent = `删掉(可能过时):“${p.text}” —— ${p.reason}`;
      else text.textContent = `换分类:“${p.text}” → ${p.category}`;
      row.append(box, text);
      list.append(row);
    }
    $("memtidyActions").hidden = !mt.proposals.length;
    $("memtidyNote").textContent = mt.proposals.length ? "这些是建议,打勾的才会改:"
      : `每周用最便宜的 Haiku 帮星期五整理一次记忆(现在 ${mt.facts} 条),只给建议,你勾了才改` + (mt.last_run ? `。上次:${mt.last_run}` : "");
  }
  if (s.cleanup && !$("cleanupList").children.length) showCleanup(s.cleanup);
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
    const rl = sec.remote_lock || {};
    $("remoteLockState").textContent = rl.paused ? "已暂停" : rl.enabled ? "已开启" : "没开";
    $("remoteLockState").className = "google-state " + (rl.paused ? "warn" : rl.enabled ? "ok" : "warn");
    $("remoteLockSetup").hidden = !!rl.enabled || !sec.discord;
    $("remoteLockOff").hidden = !rl.enabled;
    $("remoteLockResume").hidden = !rl.paused;
    $("pinState").textContent = sec.pin_set ? "已设" : "没设";
    $("pinState").className = "google-state " + (sec.pin_set ? "ok" : "warn");
    $("pinOld").hidden = !sec.pin_set;
    $("setLockMinutes").value = String(sec.lock_minutes || 0);
    $("lockBtn").hidden = !sec.pin_set;
    const notes = ["钥匙和密码文件星期五不读不碰", "网页、邮件里的指令不照做", "Discord 配对码猜错 5 次就拉黑"];
    notes.push(sec.vendor_local ? "光球的程序库已存在本机" : "光球的程序库还在从网站读取(下次打开会存到本机)");
    if (sec.blocked) notes.push(`已拉黑 ${sec.blocked} 个乱猜配对码的账号`);
    $("securityNote").textContent = "✓ " + notes.join(" · ");
  }
  $("rainRow").hidden = !s.rain_alert;
  if (s.rain_alert) $("setRain").value = s.rain_alert;
  $("briefRow").hidden = s.brief == null;
  if (s.brief != null) $("setBrief").checked = s.brief;
  $("eventRow").hidden = s.event_lead === null || s.event_lead === undefined;
  if (s.event_lead != null) $("setEventLead").value = String(s.event_lead);
  const hr = s.homerules;
  $("ruleGroup").hidden = !hr;
  if (hr) {
    const list = $("ruleList");
    list.replaceChildren();
    for (const r of hr) {
      const row = document.createElement("div");
      row.className = "set-row sub";
      const label = document.createElement("span");
      label.textContent = `${r.id}. ${r.text}${r.enabled ? "" : "(暂停中)"}`;
      const ctl = document.createElement("span");
      ctl.className = "set-ctl";
      const pause = document.createElement("button");
      pause.type = "button"; pause.className = "mini-btn"; pause.textContent = r.enabled ? "暂停" : "恢复";
      pause.onclick = () => saveSetting({ homerule_toggle: r.id });
      const del = document.createElement("button");
      del.type = "button"; del.className = "mini-btn"; del.textContent = "删掉";
      del.onclick = () => saveSetting({ homerule_remove: r.id });
      ctl.append(pause, del);
      row.append(label, ctl);
      list.append(row);
    }
  }
  const sw = s.spendwatch;
  $("spendGroup").hidden = !sw;
  if (sw) {
    $("setSpendAlerts").checked = sw.on;
    if (document.activeElement !== $("setSpendBudget")) $("setSpendBudget").value = sw.budget || 0;
    $("spendNote").textContent = `这个月到现在 RM${sw.month_to_date.toFixed(0)},照最近的速度月底大概 RM${sw.forecast.toFixed(0)}`
      + (sw.budget ? `(预算 RM${sw.budget.toFixed(0)},${sw.forecast > sw.budget ? "⚠ 会超" : "来得及"})` : "")
      + "。也可以问“这个月会花多少”。";
  }
  const pr = s.practice;
  $("practiceGroup").hidden = !pr;
  if (pr) {
    $("setPracticeGoal").value = String(pr.goal);
    $("setPracticeRemind").value = pr.remind;
    if (document.activeElement !== $("setPracticeApps")) $("setPracticeApps").value = pr.apps || "";
    $("practiceNote").textContent = (pr.now ? `🎸 正在练(${pr.now})· ` : "") + `今天 ${pr.today} 分钟`
      + (pr.goal ? ` / 目标 ${pr.goal}` : "") + ` · 这周 ${pr.week_days} 天共 ${pr.week} 分钟`
      + (pr.streak >= 2 ? ` · 连续 ${pr.streak} 天` : "");
  }
  const stm = s.statement;
  $("stmtGroup").hidden = !stm;
  if (stm) $("setStmtPw").placeholder = stm.passwords ? `已存 ${stm.passwords} 个(要改就重新填)` : "只存在这台电脑";
  const bf = s.billfile;
  $("billGroup").hidden = !bf;
  if (bf) {
    $("setBillFile").checked = bf.on;
    $("setBillFolder").placeholder = bf.folder;
    if (document.activeElement !== $("setBillFolder")) $("setBillFolder").value = bf.custom || "";
    $("billNote").textContent = bf.recent.length
      ? "最近归档:" + bf.recent.map((m) => `${m.time.slice(5)} ${m.name}`).join(";")
      : "认不出的文件不会动;有密码的 PDF 也不动。认不出时会请 Haiku 看前两页(身份证、卡号先遮掉,每个约 RM0.01)。";
  }
  const pf = s.photofind;
  $("photoFindRow").hidden = !pf;
  if (pf) {
    $("photoFindNote").textContent = pf.busy ? "照片按时间地点找:正在整理目录……"
      : `照片按时间地点找:${pf.photos} 张,${pf.with_place} 张有地点(${pf.places} 个地方)` +
        (pf.scanned ? `,上次整理 ${pf.scanned}` : ",目录还没做") + "。可以问“去年在槟城拍的照片”";
  }
  const dp = s.departure;
  $("departGroup").hidden = !dp;
  if (dp) {
    $("setDepartOn").checked = dp.on;
    if (document.activeElement !== $("setDepartHome")) $("setDepartHome").value = dp.home || "";
    $("setDepartExtra").value = String(dp.extra);
    const next = (dp.upcoming || []).map((u) => `${u.leave} 出门 → ${u.start}「${u.title}」(预留 ${u.minutes} 分钟)`);
    $("departNote").textContent = dp.problem ? "⚠ " + dp.problem
      : !dp.home ? "先写家的地址才会提醒" : next.length ? "接下来:" + next.join(";") : "接下来 4 小时没有要出门的活动";
    $("departNote").className = "set-note" + (dp.problem ? " warn" : "");
  }
  const ml = s.mail_alert;
  $("mailGroup").hidden = !ml;
  if (ml && !$("mailGroup").contains(document.activeElement)) {
    $("mailSenders").value = ml.senders.join(", ");
    $("mailWords").value = ml.words.join(", ");
    $("mailImportant").checked = ml.important;
  }
  const md = ml && ml.draft;
  $("mailDraftRow").hidden = !md;
  $("mailDraftNote").hidden = !md || !md.recent.length;
  if (md) {
    $("setMailDraft").checked = md.on;
    $("mailDraftNote").textContent = `今天还可以拟 ${md.left} 封。最近:` +
      md.recent.map((r) => `${r.time}「${r.subject}」${r.drafted ? "✉ 有草稿" : "(不用回)"}`).join(";");
  }
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
    $("setGameBoost").checked = care.game_boost !== false;
    $("setGameReport").checked = care.game_report !== false;
    if (document.activeElement !== $("setGameClose")) $("setGameClose").value = (care.game_close || []).join(", ");
  }
  $("setAutoUpdate").checked = s.auto_update !== false;
  $("pttDuckRow").hidden = !s.ptt;
  $("subtitleRow").hidden = !s.ptt;
  if (s.ptt) { $("setPttDuck").checked = s.ptt.duck !== false; $("setSubtitles").checked = s.ptt.subtitles !== false; }
  $("subtitleOptRow").hidden = $("duckToRow").hidden = !s.ptt;
  if (s.ptt) {
    if (!$("subPos").options.length) for (const [k, v] of s.ptt.sub_positions) $("subPos").append(new Option(v, k));
    if (!$("duckTo").options.length) for (const p of s.ptt.duck_choices) $("duckTo").append(new Option(`${p}%`, String(p)));
    $("subPos").value = s.ptt.sub_pos; $("subSize").value = s.ptt.sub_size; $("subTime").value = s.ptt.sub_time;
    $("duckTo").value = String(s.ptt.duck_to);
  }
  fillLightLink(s.lightlink);
  fillParty(s.party);
  for (const [row, select, button, state] of [["pttRow", "setPtt", "pttCapture", s.ptt],
                                                ["shotRow", "setShot", "shotCapture", s.ptt && s.ptt.shot]]) {
    $(row).hidden = !state;
    if (!state) continue;
    const sel = $(select);
    if (sel.options.length <= 1) {
      for (const [key, label] of s.ptt.keys) sel.append(new Option(label, key));
      sel.append(new Option("自定义…", "custom"));
    }
    if (keyCapturing !== button) {
      sel.value = state.key;
      $(button).hidden = state.key !== "custom";
      $(button).textContent = state.custom ? `现在是 ${state.custom}(按这里换)` : "按这里,再按你要的键";
    }
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
  if (document.activeElement !== $("setKeyExpires")) $("setKeyExpires").value = s.key_expires || "";
  $("apiKeyRow").hidden = !s.api_key_tail;
  if (s.api_key_tail) $("apiKeyTail").textContent = "…" + s.api_key_tail;
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
// ---------- 聊天记录:按日期看、搜索、复制 ----------
let histDate = "";
let histTimer = null;
function histDayLabel(date) {
  const [y, m, d] = date.split("-").map(Number);
  return dayLabel(new Date(y, m - 1, d).getTime());
}
function renderHistory(r, query) {
  const nav = $("histDays");
  const all = document.createElement("button");
  all.type = "button"; all.className = "set-tab" + (!histDate ? " on" : "");
  all.innerHTML = '<span class="set-tab-icon" aria-hidden="true">🕘</span><span>最近</span>';
  all.onclick = () => { histDate = ""; loadHistory(); };
  nav.replaceChildren(all, ...(r.days || []).map((d) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "set-tab" + (histDate === d.date ? " on" : "");
    const label = document.createElement("span"); label.textContent = histDayLabel(d.date);
    const count = document.createElement("small"); count.className = "hist-count"; count.textContent = d.count;
    b.append(label, count);
    b.onclick = () => { histDate = d.date; loadHistory(); };
    return b;
  }));
  const list = $("histList");
  const items = r.items || [];
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "set-empty";
    empty.textContent = query ? `没有找到“${query}”。换个关键词试试。` : "这里还没有聊天记录。";
    list.replaceChildren(empty);
    return;
  }
  const head = document.createElement("div");
  head.className = "hist-summary";
  head.textContent = (query ? `找到 ${r.total} 条` : histDate ? `${histDayLabel(histDate)}共 ${r.total} 条` : `最近的问答`) +
    (r.total > items.length ? `(显示最新 ${items.length} 条)` : "");
  list.replaceChildren(head, ...items.map((e) => {
    const card = document.createElement("article");
    card.className = "hist-item";
    const top = document.createElement("div"); top.className = "hist-top";
    const time = document.createElement("span"); time.className = "hist-time"; time.textContent = e.time;
    const copy = document.createElement("button"); copy.type = "button"; copy.className = "msg-copy"; copy.textContent = "复制回答";
    copy.onclick = async () => { copy.textContent = (await copyText(e.a)) ? "已复制 ✓" : "复制失败"; setTimeout(() => { copy.textContent = "复制回答"; }, 1600); };
    const ask = document.createElement("button"); ask.type = "button"; ask.className = "msg-copy"; ask.textContent = "再问一次";
    ask.title = "把这个问题放进输入框";
    ask.onclick = () => { closeHistory(); $("input").value = e.q; $("input").focus(); };
    top.append(time, ask, copy);
    const q = document.createElement("div"); q.className = "hist-q"; q.textContent = e.q;
    const a = document.createElement("div"); a.className = "hist-a"; a.textContent = e.a;
    card.append(top, q, a);
    if (e.a.length > 320) {
      card.classList.add("long");
      const more = document.createElement("button"); more.type = "button"; more.className = "msg-more"; more.textContent = "展开全部 ▾";
      more.onclick = () => { const open = card.classList.toggle("open"); more.textContent = open ? "收起 ▴" : "展开全部 ▾"; };
      card.append(more);
    }
    return card;
  }));
  list.scrollTop = 0;
}
async function loadHistory() {
  const query = $("histSearch").value.trim();
  const params = new URLSearchParams({ q: query, date: query ? "" : histDate });
  const r = await api(`/api/history?${params}`).catch(() => ({ error: "读不到聊天记录" }));
  if (r.error) { toast(r.error); return; }
  renderHistory(r, query);
}
function openHistory() { $("historyModal").hidden = false; $("histSearch").value = ""; histDate = ""; loadHistory(); $("histSearch").focus(); }
function closeHistory() { $("historyModal").hidden = true; }
$("historyBtn").onclick = openHistory;
$("historyClose").onclick = closeHistory;
$("historyModal").addEventListener("click", (e) => { if (e.target === $("historyModal")) closeHistory(); });
$("histSearch").addEventListener("input", () => { clearTimeout(histTimer); histTimer = setTimeout(loadHistory, 300); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("historyModal").hidden) closeHistory(); });

// ---------- 齿轮设定:左边分类、上面搜索 ----------
const SET_CATS = [
  ["general", "⚙", "常用"], ["voice", "🔊", "声音"], ["listen", "🎙", "语音识别和唤醒"], ["remind", "⏰", "提醒"],
  ["mail", "📧", "邮件"], ["money", "💰", "花费"], ["home", "🏠", "家里"], ["game", "🎮", "游戏"],
  ["life", "🎸", "生活"], ["work", "📋", "工作和资料"], ["pc", "🖥", "电脑"], ["look", "🎨", "外观"],
  ["apps", "🔗", "连接的应用"], ["backup", "💾", "备份"], ["security", "🔒", "安全"], ["other", "ℹ", "账号和版本"],
];
// 拆开的区块跟着原来那一块显示 / 隐藏(例如“语音识别”跟着“听懂我”)
function syncFollowers() {
  document.querySelectorAll("#setPane [data-follow]").forEach((g) => {
    const lead = $(g.dataset.follow);
    if (lead && g.hidden !== lead.hidden) g.hidden = lead.hidden;  // 没变就别动(不然会一直触发下面的监看)
  });
}
// 每一类最上面:这一类有哪几块,点一下跳过去
function buildSetToc() {
  const toc = $("setToc");
  const shown = setGroups().filter((g) => !g.classList.contains("set-off") && groupHasContent(g));
  toc.replaceChildren(...shown.map((g) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "set-toc-chip";
    b.textContent = (g.querySelector("h3")?.textContent || "").replace(/[((].*$/, "");
    b.onclick = () => g.scrollIntoView({ behavior: "smooth", block: "start" });
    return b;
  }));
  const hide = shown.length < 3 || !!$("setSearch").value;
  if (toc.hidden !== hide) toc.hidden = hide;
}
let setCat = "general";
try { setCat = localStorage.getItem("friday-set-cat") || "general"; } catch {}
const setGroups = () => [...document.querySelectorAll("#setPane .set-group")];
function groupHasContent(g) {
  if (g.hasAttribute("hidden")) return false;
  return [...g.children].some((c) => c.tagName !== "H3" && !c.hidden);
}
function buildSetNav() {
  const nav = $("setNav");
  const live = new Set(setGroups().filter(groupHasContent).map((g) => g.dataset.cat));
  if (!live.has(setCat)) setCat = "general";
  nav.replaceChildren(...SET_CATS.filter(([key]) => live.has(key)).map(([key, icon, label]) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "set-tab" + (key === setCat && !$("setSearch").value ? " on" : "");
    b.dataset.cat = key;
    b.innerHTML = `<span class="set-tab-icon" aria-hidden="true">${icon}</span><span>${label}</span>`;
    b.onclick = () => { setCat = key; $("setSearch").value = ""; try { localStorage.setItem("friday-set-cat", key); } catch {} showSetCat(); };
    return b;
  }));
}
function markFirstGroup() {
  let first = true;
  for (const g of setGroups()) {
    const visible = !g.classList.contains("set-off") && groupHasContent(g);
    g.classList.toggle("set-first", visible && first);
    if (visible) first = false;
  }
}
function clearSetSearch() {
  document.querySelectorAll("#setPane .set-miss").forEach((el) => el.classList.remove("set-miss"));
  document.querySelectorAll("#setPane mark.set-hit").forEach((m) => m.replaceWith(m.textContent));
}
function showSetCat() {
  clearSetSearch();
  syncFollowers();
  $("setEmpty").hidden = true;
  for (const g of setGroups()) g.classList.toggle("set-off", g.dataset.cat !== setCat);
  buildSetNav();
  markFirstGroup();
  buildSetToc();
  $("setPane").scrollTop = 0;
}
function searchSettings(text) {
  const words = text.trim().toLowerCase();
  clearSetSearch();
  syncFollowers();
  if (!words) { showSetCat(); return; }
  let found = 0;
  for (const g of setGroups()) {
    const title = (g.querySelector("h3")?.textContent || "").toLowerCase();
    const keys = (g.dataset.keys || "").toLowerCase();  // 常见说法(例如“灯”找得到“家里”)
    const groupHit = title.includes(words) || keys.split(" ").some((k) => k && (k.includes(words) || words.includes(k)))
      || (SET_CATS.find(([k]) => k === g.dataset.cat)?.[2] || "").includes(words);
    let rows = 0;
    for (const row of g.querySelectorAll(":scope > .set-row, :scope > .set-note, :scope > div:not(.set-row):not(.set-note)")) {
      if (row.hidden) continue;
      const hit = groupHit || row.textContent.toLowerCase().includes(words);
      row.classList.toggle("set-miss", !hit);
      if (hit) rows++;
    }
    const show = rows > 0 && groupHasContent(g);
    g.classList.toggle("set-off", !show);
    if (show) found++;
  }
  $("setEmpty").hidden = found > 0;
  if (!$("setToc").hidden) $("setToc").hidden = true;
  markFirstGroup();
  document.querySelectorAll("#setNav .set-tab").forEach((b) => b.classList.remove("on"));
}
$("setSearch").addEventListener("input", (e) => searchSettings(e.target.value));
$("setSearch").addEventListener("keydown", (e) => { if (e.key === "Escape" && e.target.value) { e.stopPropagation(); e.target.value = ""; showSetCat(); } });
new MutationObserver((records) => {
  if ($("settings").hidden || records.every((r) => r.target.id === "setToc")) return;
  syncFollowers();
  if (!$("setSearch").value) { buildSetNav(); buildSetToc(); }
})
  .observe($("setPane"), { attributes: true, attributeFilter: ["hidden"], subtree: true });

function openSettings() {
  fillSettings();
  $("settings").hidden = false;
  $("setSearch").value = "";
  showSetCat();
  $("setSearch").focus();
}
function closeSettings() { $("settings").hidden = true; }
$("settingsBtn").onclick = openSettings;
$("settingsClose").onclick = closeSettings;
$("settings").addEventListener("click", (e) => { if (e.target.id === "settings") closeSettings(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("settings").hidden) closeSettings(); });
$("saveCallMe").onclick = () => saveSetting({ call_me: $("setCallMe").value.trim() });
$("setCallMe").onkeydown = (e) => { if (e.key === "Enter") $("saveCallMe").click(); };
$("setVoice").onchange = (e) => saveSetting({ voice: e.target.value });
$("setSpeakMax").onchange = (e) => saveSetting({ speak_max: e.target.value });
$("setCallQuiet").onchange = (e) => saveSetting({ call_quiet: e.target.checked });
$("setSpeakWhen").onchange = (e) => saveSetting({ speak_when: e.target.value });
$("setSpeechRate").onchange = (e) => saveSetting({ speech_rate: e.target.value });
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
$("setSnipHotkey").onchange = (e) => saveSetting({ snip_hotkey: e.target.value });
$("setPush").onchange = (e) => saveSetting({ push: e.target.value });
$("setCareWelcome").onchange = (e) => saveSetting({ care_welcome: e.target.checked });
$("setCareBreak").onchange = (e) => saveSetting({ care_break: Number(e.target.value) });
$("setCareNight").onchange = (e) => saveSetting({ care_night: e.target.checked });
$("setHealth").onchange = (e) => saveSetting({ health: e.target.checked });
$("tidyNow").onclick = () => { if (confirm("把下载文件夹最上层的文件按类型分进子文件夹(图片、文档、安装程序……)?整理错了可以撤销。")) saveSetting({ tidy_downloads: true }, 6000); };
$("tidyUndo").onclick = () => saveSetting({ undo_tidy: true }, 5000);
$("remoteLockSetup").onclick = async () => {
  const r = await api("/api/settings", { remote_lock_setup: true }).catch(() => ({ error: "星期五没有响应" }));
  if (r.error) { toast(r.error, 4000); return; }
  $("remoteLockSecret").textContent = r.secret;
  $("remoteLockBox").hidden = false;
  $("remoteLockCode").value = "";
  $("remoteLockCode").focus();
};
$("remoteLockConfirm").onclick = async () => {
  if (await saveSetting({ remote_lock_confirm: $("remoteLockCode").value.trim() }, 6000)) {
    $("remoteLockBox").hidden = true; $("remoteLockSecret").textContent = "";
  }
};
$("remoteLockOff").onclick = () => { if (confirm("关闭手机遥控锁?以后用 Discord 遥控时只要回复 y 就能允许操作。")) saveSetting({ remote_lock_off: true }); };
$("remoteLockResume").onclick = () => saveSetting({ remote_lock_resume: true });
$("pinSave").onclick = async () => {
  if (await saveSetting({ pin_old: $("pinOld").value, pin_new: $("pinNew").value, pin_new2: $("pinNew2").value }, 4000))
    for (const id of ["pinOld", "pinNew", "pinNew2"]) $(id).value = "";
};
$("setLockMinutes").onchange = (e) => saveSetting({ lock_minutes: Number(e.target.value) });
function sceneCard(scene) {
  const card = document.createElement("div");
  card.className = "scene-card";
  const name = Object.assign(document.createElement("input"), { className: "mini-input", value: scene.name || "", placeholder: "名字,例如 睡觉" });
  const triggers = Object.assign(document.createElement("input"), { className: "mini-input wide", value: (scene.triggers || []).join(", "), placeholder: "说法,例如 我要睡了, 晚安" });
  const steps = Object.assign(document.createElement("textarea"), { className: "scene-steps", value: scene.steps || "", rows: 4, placeholder: "说 晚安,老板\n静音\n锁屏" });
  const row = document.createElement("div");
  row.className = "set-ctl";
  const button = (label, onclick) => Object.assign(document.createElement("button"), { type: "button", className: "mini-btn", textContent: label, onclick });
  row.append(
    button("保存", () => saveSetting({ scene_save: { name: name.value, triggers: triggers.value, steps: steps.value, old_name: scene.name || "" } }, 4000)),
    button("试一下", () => scene.name ? saveSetting({ scene_run: scene.name }, 5000) : toast("先保存")),
    button("删除", () => { if (!scene.name) { card.remove(); return; } if (confirm(`删掉“${scene.name}”场景?`)) saveSetting({ scene_remove: scene.name }); }),
  );
  const head = document.createElement("div");
  head.className = "scene-head";
  head.append(name, triggers);
  card.append(head, steps, row);
  return card;
}
$("sceneAdd").onclick = () => { const card = sceneCard({}); $("sceneList").append(card); card.querySelector("input").focus(); };
function sizeText(n) { return n >= 1073741824 ? (n / 1073741824).toFixed(1) + "GB" : Math.round(n / 1048576) + "MB"; }
function updatePicked() {
  const boxes = [...document.querySelectorAll("#cleanupList input:checked")];
  const total = boxes.reduce((sum, b) => sum + Number(b.dataset.size), 0);
  $("cleanupPicked").textContent = boxes.length ? `选了 ${boxes.length} 个,共 ${sizeText(total)}` : "勾选要丢掉的文件";
}
function showCleanup(items) {
  const list = $("cleanupList");
  list.replaceChildren();
  for (const item of items || []) {
    const row = document.createElement("label");
    row.className = "tidy-item";
    const box = Object.assign(document.createElement("input"), { type: "checkbox", value: item.id });
    box.dataset.size = item.size;
    box.onchange = updatePicked;
    const text = document.createElement("span");
    const name = item.path.split(/[\\/]/).pop();
    text.textContent = `${name}(${sizeText(item.size)})` + (item.kind === "dup" ? ` —— 和 ${item.keep} 一模一样` : "") + `\n${item.path}`;
    text.style.whiteSpace = "pre-wrap";
    row.append(box, text);
    list.append(row);
  }
  $("cleanupActions").hidden = !(items || []).length;
  updatePicked();
}
$("photoReindex").onclick = () => saveSetting({ photo_reindex: true });
$("setBillFile").onchange = (e) => saveSetting({ bill_file: e.target.checked });
$("setSelfReview").onchange = (e) => saveSetting({ self_review: e.target.checked });
$("reviewNow").onclick = () => saveSetting({ self_review_now: true });
$("ruleAdd").onclick = () => { const t = $("ruleText").value.trim(); if (t) { $("ruleText").value = ""; saveSetting({ homerule_add: t }); } };
$("setSpendAlerts").onchange = (e) => saveSetting({ spend_alerts: e.target.checked });
$("spendBudgetSave").onclick = () => saveSetting({ spend_budget: Number($("setSpendBudget").value || 0) });
$("setPracticeGoal").onchange = (e) => saveSetting({ practice_goal: Number(e.target.value) });
$("setPracticeRemind").onchange = (e) => saveSetting({ practice_remind: e.target.value });
$("practiceAppsSave").onclick = () => saveSetting({ practice_apps: $("setPracticeApps").value });

// 节拍器:窗口用声音打拍子(第一拍高一点)
let metro = null;
function metronome(ev) {
  if (metro) { clearInterval(metro.timer); metro.box.remove(); metro = null; }
  if (!ev.bpm) return;
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const beats = Math.max(1, Math.min(9, ev.beats || 4));
  let count = 0;
  const box = document.createElement("div");
  box.className = "metro-chip";
  const dots = document.createElement("span");
  const stop = document.createElement("button");
  stop.type = "button"; stop.textContent = "停";
  stop.onclick = () => metronome({ bpm: 0 });
  box.append(`🥁 ${ev.bpm} BPM `, dots, stop);
  document.body.append(box);
  const tick = () => {
    const first = count % beats === 0;
    const osc = ctx.createOscillator(), gain = ctx.createGain();
    osc.frequency.value = first ? 1500 : 1000;
    gain.gain.setValueAtTime(0.4, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.06);
    osc.connect(gain).connect(ctx.destination);
    osc.start(); osc.stop(ctx.currentTime + 0.07);
    dots.textContent = Array.from({ length: beats }, (_, i) => (i === count % beats ? "●" : "○")).join("") + " ";
    count++;
  };
  tick();
  metro = { timer: setInterval(tick, 60000 / ev.bpm), box };
}
$("saveStmtPw").onclick = () => {  // 多个密码用 | 分开(输入框只有一行)
  const value = $("setStmtPw").value.split("|").join("\n");
  $("setStmtPw").value = "";
  saveSetting({ statement_passwords: value });
};
$("billFolderSave").onclick = () => saveSetting({ bill_folder: $("setBillFolder").value.trim() });
$("billUndo").onclick = () => saveSetting({ bill_undo: true });
$("photoSort").onclick = () => {
  const folder = $("photoFolder").value.trim();
  if (!confirm(`把${folder ? "“" + folder + "”" : "“图片”文件夹"}最上层的照片和视频,按拍摄月份分进“2026-11”这样的子文件夹?整理错了可以撤销。`)) return;
  saveSetting({ sort_photos: folder }, 6000);
};
$("steamTest").onclick = () => saveSetting({ steam_test: true }, 8000);
$("setWishlist").onchange = (e) => saveSetting({ wishlist_on: e.target.checked });
$("setWishlistMin").onchange = (e) => saveSetting({ wishlist_min: Number(e.target.value) });
$("wishlistNow").onclick = () => saveSetting({ wishlist_now: true }, 9000);
$("steamSave").onclick = () => {
  const change = { steam_id: $("steamId").value.trim() };
  if ($("steamKey").value.trim()) change.steam_key = $("steamKey").value.trim();
  $("steamKey").value = "";
  saveSetting(change, 6000);
};
$("apexSave").onclick = () => {
  const change = { apex_player: $("apexPlayer").value.trim(), apex_platform: $("apexPlatform").value };
  if ($("apexKey").value.trim()) change.apex_key = $("apexKey").value.trim();
  $("apexKey").value = "";
  saveSetting(change, 6000);
};
$("tgSave").onclick = () => {
  const value = $("tgToken").value.trim();
  if (!value && !confirm("钥匙是空的:要断开 Telegram 吗?")) return;
  $("tgToken").value = "";
  saveSetting({ telegram_token: value }, 9000);
};
$("tapoSave").onclick = () => {
  const change = { tapo_user: $("tapoUser").value.trim() };
  if ($("tapoPass").value) change.tapo_password = $("tapoPass").value;
  $("tapoPass").value = "";
  saveSetting(change, 8000);
};
$("setOrb").onchange = (e) => saveSetting({ floatorb: e.target.checked }, 5000);
$("setDiary").onchange = (e) => saveSetting({ diary_time: e.target.value });
$("tgPair").onclick = () => saveSetting({ telegram_pair: true }, 8000);
$("spotifySave").onclick = () => saveSetting({ spotify_client_id: $("spotifyClient").value.trim() }, 5000);
$("spotifyLogin").onclick = () => {
  const connected = settingsState && settingsState.apps && settingsState.apps.spotify_connected;
  if (connected) { if (confirm("断开 Spotify?")) saveSetting({ spotify_logout: true }); }
  else saveSetting({ spotify_login: true }, 6000);
};
$("fbSave").onclick = () => saveSetting({ folder_backup_set: {
  sources: $("fbSources").value.split("\n").map((x) => x.trim()).filter(Boolean), dest: $("fbDest").value.trim() } }, 6000);
$("fbRun").onclick = () => saveSetting({ folder_backup_run: true }, 5000);
$("fbForce").onclick = () => {
  if (confirm("确定这些文件是你自己改的(不是中了病毒)?照样备份会把改过的文件存进备份,旧的那份移到“_旧版本”里。")) saveSetting({ folder_backup_force: true }, 5000);
};
$("setClip").onchange = (e) => saveSetting({ clipboard: e.target.checked });
$("clipClear").onclick = () => { if (confirm("清空剪贴板历史?")) saveSetting({ clipboard_clear: true }); };
$("virusQuick").onclick = () => saveSetting({ virus_scan: "quick" }, 5000);
$("virusFull").onclick = () => {
  if (!confirm("全面扫描要一两个小时甚至更久,这段时间电脑会慢一点。扫完会告诉你,也会推到手机。要开始吗?")) return;
  saveSetting({ virus_scan: "full" }, 6000);
};
$("photoUndo").onclick = () => saveSetting({ undo_sort_photos: true }, 5000);
$("cleanupScan").onclick = async () => {
  const btn = $("cleanupScan"); btn.disabled = true; btn.textContent = "找中…(一两分钟)";
  const r = await api("/api/settings", { cleanup_scan: true }).catch(() => ({ error: "星期五没有响应" }));
  btn.disabled = false; btn.textContent = "找一找";
  if (r.error) { toast(r.error, 4000); return; }
  $("cleanupNote").hidden = false;
  $("cleanupNote").textContent = r.message;
  showCleanup(r.items);
};
$("cleanupRecycle").onclick = () => {
  const ids = [...document.querySelectorAll("#cleanupList input:checked")].map((b) => Number(b.value));
  if (!ids.length) { toast("先勾选要丢掉的文件"); return; }
  if (!confirm(`把 ${ids.length} 个文件放进回收站?(还能从回收站拿回来)`)) return;
  $("cleanupList").replaceChildren();
  saveSetting({ cleanup_recycle: ids }, 5000);
};
$("memtidyRun").onclick = async () => {
  const btn = $("memtidyRun"); btn.disabled = true; btn.textContent = "整理中…";
  await saveSetting({ memtidy_run: true }, 5000);
  btn.disabled = false; btn.textContent = "现在整理";
};
$("memtidyApply").onclick = () => {
  const ids = [...document.querySelectorAll("#memtidyList input:checked")].map((b) => Number(b.value));
  saveSetting({ memtidy_apply: ids }, 5000);
};
$("memtidyDismiss").onclick = () => saveSetting({ memtidy_dismiss: true });
$("discordPair").onclick = () => saveSetting({ discord_pair: true }, 8000);
$("saveKeyExpires").onclick = () => saveSetting({ key_expires: $("setKeyExpires").value });
// 星期五的文件夹:复制路径、打开;做一份给代码审查用的干净副本
$("appDirCopy").onclick = async () => {
  const path = settingsState?.app_folder?.path || "";
  toast(path && (await copyText(path)) ? "复制好了:" + path : "复制失败");
};
$("appDirOpen").onclick = () => saveSetting({ app_folder: "open" });
$("reviewCopy").onclick = async () => {
  $("reviewCopy").disabled = true;
  $("reviewCopy").textContent = "正在做…";
  await saveSetting({ app_folder: "review_copy" }, 9000);
  $("reviewCopy").disabled = false;
  $("reviewCopy").textContent = "再做一份";
};
$("reviewCopyOpen").onclick = () => saveSetting({ app_folder: "open_copy" });
$("setRain").onchange = (e) => saveSetting({ rain_alert: e.target.value });
$("setBrief").onchange = (e) => saveSetting({ brief: e.target.checked });
$("setEventLead").onchange = (e) => saveSetting({ event_lead: Number(e.target.value) });
$("setDepartOn").onchange = (e) => saveSetting({ depart_on: e.target.checked });
$("setDepartExtra").onchange = (e) => saveSetting({ depart_extra: Number(e.target.value) });
$("departSave").onclick = () => saveSetting({ depart_home: $("setDepartHome").value.trim() });
$("mailSave").onclick = () => saveSetting({ mail_alert: { senders: $("mailSenders").value, words: $("mailWords").value,
                                                           important: $("mailImportant").checked } }, 5000);
$("mailImportant").onchange = () => $("mailSave").click();
$("setMailDraft").onchange = (e) => saveSetting({ mail_draft: e.target.checked });
$("setWeekly").onchange = (e) => saveSetting({ weekly_report: e.target.checked });
$("setQuick").onchange = (e) => saveSetting({ quick: e.target.checked });
$("setRemindCalendar").onchange = (e) => saveSetting({ remind_calendar: e.target.checked });
$("setGameQuiet").onchange = (e) => saveSetting({ game_quiet: e.target.checked });
$("setGameBoost").onchange = (e) => saveSetting({ game_boost: e.target.checked });
$("setGameReport").onchange = (e) => saveSetting({ game_report: e.target.checked });
for (const [select, button, field] of [["setPtt", "pttCapture", "ptt_key"], ["setShot", "shotCapture", "shot_key"]]) {
  $(select).onchange = (e) => {
    if (e.target.value === "custom") { $(button).hidden = false; startKeyCapture(button, field); return; }
    $(button).hidden = true;
    saveSetting({ [field]: e.target.value });
  };
  $(button).onclick = () => startKeyCapture(button, field);
}

// 自定义按住说话的键:按下“按这里”后,下一个按下的键盘键或鼠标键(中键、侧键)就是它
const VK_NAMED = {
  Backquote: [0xC0, "` 键"], Minus: [0xBD, "-"], Equal: [0xBB, "="], BracketLeft: [0xDB, "["], BracketRight: [0xDD, "]"],
  Backslash: [0xDC, "\\"], Semicolon: [0xBA, ";"], Quote: [0xDE, "'"], Comma: [0xBC, ","], Period: [0xBE, "."], Slash: [0xBF, "/"],
  Tab: [0x09, "Tab"], CapsLock: [0x14, "Caps Lock"], ShiftLeft: [0xA0, "左 Shift"], ShiftRight: [0xA1, "右 Shift"],
  ControlLeft: [0xA2, "左 Ctrl"], ControlRight: [0xA3, "右 Ctrl"], AltLeft: [0xA4, "左 Alt"], AltRight: [0xA5, "右 Alt"],
  Space: [0x20, "空格"], Insert: [0x2D, "Insert"], Delete: [0x2E, "Delete"], Home: [0x24, "Home"], End: [0x23, "End"],
  PageUp: [0x21, "Page Up"], PageDown: [0x22, "Page Down"], ArrowLeft: [0x25, "←"], ArrowUp: [0x26, "↑"],
  ArrowRight: [0x27, "→"], ArrowDown: [0x28, "↓"], Pause: [0x13, "Pause"], ScrollLock: [0x91, "Scroll Lock"],
  NumpadMultiply: [0x6A, "小键盘 *"], NumpadAdd: [0x6B, "小键盘 +"], NumpadSubtract: [0x6D, "小键盘 -"],
  NumpadDecimal: [0x6E, "小键盘 ."], NumpadDivide: [0x6F, "小键盘 /"], NumpadEnter: [0x0D, "小键盘 Enter"],
  Enter: [0x0D, "Enter"], Backspace: [0x08, "Backspace"], ContextMenu: [0x5D, "菜单键"], PrintScreen: [0x2C, "Print Screen"], IntlBackslash: [0xE2, "\\"],
};
function keyToVk(e) {
  const code = e.code || "";
  let m;
  if ((m = /^Key([A-Z])$/.exec(code))) return [m[1].charCodeAt(0), m[1]];
  if ((m = /^Digit([0-9])$/.exec(code))) return [0x30 + Number(m[1]), m[1]];
  if ((m = /^F([0-9]{1,2})$/.exec(code)) && Number(m[1]) >= 1 && Number(m[1]) <= 24) return [0x6F + Number(m[1]), code];
  if ((m = /^Numpad([0-9])$/.exec(code))) return [0x60 + Number(m[1]), `小键盘 ${m[1]}`];
  return VK_NAMED[code] || null;
}
const MOUSE_VK = { 1: [0x04, "鼠标中键"], 3: [0x05, "鼠标侧键(后)"], 4: [0x06, "鼠标侧键(前)"] };
let keyCapturing = null;  // 正在等你按键的那个按钮
function startKeyCapture(buttonId, field) {
  if (keyCapturing) return;
  keyCapturing = buttonId;
  const btn = $(buttonId);
  btn.textContent = "请按你要的键……(Esc 取消)";
  btn.classList.add("waiting");
  const finish = (choice) => {
    keyCapturing = null;
    btn.classList.remove("waiting");
    window.removeEventListener("keydown", onKey, true);
    window.removeEventListener("keyup", onKeyUp, true);
    window.removeEventListener("mousedown", onMouse, true);
    window.removeEventListener("auxclick", block, true);
    if (choice) saveSetting({ [field]: { vk: choice[0], label: choice[1] } }).then(() => setTimeout(() => loadState().then(fillSettings), 300));
    else setTimeout(() => loadState().then(fillSettings), 50);
  };
  const block = (e) => { e.preventDefault(); e.stopPropagation(); };
  const onKey = (e) => {
    block(e);
    if (e.code === "Escape") { finish(null); return; }
    const choice = keyToVk(e);
    if (!choice) { btn.textContent = "这个键认不出来,换一个(Esc 取消)"; return; }
    finish(choice);
  };
  const onMouse = (e) => {
    if (e.button === 0 && e.target === btn) return;  // 刚按下“按这里”那一下
    if (e.button === 0 || e.button === 2) { btn.textContent = "鼠标左右键不能用,换一个(Esc 取消)"; block(e); return; }
    block(e);
    if (MOUSE_VK[e.button]) finish(MOUSE_VK[e.button]);
  };
  const onKeyUp = (e) => { if (e.code === "PrintScreen") onKey(e); };  // Print Screen 在网页里常常只有放开那一下
  window.addEventListener("keydown", onKey, true);
  window.addEventListener("keyup", onKeyUp, true);
  window.addEventListener("mousedown", onMouse, true);
  window.addEventListener("auxclick", block, true);
}
$("setAutoUpdate").onchange = (e) => saveSetting({ auto_update: e.target.checked });
$("setPttDuck").onchange = (e) => saveSetting({ ptt_duck: e.target.checked });
$("setSubtitles").onchange = (e) => saveSetting({ subtitles: e.target.checked });
$("subPos").onchange = (e) => saveSetting({ sub_pos: e.target.value });
$("subSize").onchange = (e) => saveSetting({ sub_size: e.target.value });
$("subTime").onchange = (e) => saveSetting({ sub_time: e.target.value });
$("duckTo").onchange = (e) => saveSetting({ duck_to: e.target.value });
$("subPreview").onclick = () => saveSetting({ subtitle_preview: true });
$("labOpen").onclick = () => saveSetting({ lab_open: true });
$("startCheck").onclick = () => saveSetting({ startup_check: true });
$("startClean").onclick = () => { if (confirm("清掉临时文件夹里超过 7 天没用的临时文件?(正在用的不会动)")) saveSetting({ startup_clean: true }, 4500); };
$("setReceipts").onchange = (e) => saveSetting({ receipts_on: e.target.checked });
$("rcptScan").onclick = () => saveSetting({ receipt_scan: true });
$("rcptAll").onclick = () => saveSetting({ receipt_confirm: "all" });
$("rcptNone").onclick = () => { if (confirm("全部忽略?(不会记账)")) saveSetting({ receipt_dismiss: "all" }); };
$("electricRateSave").onclick = () => saveSetting({ electric_rate: $("electricRate").value.trim() });
$("planHoursSave").onclick = () => saveSetting({ plan_hours: $("planHours").value.trim() });
$("planWeek").onclick = () => saveSetting({ plan_propose: "这周" }, 6000);
$("planTomorrow").onclick = () => saveSetting({ plan_propose: "明天" }, 6000);
$("planApply").onclick = () => saveSetting({ plan_apply: true });
$("planCancel").onclick = () => saveSetting({ plan_cancel: true });
$("parcelScan").onclick = () => saveSetting({ parcel_scan: true });
$("subScan").onclick = () => saveSetting({ sub_scan: true }, 5000);
$("subAdd").onclick = async () => {
  const ok = await saveSetting({ sub_add: { name: $("subName").value.trim(), amount: Number($("subAmount").value) || 0,
    currency: $("subCurrency").value, cycle: $("subCycle").value, day: $("subDay").value.trim() } });
  if (ok) { $("subName").value = ""; $("subAmount").value = ""; $("subDay").value = ""; }
};
$("meetStart").onclick = () => saveSetting({ meeting_start: $("meetTitle").value.trim() });
$("meetStop").onclick = () => saveSetting({ meeting_stop: true });
$("meetTodos").onclick = () => saveSetting({ meeting_todos: true });
$("meetingChip").onclick = () => { if (confirm("停止记录?停止后我会整理出重点和待办。")) saveSetting({ meeting_stop: true }); };
$("setLibOn").onchange = (e) => saveSetting({ library_on: e.target.checked });
$("libSave").onclick = () => saveSetting({ library_folders: $("libFolders").value.split("\n").map((x) => x.trim()).filter(Boolean) });
$("libRefresh").onclick = () => saveSetting({ library_refresh: true });
$("bgCancel").onclick = () => saveSetting({ bg_cancel: true });
$("bgOpen").onclick = () => saveSetting({ bg_open: true });
$("setLearn").onchange = (e) => saveSetting({ learn: e.target.checked });
$("setSttMixed").onchange = (e) => saveSetting({ stt_mixed: e.target.checked });
$("setSttEngine").onchange = (e) => saveSetting({ stt_engine: e.target.value }, 6000);
$("vocabWordsSave").onclick = () => saveSetting({ vocab_words: $("vocabWords").value });
$("vocabAdd").onclick = async () => {
  if (await saveSetting({ vocab_add: { heard: $("vocabHeard").value, meant: $("vocabMeant").value } })) {
    $("vocabHeard").value = ""; $("vocabMeant").value = "";
  }
};

// 家里的灯联动:开游戏时调灯、离开时关灯
function picks(box, devices, chosen, onlyLights) {
  if (box.contains(document.activeElement)) return;
  box.replaceChildren(...devices.filter((d) => !onlyLights || d.kind === "light").map((d) => {
    const label = document.createElement("label");
    label.className = "pick";
    const input = document.createElement("input");
    input.type = "checkbox"; input.value = d.name; input.checked = chosen.includes(d.name);
    input.onchange = saveLightLink;
    label.append(input, document.createTextNode((d.kind === "light" ? "💡 " : "🔌 ") + d.name));
    return label;
  }));
  if (!box.children.length) box.textContent = "还没找到设备";
}
function fillLightLink(link) {
  $("lightGroup").hidden = !link;
  if (!link) return;
  const mode = $("lightGameMode");
  if (!mode.options.length) for (const [k, v] of link.modes) mode.append(new Option(v, k));
  const minutes = $("lightAwayMin");
  if (!minutes.options.length) for (const m of link.minutes) minutes.append(new Option(m ? `${m} 分钟` : "不关", String(m)));
  mode.value = link.game_mode; minutes.value = String(link.away_minutes); $("lightBack").checked = link.back;
  picks($("lightGameList"), link.devices, link.game, link.game_mode !== "off");
  picks($("lightAwayList"), link.devices, link.away, false);
}
function checked(box) { return [...box.querySelectorAll("input:checked")].map((i) => i.value); }
function saveLightLink() {
  saveSetting({ lightlink: { game_mode: $("lightGameMode").value, game: checked($("lightGameList")),
    away_minutes: Number($("lightAwayMin").value), away: checked($("lightAwayList")), back: $("lightBack").checked } });
}
function fillParty(p) {
  $("partyGroup").hidden = !p;
  if (!p || $("partyGroup").contains(document.activeElement)) return;
  $("partyApps").value = p.apps; $("partyGame").value = p.game; $("partyPlaylist").value = p.playlist;
  const sel = $("partyLights");
  if (!sel.options.length) for (const [k, v] of p.modes) sel.append(new Option(v, k));
  sel.value = p.lights;
  $("partyLightsRow").hidden = !p.has_lights;
  $("partyQuiet").checked = p.quiet; $("partyClose").checked = p.close;
}
$("partySave").onclick = () => saveSetting({ party: {
  apps: $("partyApps").value, game: $("partyGame").value.trim(), playlist: $("partyPlaylist").value.trim(),
  lights: $("partyLights").value || "none", quiet: $("partyQuiet").checked, close: $("partyClose").checked } });
$("lightGameMode").onchange = saveLightLink;
$("lightAwayMin").onchange = saveLightLink;
$("lightBack").onchange = saveLightLink;
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
let reader = null;  // 读给我听:{id, rate, next, total, queue, fetching, started, playing}
let readerPlaying = false;
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
let speechRate = 1;  // 朗读速度(齿轮设定 / “读快一点”),回答和读文章一样
let audioPaused = false;
let audioTurn = null;
let ignoreTurn = -1;  // 被打断的那次回答,后面陆续到的语音也不读了
let levelLoop = false;
function enqueueAudio(url, turn) {
  if (!speakOn) return;
  if (readerPlaying) stopReaderLocal();  // 有新的话要说:先停下朗读(说“继续”接着读)
  if (turn !== undefined && turn <= ignoreTurn) return;
  if (turn !== undefined && turn !== audioTurn) { stopSpeaking(); audioTurn = turn; }
  audioQueue.push(url);
  if (!speaking && !audioPaused) playNext();  // 暂停着:先排着,说“继续”再播
}
// 你开口(按麦克风、说话打断、打字送出)时:回答先暂停、记住读到哪,告诉星期五;
// 你说“继续”就接着读,“别读了”就丢掉,问别的她会叫窗口丢掉旧的再回答
function pauseSpeaking() {
  if (reader || readerPlaying) { stopSpeaking(); return false; }  // 读文章:停在那一段,说“继续”接着读(原来的做法)
  if (!speaking) return audioPaused;
  audioEl.pause();
  audioPaused = true;
  speaking = false; orbState.level = 0; stopBargeWatch(); refreshMode();
  api("/api/command", { command: "speech_paused" }).catch(() => {});
  return true;
}
function resumeSpeaking(tell = false) {
  if (!audioPaused) return;
  audioPaused = false;
  speaking = true; refreshMode();
  if (tell) api("/api/command", { command: "speech_resumed" }).catch(() => {});
  if (audioEl.src && !audioEl.ended && audioEl.currentTime > 0) {
    audioEl.play().then(startBargeWatch).catch(() => playNext());
  } else {
    playNext();
  }
}
function stopSpeaking() {
  audioPaused = false;
  if (reader || readerPlaying) stopReaderLocal();
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
  speaking = true; audioPaused = false; refreshMode();
  audioEl.src = `${url}?t=${encodeURIComponent(token)}`;
  audioEl.defaultPlaybackRate = speechRate; audioEl.playbackRate = speechRate;
  audioEl.play().then(() => {
    startBargeWatch();
    if (levelLoop) return;
    levelLoop = true;
    const loop = () => {
      if (!speaking) { levelLoop = false; return; }
      orbState.level = Math.min(1, rms(analyser) * 4.5); sendLevel(orbState.level);
      requestAnimationFrame(loop);
    };
    loop();
  }).catch(() => { audioQueue.length = 0; speaking = false; refreshMode(); toast("点一下页面,才能播放声音"); });
}
audioEl.onended = audioEl.onerror = () => { if (readerPlaying) readerPlayNext(); else if (speaking) playNext(); };

// ---------- 读给我听:一段一段向星期五要声音,播完一段接下一段 ----------
function readerEvent(ev) {
  if (ev.kind === "rate") {
    if (reader && reader.id === ev.id) { reader.rate = ev.rate; if (readerPlaying) audioEl.playbackRate = ev.rate; }
    return;
  }
  if (ev.state === "reading") {
    stopReaderLocal();
    reader = { id: ev.id, rate: ev.rate || 1, next: ev.index || 0, total: ev.total || 0, queue: [], fetching: false,
               started: false, playing: ev.index || 0 };
    tryStartReader();
  } else if (reader && reader.id === ev.id) {
    stopReaderLocal();
  }
}
function stopReaderLocal() {
  if (readerPlaying) {
    readerPlaying = false;
    audioEl.pause();
    audioEl.playbackRate = speechRate;
    speaking = false; orbState.level = 0; stopBargeWatch(); refreshMode();
  }
  reader = null;
}
function tryStartReader() {
  const r0 = reader;
  if (!r0 || r0.started) return;
  if (busy || speaking || recorder) { setTimeout(tryStartReader, 600); return; }  // 等她先把话说完
  if (!speakOn) { toast("朗读开关关着,打开下面的喇叭按钮才听得到", 4000); reader = null; return; }
  r0.started = true;
  readerFill();
}
async function readerFill() {
  const r0 = reader;
  if (!r0 || !r0.started || r0.fetching || r0.next >= r0.total || r0.queue.length >= 2) return;
  r0.fetching = true;
  const index = r0.next;
  const r = await api("/api/reader", { action: "chunk", id: r0.id, index, playing: r0.playing }).catch(() => null);
  r0.fetching = false;
  if (reader !== r0) return;
  if (!r || r.stop) { stopReaderLocal(); return; }
  r0.next = r.done ? r0.total : index + 1;
  if (r.url) r0.queue.push({ url: r.url, index });
  if (!readerPlaying) readerPlayNext();
  readerFill();
}
function readerPlayNext() {
  const r0 = reader;
  if (!r0) return;
  const item = r0.queue.shift();
  if (!item) {
    readerPlaying = false; speaking = false; orbState.level = 0; stopBargeWatch(); refreshMode();
    if (r0.next >= r0.total && !r0.fetching) {  // 读完了
      api("/api/reader", { action: "finished", id: r0.id }).catch(() => {});
      reader = null;
    }
    return;
  }
  r0.playing = item.index;
  ensureAudio();
  readerPlaying = true; speaking = true; refreshMode();
  audioEl.src = `${item.url}?t=${encodeURIComponent(token)}`;
  audioEl.playbackRate = r0.rate;
  audioEl.play().then(() => {
    startBargeWatch();  // 读的时候直接开口(“暂停”“跳过”)就会停下来听
    if (levelLoop) return;
    levelLoop = true;
    const loop = () => {
      if (!speaking) { levelLoop = false; return; }
      orbState.level = Math.min(1, rms(analyser) * 4.5); sendLevel(orbState.level);
      requestAnimationFrame(loop);
    };
    loop();
  }).catch(() => { stopReaderLocal(); toast("点一下页面,才能播放声音"); });
  readerFill();
}
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
  if (!spoke) pauseSpeaking();
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
      orbState.level = Math.min(1, level * 6); sendLevel(orbState.level);
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
    if (!spoke) { if (!followUp) toast("没听到声音"); resumeSpeaking(true); return; }  // 你没说话:接着读
    const pendingLine = addLine("识别中……");
    const r = await api("/api/voice", new Blob(chunks, { type: type || "audio/webm" }), { "X-Speak": speakOn ? "1" : "0" });
    pendingLine.remove(); // 识别完了(听清了会显示你说的话,没听清会提示),这一行不用留着
    if (!r.text) { toast(r.error ? `语音识别失败:${r.error}` : "没听清,再说一次?", 3500); resumeSpeaking(true); }
  };
  recorder.start(250);
  watch();
}
$("micBtn").onclick = () => (recorder ? recorder.stop() : listen());

// ---------- 说话打断她:她在说话时你一开口,她就停下来听你说 ----------
let bargeOn = loadPrefs("friday-barge", true);
async function startBargeWatch() {
  if (!bargeOn || bargeStream || recorder || !audioCtx) return;
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  } catch { return; }
  if (!speaking || recorder || bargeStream) { stream.getTracks().forEach((t) => t.stop()); return; }
  bargeStream = stream;
  refreshMode();
  const an = audioCtx.createAnalyser();
  an.fftSize = 1024;
  audioCtx.createMediaStreamSource(stream).connect(an);
  // 她自己的声音会从喇叭漏回麦克风:一边听一边学“漏回来有多大”,只有比漏回来的大很多才算你在说话。
  // (以前只在开头 0.4 秒量一次,语音开头常是静音,量到的几乎是 0,念到大声的地方就以为你在说话,读到一半停下来)
  let floor = 0.008, leak = 0, echo = 0, heard = 0, loud = 0;
  // 用定时器而不是动画帧:窗口被挡住时动画帧会暂停,定时器还会继续
  const timer = setInterval(() => {
    if (bargeStream !== stream) { clearInterval(timer); return; }
    if (!speaking) { clearInterval(timer); stopBargeWatch(); return; }
    const level = rms(an);
    const out = analyser ? rms(analyser) : 0;
    echo = Math.max(out, echo * 0.93);  // 她最近约 0.3 秒的音量(房间回音也算进去)
    if (heard < BARGE_LEARN) {  // 先学:她念了约 0.6 秒以后才开始判断
      if (echo > 0.02) { heard++; leak = Math.max(leak, level / echo); } else floor = Math.max(floor, level);
      return;
    }
    const limit = Math.max(BARGE_MIN, floor * BARGE_RATIO, Math.min(leak, 4) * echo * BARGE_RATIO);
    if (level > limit) {
      if (++loud >= BARGE_FRAMES) {  // 大约 0.25 秒都比她漏回来的声音大很多:是你在说话
        clearInterval(timer);
        bargeStream = null;
        refreshMode();
        pauseSpeaking();
        addLine("🎙 好像听到你在说话,先停下来听你说(不是你的话:齿轮设定 → 声音 关掉“说话可以打断她”,或戴耳机)");
        listen({ stream, spoke: true });
      }
    } else {
      loud = Math.max(0, loud - 1);
      if (echo > 0.02) leak = Math.max(leak * 0.9995, level / echo);  // 慢慢跟着调(换音量、换喇叭)
      else floor = Math.max(floor * 0.999, level);
    }
  }, 30);
}
// 门槛:开了回声消除和降噪后,正常说话的音量常常只有 0.03~0.1
const BARGE_MIN = 0.025, BARGE_RATIO = 1.8, BARGE_FRAMES = 8, BARGE_LEARN = 20;
function stopBargeWatch() {
  if (bargeStream) { bargeStream.getTracks().forEach((t) => t.stop()); bargeStream = null; refreshMode(); }
}

// ---------- 点光球:开始说话(再点一下结束) ----------
orbState.onOrbClick = () => { orbState.pulse && orbState.pulse(1.2); return recorder ? recorder.stop() : listen(); };
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

// ---------- 窗口锁 ----------
let isLocked = false;
function showLock() {
  if (isLocked) return;
  isLocked = true;
  if (source) { source.close(); source = null; }
  try { if (recorder && recorder.state !== "inactive") recorder.stop(); } catch {}
  stopSpeaking();
  closeSettings();
  log.innerHTML = ""; current = null;
  for (const id of ["memoryList", "reminderList", "todoList"]) $(id).innerHTML = "";
  document.body.classList.add("locked");
  $("lockScreen").hidden = false;
  $("lockError").textContent = "";
  $("lockPin").value = "";
  $("lockPin").focus();
}
async function tryUnlock() {
  const pin = $("lockPin").value.trim();
  if (!pin) return;
  const r = await api("/api/unlock", { pin }).catch(() => ({ error: "星期五没有响应" }));
  $("lockPin").value = "";
  if (!r.ok) { $("lockError").textContent = r.error || "PIN 不对"; $("lockPin").focus(); return; }
  isLocked = false;
  $("lockScreen").hidden = true;
  document.body.classList.remove("locked");
  await loadState();
  connect();
}
$("unlockBtn").onclick = tryUnlock;
$("lockPin").addEventListener("keydown", (e) => { if (e.key === "Enter") tryUnlock(); });
$("lockBtn").onclick = async () => {
  const r = await api("/api/lock", {}).catch(() => ({ error: "星期五没有响应" }));
  if (r.error) toast(r.error, 4000); else showLock();
};

// ---------- 仪表板:天气、电脑、今天、正在播放、家里 ----------
const WEATHER_ICON = (code) => {
  const c = Number(code);
  if (c === 0) return "☀️";
  if (c <= 2) return "🌤️";
  if (c === 3) return "☁️";
  if (c <= 48) return "🌫️";
  if (c <= 57 || (c >= 80 && c <= 82)) return "🌦️";
  if (c <= 67) return "🌧️";
  if (c <= 86) return "🌨️";
  return "⛈️";
};
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}
let dashMusic = null;
let dashMusicAt = 0;
function renderWeather(w) {
  $("dashWeather").hidden = !w;
  if (!w) return;
  $("weatherPlace").textContent = w.place || "";
  const box = $("weatherBox");
  const now = el("div", "wx-now");
  now.append(el("span", "wx-icon", WEATHER_ICON(w.code)), el("span", "wx-temp", `${Math.round(w.temp)}°`));
  const info = el("div", "wx-info");
  info.append(el("div", "wx-text", w.text || ""),
    el("div", "wx-meta", `体感 ${Math.round(w.feels)}° · ${Math.round(w.low)}°~${Math.round(w.high)}° · 降雨 ${w.rain ?? 0}%`));
  now.append(info);
  const hours = el("div", "wx-hours");
  for (const h of w.hours || []) {
    const cell = el("div", "wx-hour" + (h.rain >= 50 ? " wet" : ""));
    cell.title = `${h.time} 降雨机会 ${h.rain}%`;
    cell.append(el("span", "", h.time), el("span", "wx-hicon", WEATHER_ICON(h.code)), el("b", "", `${Math.round(h.temp)}°`),
      el("small", "", `${h.rain}%`));
    hours.append(cell);
  }
  box.replaceChildren(now, hours);
}
function meter(label, value, text, warn, hot) {
  const row = el("div", "meter" + (value >= hot ? " hot" : value >= warn ? " warm" : ""));
  const bar = el("div", "meter-bar");
  const fill = el("i");
  fill.style.width = `${Math.max(2, Math.min(100, value || 0))}%`;
  bar.append(fill);
  row.append(el("span", "meter-label", label), bar, el("span", "meter-value", text));
  return row;
}
function renderPc(p) {
  $("dashPc").hidden = !p;
  if (!p) return;
  const rows = [];
  if (p.cpu !== null && p.cpu !== undefined) rows.push(meter("CPU", p.cpu, `${p.cpu}%`, 70, 90));
  if (p.mem !== undefined) rows.push(meter("内存", p.mem, `${p.mem_used}/${p.mem_total} GB`, 80, 92));
  if (p.cpu_temp) rows.push(meter("CPU 温度", p.cpu_temp, `${Math.round(p.cpu_temp)}°C`, 82, 92));
  if (p.gpu_temp) rows.push(meter("显卡温度", p.gpu_temp, `${Math.round(p.gpu_temp)}°C`, 75, 85));
  $("pcBox").replaceChildren(...rows);
}
function renderToday(items) {
  $("dashToday").hidden = !items;
  if (!items) return;
  $("todayCount").textContent = items.length ? `(${items.length})` : "";
  const list = $("todayList");
  if (!items.length) { list.replaceChildren(el("li", "empty", "今天没有别的安排了")); return; }
  list.replaceChildren(...items.map((i) => {
    const li = el("li", "today-item " + i.kind);
    li.append(el("span", "today-time", i.time), el("span", "today-title", (i.kind === "event" ? "📅 " : "⏰ ") + i.title));
    return li;
  }));
}
function renderMusic(m) {
  $("dashMusic").hidden = !m;
  if (!m) return;
  dashMusic = m; dashMusicAt = Date.now();
  const idle = !!m.idle;
  $("musicBox").classList.toggle("idle", idle);
  $("musicTitle").textContent = idle ? "没在播放" : m.title;
  $("musicArtist").textContent = idle ? "说“放一首歌”就会开始" : m.artist + (m.device ? ` · ${m.device}` : "");
  $("musicArt").hidden = idle || !m.art;
  if (!idle && m.art && $("musicArt").src !== m.art) $("musicArt").src = m.art;
  $("musicPlayIcon").setAttribute("d", m.playing ? "M7 5h4v14H7zM13 5h4v14h-4z" : "M8 5v14l11-7z");
  $("musicPlay").title = m.playing ? "暂停" : "播放";
  renderMusicProgress();
}
function renderMusicProgress() {
  const m = dashMusic;
  if (!m || m.idle || !m.duration) { $("musicProgress").style.width = "0"; return; }
  const now = m.progress + (m.playing ? Date.now() - dashMusicAt : 0);
  $("musicProgress").style.width = `${Math.min(100, now / m.duration * 100)}%`;
}
setInterval(renderMusicProgress, 1000);
function renderHome(devices) {
  $("dashHome").hidden = !devices || !devices.length;
  if (!devices || !devices.length) return;
  $("homeCount").textContent = `(${devices.filter((d) => d.on).length}/${devices.length} 开着)`;
  $("homeBox").replaceChildren(...devices.map((d) => {
    const b = el("button", "home-tile" + (d.on ? " on" : ""));
    b.type = "button";
    b.title = `${d.name}:点一下${d.on ? "关掉" : "打开"}`;
    b.setAttribute("aria-pressed", String(!!d.on));
    b.append(el("span", "home-icon", d.kind === "light" ? "💡" : "🔌"), el("span", "home-name", d.name),
      el("small", "", d.on ? (d.brightness ? `开 · ${d.brightness}%` : "开") : "关"));
    b.onclick = () => dashAct({ action: "light", id: d.id, on: !d.on }, b);
    return b;
  }));
}
function renderDash(d) {
  if (!d || d.error || d.locked) return;
  renderWeather(d.weather); renderPc(d.pc); renderToday(d.today); renderMusic(d.music); renderHome(d.home);
}
async function loadDash() {
  const hud = document.querySelector(".hud-left");
  if (isLocked || document.visibilityState !== "visible" || !hud || getComputedStyle(hud).display === "none") return;
  try { renderDash(await api("/api/dashboard")); } catch {}
}
async function dashAct(payload, button) {
  if (button) button.disabled = true;
  try {
    const r = await api("/api/dashboard", payload);
    if (r.error) toast(r.error);
    else renderDash(r.data);
    if (payload.action === "music") setTimeout(loadDash, 1500);  // Spotify 要一下子才更新
  } finally { if (button) button.disabled = false; }
}
$("musicPrev").onclick = (e) => dashAct({ action: "music", op: "previous" }, e.currentTarget);
$("musicNext").onclick = (e) => dashAct({ action: "music", op: "next" }, e.currentTarget);
$("musicPlay").onclick = (e) => dashAct({ action: "music", op: "play_pause" }, e.currentTarget);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") loadDash(); });

// ---------- 启动 ----------
(async () => {
  setMode("idle");
  const s = await loadState();
  greeting = (s && s.greeting) || "";
  if (!isLocked) connect();
  setInterval(loadState, 30000);
  loadDash();
  setInterval(loadDash, 10000);
  $("input").focus();
})();
