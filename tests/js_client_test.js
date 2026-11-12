/**
 * 浏览器端黑盒测试：给 index.html 的脚本喂一套 DOM/WebSocket 垫片，真跑一遍。
 * 验证：连接 → 房主欢迎 → 点击开始 → 广播状态 → 收到 bot 输入 → 结算展示。
 *
 *     node tools/js_client_test.js
 */
const fs = require("fs");
const path = require("path");

const htmlPath = path.join(__dirname, "..", "web", "index.html");
const html = fs.readFileSync(htmlPath, "utf8");
const m = html.match(/<script>([\s\S]*)<\/script>/);
if (!m) {
  console.error("找不到 <script>");
  process.exit(1);
}
const script = m[1];

let failed = 0;
const ok = (name, cond, extra) => {
  if (cond) console.log(`  [ok] ${name}`);
  else {
    failed++;
    console.log(`  [FAIL] ${name} ${extra === undefined ? "" : JSON.stringify(extra)}`);
  }
};

/* ---------------- DOM 垫片 ---------------- */
function leaf() {
  const el = {
    style: {}, _kids: [], textContent: "", innerHTML: "", value: "", disabled: false,
    _handlers: {},
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); },
      remove(c) { this._s.delete(c); },
      toggle(c, f) {
        if (f === undefined) { this._s.has(c) ? this._s.delete(c) : this._s.add(c); }
        else if (f) this._s.add(c); else this._s.delete(c);
      },
      contains(c) { return this._s.has(c); },
    },
    addEventListener(t, h) { (this._handlers[t] = this._handlers[t] || []).push(h); },
    appendChild(c) { this._kids.push(c); return c; },
    setPointerCapture() {},
  };
  return el;
}

const els = new Map();
function el(id) {
  if (!els.has(id)) {
    const e = leaf();
    e.id = id;
    e.parentElement = leaf();
    e.firstElementChild = leaf();
    els.set(id, e);
  }
  return els.get(id);
}

const ctx2d = new Proxy(
  {
    createLinearGradient: () => ({ addColorStop() {} }),
    measureText: () => ({ width: 10 }),
  },
  {
    get(t, k) {
      if (k in t) return t[k];
      return () => {};
    },
    set() { return true; },
  }
);

const canvas = el("cv");
canvas.getContext = () => ctx2d;

globalThis.document = {
  getElementById: (id) => (id === "cv" ? canvas : el(id)),
  createElement: () => leaf(),
  createTextNode: (txt) => ({ nodeValue: txt, text: txt }),
  body: leaf(),
};
globalThis.innerWidth = 1280;
globalThis.innerHeight = 720;
globalThis.devicePixelRatio = 1;
const gh = {};
globalThis.addEventListener = (t, h) => { (gh[t] = gh[t] || []).push(h); };
globalThis.localStorage = {
  _d: {},
  getItem(k) { return this._d[k] ?? null; },
  setItem(k, v) { this._d[k] = String(v); },
};
globalThis.location = { search: "?room=TEST01", host: "127.0.0.1:8399", protocol: "http:", reload() {}, href: "" };
globalThis.CSS = {};

/* ---------------- WebSocket 垫片 ---------------- */
class FakeWS {
  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.sent = [];
    FakeWS.last = this;
  }
  send(d) { this.sent.push(JSON.parse(d)); }
  close() { this.readyState = 3; if (this.onclose) this.onclose(); }
  open() { this.readyState = 1; if (this.onopen) this.onopen(); }
  push(obj) { if (this.onmessage) this.onmessage({ data: JSON.stringify(obj) }); }
}
globalThis.WebSocket = FakeWS;

/* ---------------- rAF 垫片 ---------------- */
let rafCb = null;
globalThis.requestAnimationFrame = (cb) => { rafCb = cb; return 1; };

/* ---------------- 运行脚本 ---------------- */
try {
  (0, eval)(script);
} catch (e) {
  console.log("脚本初始化抛异常 ❌\n" + e.stack);
  process.exit(1);
}
console.log("0) 脚本初始化");
ok("没有在启动阶段抛异常", true);

const ws = FakeWS.last;
ok("已发起 WS 连接", !!ws && ws.url.includes("/ws?room=TEST01"), ws && ws.url);
ok("连接带了名字参数", !!ws && ws.url.includes("name="), ws && ws.url);

