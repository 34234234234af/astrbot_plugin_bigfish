/**
 * 前端规则引擎单测：把 web/index.html 里的 CFG + World 抠出来，在 Node 里跑。
 *
 *     node tools/js_world_test.js
 */
const fs = require("fs");
const path = require("path");

const htmlPath = path.join(__dirname, "..", "web", "index.html");
const html = fs.readFileSync(htmlPath, "utf8");

const start = html.indexOf("const CFG = {");
const end = html.indexOf("/* ============================ 网络");
if (start < 0 || end < 0) {
  console.error("没能从 index.html 里定位到规则代码段");
  process.exit(1);
}
const code = html.slice(start, end) + "\n;globalThis.__T = { CFG, World };";
// World 内部会引用这两个外部标识符
globalThis.myPid = "p1";
globalThis.toast = () => {};
(0, eval)(code);

const { CFG, World } = globalThis.__T;

let failed = 0;
function ok(name, cond, extra) {
  if (cond) {
    console.log(`  [ok] ${name}`);
  } else {
    failed++;
    console.log(`  [FAIL] ${name} ${extra === undefined ? "" : JSON.stringify(extra)}`);
  }
}

function fresh() {
  const w = new World([{ pid: "p1", name: "A" }, { pid: "p2", name: "B" }], 60);
  return w;
}

// 清空 NPC，方便构造确定性场景
function clearNpc(w) {
  w.ents = w.ents.filter((e) => e.kind === "player");
}

console.log("1) 吃鱼与成长");
{
  const w = fresh();
  clearNpc(w);
  const me = w.ents.find((e) => e.pid === "p1");
  const prey = {
    i: 9001, kind: "npc", pid: "", name: "", skill: 0, x: me.x + 5, y: me.y,
    vx: 0, vy: 0, dirx: 1, diry: 0, s: 8, alive: true, respawnAt: 0, invulnUntil: 0,
    eaten: 0, deaths: 0, score: 0, maxSize: 8, color: 0, wanderAng: 0, wanderT: 99,
  };
  w.ents.push(prey);
  const sizeBefore = me.s;
  w.step(CFG.TICK);
  ok("小鱼被吃掉", prey.alive === false && !w.ents.includes(prey));
  ok("体型变大", me.s > sizeBefore, { before: sizeBefore, after: me.s });
  ok("得分增加", me.score > 0, me.score);
  ok("吃了一计数 +1", me.eaten === 1, me.eaten);
}

console.log("2) 体型差不够不能吃");
{
  const w = fresh();
  clearNpc(w);
  const me = w.ents.find((e) => e.pid === "p1");
  const peer = {
    i: 9002, kind: "npc", pid: "", name: "", skill: 0, x: me.x + 5, y: me.y,
    vx: 0, vy: 0, dirx: 1, diry: 0, s: me.s / 1.05, alive: true, respawnAt: 0,
    invulnUntil: 0, eaten: 0, deaths: 0, score: 0, maxSize: me.s, color: 0,
    wanderAng: 0, wanderT: 99,
  };
  w.ents.push(peer);
  w.step(CFG.TICK);
  ok("体型差不足时吃不掉", peer.alive === true, { me: me.s, peer: peer.s });
}

console.log("3) 被吃 → 复活 → 短暂无敌");
{
  const w = fresh();
  clearNpc(w);
  const me = w.ents.find((e) => e.pid === "p1");
  const big = {
    i: 9003, kind: "npc", pid: "", name: "", skill: 0, x: me.x, y: me.y,
    vx: 0, vy: 0, dirx: 1, diry: 0, s: 80, alive: true, respawnAt: 0, invulnUntil: 0,
    eaten: 0, deaths: 0, score: 0, maxSize: 80, color: 0, wanderAng: 0, wanderT: 99,
  };
  w.ents.push(big);
  w.step(CFG.TICK);
  ok("玩家被吃", me.alive === false && me.deaths === 1, { alive: me.alive, deaths: me.deaths });

  const snapDead = w.snapshot();
  const meSnap = snapDead.ents.find((e) => e.i === me.i);
  ok("死后仍在快照里（前端要显示倒计时）", !!meSnap && meSnap.a === 0, meSnap);

  // 快进到复活
  let guard = 0;
  while (!me.alive && guard++ < 400) w.step(CFG.TICK);
  ok("复活了", me.alive === true, { guard });
  ok("复活后回到初始体型", Math.abs(me.s - CFG.PLAYER_START) < 0.001, me.s);
  ok("复活带无敌时间", me.invulnUntil > w.elapsed, { invuln: me.invulnUntil, t: w.elapsed });
}

