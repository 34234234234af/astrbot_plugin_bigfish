/**
 * 普通玩家（非房主）路径的黑盒测试：用 vm 起一个干净的上下文跑 index.html 的脚本。
 * 验证：游客身份看不到开始按钮 → 收到状态自动进入对局 → 持续上报操作 → 结算只读。
 *
 *     node tools/js_guest_test.js
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const htmlPath = path.join(__dirname, "..", "web", "index.html");
const script = fs.readFileSync(htmlPath, "utf8").match(/<script>([\s\S]*)<\/script>/)[1];

let failed = 0;
const ok = (name, cond, extra) => {
  if (cond) console.log(`  [ok] ${name}`);
  else {
    failed++;
    console.log(`  [FAIL] ${name} ${extra === undefined ? "" : JSON.stringify(extra)}`);
  }
};

/* ---- 垫片 ---- */
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
  { createLinearGradient: () => ({ addColorStop() {} }), measureText: () => ({ width: 10 }) },
  { get: (t, k) => (k in t ? t[k] : () => {}), set: () => true }
);
const canvas = el("cv");
canvas.getContext = () => ctx2d;

class FakeWS {
  constructor(url) {
    this.url = url; this.readyState = 0; this.sent = []; FakeWS.last = this;
  }
  send(d) { this.sent.push(JSON.parse(d)); }
  close() { this.readyState = 3; if (this.onclose) this.onclose(); }
  open() { this.readyState = 1; if (this.onopen) this.onopen(); }
  push(o) { if (this.onmessage) this.onmessage({ data: JSON.stringify(o) }); }
}

let rafCb = null;
const sandbox = {
  console,
  setTimeout, clearTimeout,
  performance: { now: () => Date.now() },
  document: {
    getElementById: (id) => (id === "cv" ? canvas : el(id)),
    createElement: () => leaf(),
    createTextNode: (t) => ({ nodeValue: t }),
    body: leaf(),
  },
  innerWidth: 1280, innerHeight: 720, devicePixelRatio: 1,
  addEventListener: () => {},
  localStorage: { _d: {}, getItem(k) { return this._d[k] ?? null; }, setItem(k, v) { this._d[k] = String(v); } },
  location: { search: "?room=TEST02", host: "127.0.0.1:8399", protocol: "http:", reload() {}, href: "" },
  WebSocket: FakeWS,
  URLSearchParams,
  requestAnimationFrame: (cb) => { rafCb = cb; return 1; },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(script, sandbox);

console.log("0) 游客端启动");
ok("没有抛异常", true);
const ws = FakeWS.last;
ok("已连接房间 TEST02", !!ws && ws.url.includes("room=TEST02"), ws && ws.url);

let T = 1000;
function frames(n, stepMs = 16.7) {
  for (let i = 0; i < n; i++) {
    T += stepMs;
    const cb = rafCb; rafCb = null;
    if (!cb) throw new Error("主循环停了");
    cb(T);
  }
}

console.log("1) 以游客身份进入");
ws.open();
ws.push({
  t: "welcome", pid: "g2", role: "guest", room: "TEST02",
  roster: [
    { pid: "h1", name: "房主", role: "host" },
    { pid: "g2", name: "我", role: "guest" },
    { pid: "b1", name: "小鱼苗", role: "bot" },
  ],
  max_players: 2, started: false,
});
ok("游客看不到开始按钮", el("startBtn").style.display === "none", el("startBtn").style.display);
ok("提示等待房主", el("lobbySub").textContent.includes("等房主"), el("lobbySub").textContent);
ok("名单显示 3 人", el("roster")._kids.length === 3, el("roster")._kids.length);

console.log("2) 房主开局 → 自动进入对局");
const stateMsg = {
  t: "state", tk: 1, tl: 42.5, w: 1600, h: 900,
  ents: [
    { i: 1, x: 800, y: 400, s: 22, p: "h1", n: "房主", a: 1, r: 0, v: 0 },
    { i: 2, x: 700, y: 300, s: 31, p: "g2", n: "我", a: 1, r: 0, v: 0 },
    { i: 3, x: 900, y: 500, s: 40, p: "b1", n: "小鱼苗", a: 1, r: 0, v: 0 },
    { i: 4, x: 500, y: 500, s: 12, p: "", n: "", a: 1, r: 0, v: 0 },
  ],
  sc: { h1: { s: 120, e: 4, d: 1 }, g2: { s: 260, e: 8, d: 0 } },
};
ws.push(stateMsg);
frames(1);   // HUD 在下一帧刷新
ok("收到状态后自动关掉大厅", el("lobby").classList.contains("hidden"));
ok("我的分数已显示", el("meScore").textContent === 260, el("meScore").textContent);
ok("倒计时显示 43s", el("timer").textContent === "43s", el("timer").textContent);

console.log("3) 持续上报操作");
try {
  frames(40);
  const inputs = ws.sent.filter((x) => x.t === "input");
  ok("有上报方向", inputs.length > 0, inputs.length);
  const bad = inputs.filter((x) => !isFinite(x.dx) || !isFinite(x.dy));
  ok("方向值是有限数", bad.length === 0, bad.slice(0, 2));
  ok("上报频率被限流（<25/s）", inputs.length / (40 * 0.0167) < 25, (inputs.length / (40 * 0.0167)).toFixed(1));
} catch (e) {
  ok("上报流程不抛异常", false, e.message);
}

console.log("4) 结算（游客只读）");
ws.push({
  t: "end",
  result: {
    duration: 90,
    players: [
      { id: "g2", name: "我", score: 520, eaten: 15, deaths: 2 },
      { id: "b1", name: "小鱼苗", score: 310, eaten: 9, deaths: 3 },
    ],
    winner: "g2",
  },
});
ok("结算面板渲染 2 行", el("resultRows")._kids.length === 2, el("resultRows")._kids.length);
ok("我赢了的标题", el("resultTitle").textContent.includes("赢了"), el("resultTitle").textContent);
ok("游客没有再来一局按钮", el("againBtn").style.display === "none", el("againBtn").style.display);

console.log("5) 房主解散");
ws.push({ t: "room_closed", msg: "房主解散了房间" });
ok("提示房间关闭", el("lobbySub").textContent.includes("解散"), el("lobbySub").textContent);
ok("出现重连按钮", el("reloadBtn").style.display !== "none");

console.log(failed === 0 ? "\n游客端全部通过 ✅" : `\n失败 ${failed} 项 ❌`);
process.exit(failed === 0 ? 0 : 1);