let T = 1000;
function frames(n, stepMs = 16.7) {
  for (let i = 0; i < n; i++) {
    T += stepMs;
    const cb = rafCb;
    rafCb = null;
    if (!cb) throw new Error("主循环没有继续申请下一帧");
    cb(T);
  }
}

console.log("1) 欢迎消息 → 成为房主");
ws.open();
ws.push({
  t: "welcome",
  pid: "h1",
  role: "host",
  room: "TEST01",
  roster: [
    { pid: "h1", name: "房主", role: "host" },
    { pid: "b1", name: "小鱼苗", role: "bot" },
  ],
  max_players: 2,
  started: false,
});
ok("房号已展示", el("roomCode").textContent === "TEST01", el("roomCode").textContent);
ok("房主看到开始按钮", el("startBtn").style.display !== "none");
ok("有 bot 时开始按钮可点", el("startBtn").disabled === false, el("startBtn").disabled);
ok("名单里有 bot", el("roster")._kids.length === 2, el("roster")._kids.length);

console.log("2) 点击开始 → 进入对局");
el("nameInput").value = "房主";
el("startBtn")._handlers.click[0]();
ok("发出了 start", ws.sent.some((x) => x.t === "start"), ws.sent.map((x) => x.t));
ok("大厅已隐藏", el("lobby").classList.contains("hidden"));

frames(12);
const states = ws.sent.filter((x) => x.t === "state");
ok("开始广播状态", states.length > 0, states.length);
const st = states[states.length - 1];
ok("状态里有倒计时 tl", typeof st.tl === "number", st.tl);
ok("状态里有实体", Array.isArray(st.ents) && st.ents.length > 5, st.ents.length);
ok("实体带玩家标记字段 p", "p" in st.ents[0]);
ok("有 bot 实体", st.ents.some((e) => e.p === "b1"), st.ents.filter((e) => e.p).map((e) => e.p));

console.log("3) 收到玩家输入 / 中途加入");
try {
  ws.push({ t: "input", pid: "b1", dx: 0.6, dy: -0.4 });
  ws.push({ t: "peer_join", pid: "g3", name: "阿强", role: "guest" });
  frames(10);
  const after = ws.sent.filter((x) => x.t === "state").pop();
  ok("中途加入的玩家出现在世界里", after.ents.some((e) => e.p === "g3"), after.ents.filter((e) => e.p).map((e) => e.p));
} catch (e) {
  ok("处理输入/加入不抛异常", false, e.message);
}

console.log("4) 打完整局 → 结算");
try {
  // dt 上限 0.1s，每帧最多推进 8 个 tick，用大跨度快进
  let guard = 0;
  while (!ws.sent.some((x) => x.t === "end") && guard++ < 2000) {
    frames(1, 100);
  }
  const endMsg = ws.sent.find((x) => x.t === "end");
  ok("发出了 end", !!endMsg, ws.sent.map((x) => x.t).slice(-3));
  ok("结算里有 players", !!endMsg && Array.isArray(endMsg.result.players) && endMsg.result.players.length >= 2,
     endMsg && endMsg.result && endMsg.result.players && endMsg.result.players.length);
  ok("结算面板已渲染", el("resultRows")._kids.length >= 2, el("resultRows")._kids.length);
  ok("结算标题已设置", !!el("resultTitle").textContent, el("resultTitle").textContent);
  console.log("     标题：" + el("resultTitle").textContent);
  console.log("     排行榜：" + endMsg.result.players.map((p) => `${p.name}=${p.score}`).join("  "));
} catch (e) {
  ok("整局跑完不抛异常", false, e.message);
}

console.log("5) 房主断线 / 房间关闭处理");
try {
  ws.push({ t: "host_left", msg: "房主断线，本局结束" });
  ok("提示了大厅文案", el("lobbySub").textContent.includes("房主断线"), el("lobbySub").textContent);
  ok("出现重连按钮", el("reloadBtn").style.display !== "none");
} catch (e) {
  ok("断线处理不抛异常", false, e.message);
}

console.log(failed === 0 ? "\n前端交互全部通过 ✅" : `\n失败 ${failed} 项 ❌`);
process.exit(failed === 0 ? 0 : 1);