console.log("4) 快照结构（bot 客户端依赖这个格式）");
{
  const w = fresh();
  const snap = w.snapshot();
  ok("有 tl/w/h", typeof snap.tl === "number" && snap.w === CFG.W && snap.h === CFG.H);
  ok("ents 非空", Array.isArray(snap.ents) && snap.ents.length > 0);
  const meEnt = snap.ents.find((e) => e.p === "p1");
  ok("玩家实体带 p/n", !!meEnt && meEnt.n === "A", meEnt && meEnt.n);
  ok("实体字段齐全", ["i", "x", "y", "s", "p", "n", "a"].every((k) => k in snap.ents[0]));
}
console.log("5) 整局 60 秒跑通（贪心 AI 驱动双方）");
{
  const w = new World([{ pid: "p1", name: "A" }, { pid: "p2", name: "B" }], 60);
  const dt = CFG.TICK;
  let steps = 0;
  let threw = null;
  try {
    while (!w.step(dt)) {
      steps++;
      for (const pid of ["p1", "p2"]) {
        const me = w.ents.find((e) => e.kind === "player" && e.pid === pid && e.alive);
        if (!me) continue;
        let best = null;
        let bestScore = -1;
        for (const e of w.ents) {
          if (e === me || !e.alive) continue;
          if (e.s * CFG.EAT_RATIO > me.s) continue;
          const d = Math.hypot(e.x - me.x, e.y - me.y);
          if (d > 700) continue;
          const sc = e.s / (d + 50);
          if (sc > bestScore) { bestScore = sc; best = e; }
        }
        if (best) {
          const dx = best.x - me.x, dy = best.y - me.y;
          const d = Math.hypot(dx, dy) || 1;
          w.setInput(pid, dx / d, dy / d);
        }
      }
      if (steps > 60 * 30 + 10) break;
    }
  } catch (e) {
    threw = e;
  }
  ok("全程无异常", threw === null, threw && threw.message);
  const res = w.result();
  const players = res.players;
  ok("两个玩家都在结果里", players.length === 2);
  ok("分数有限且非负", players.every((p) => isFinite(p.score) && p.score >= 0), players.map((p) => p.score));
  ok("产生了吃鱼事件", players.some((p) => p.eaten > 0), players.map((p) => p.eaten));
  ok("实体数量受控", w.ents.length < 200, w.ents.length);
  const aliveNpc = w.ents.filter((e) => e.kind === "npc" && e.alive).length;
  ok("NPC 数量维持", aliveNpc >= CFG.NPC_TARGET - 3, aliveNpc);
  console.log(`     结算：${players.map((p) => `${p.name}=${p.score}(吃${p.eaten}/死${p.deaths})`).join("  ")}`);
}

console.log("6) NPC 生态平衡（别把玩家围死）");
{
  const w = new World([{ pid: "p1", name: "A" }], 60);
  w.ents = w.ents.filter((e) => e.kind === "player"); // 清空已有 NPC
  const base = Math.max(...w.players().map((p) => p.s));

  let big = 0, small = 0, total = 0;
  for (let k = 0; k < 600; k++) {
    w.spawnNpc();
    const e = w.ents[w.ents.length - 1];
    total++;
    if (e.s >= base * CFG.EAT_RATIO) big++;
    if (e.s < base * 0.5) small++;
    w.ents.pop(); // 刷完就删，避免"大鱼上限"影响分布统计
  }
  ok("能吃掉玩家的鱼 <= 8%", (big / total) * 100 <= 8, ((big / total) * 100).toFixed(1) + "%");
  ok("小鱼（<玩家一半）>= 60%", (small / total) * 100 >= 60, ((small / total) * 100).toFixed(1) + "%");

  // 场上同时存在的大鱼有上限
  const w2 = new World([{ pid: "p1", name: "A" }], 60);
  for (let k = 0; k < 60; k++) w2.spawnNpc();
  const threats = w2.ents.filter(
    (e) => e.kind === "npc" && e.alive && e.s >= CFG.PLAYER_START * CFG.EAT_RATIO
  ).length;
  ok("场上大鱼不超过上限", threats <= CFG.NPC_MAX_BIG + 1, threats);

  // NPC 比玩家慢，才追得上 / 逃得掉
  ok("NPC 速度系数 < 1", CFG.NPC_SPEED < 1, CFG.NPC_SPEED);
  ok("捕食半径小于逃跑半径", CFG.NPC_CHASE_R < CFG.NPC_FLEE_R,
     { chase: CFG.NPC_CHASE_R, flee: CFG.NPC_FLEE_R });
}

console.log(failed === 0 ? "\n前端规则全部通过 ✅" : `\n失败 ${failed} 项 ❌`);
process.exit(failed === 0 ? 0 : 1);