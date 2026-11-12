# astrbot_plugin_bigfish · 大鱼吃小鱼

给 AstrBot 的实时小游戏：群里发一条指令就能开一个房间，**游戏跑在房主的浏览器里**，
人多了就一起玩，没人陪的时候 bot 会自己打一局，然后私聊向你汇报战绩。

- 实时操作（WASD / 方向键 / 鼠标 / 触屏摇杆），不是回合制
- bot 会作为普通玩家进房间陪玩，会追小鱼、躲大鱼
- 服务器只做 WebSocket 转发：**不做模拟、不画图、不存视频，几乎不吃 CPU**
- 空闲自娱：群里安静一段时间后，bot 自己打一局并发私聊战报（含输赢、吃鱼数、分数、高光时刻）
- **指令词全部可自定义**：主指令名、开房/关房等子指令都能改成你自己的说法
- **人格化点评**：不管她自己打完还是群里打完，都会用你设定的那一套人格去评价/吐槽这一局

---

## 一、它是怎么省服务器的

```
群友 A: /鱼 开房
        │
        ▼
   AstrBot 插件 ──创建房间、把 bot 派进去──┐
        │                                 │
        │  回复链接                        │
        ▼                                 ▼
   群里所有人点链接                 bot 作为玩家连进房间
        │                                 │
        ▼                                 ▼
   第一个打开的人是「房主」   <──── WebSocket 中继 ────>
   他的浏览器跑整个世界的模拟         （只转发消息）
```

- **房主的浏览器**：跑物理、吃鱼判定、NPC AI、渲染。这是最吃 CPU 的部分，全在玩家机器上。
- **服务器**：只维护房间表 + 转发 JSON。一局满打满算十几 KB/s。
- **bot**：每秒收 ~15 个状态快照、发 ~15 个方向包，一个房间几千字节每秒。

自娱模式更省：直接在插件进程里用纯逻辑内核快进算完一局，**一局耗时几毫秒**。

---

## 二、安装

1. 把整个 `astrbot_plugin_bigfish` 目录放到服务器的
   `AstrBot/data/plugins/astrbot_plugin_bigfish/`
2. 在 AstrBot WebUI → 插件 里点「重载插件」，或直接重启 AstrBot
3. 依赖 `aiohttp`（AstrBot 自带了；如果提示缺失就按 `requirements.txt` 装一次）
4. 到 插件配置 里填 **公网访问地址**（见下）

### 必须配的一项：公网地址

游戏页面由插件自己起的一个小 HTTP 服务提供（默认端口 `8317`）。三种做法任选：

| 做法 | 配置 | 说明 |
|---|---|---|
| 直接暴露端口 | `public_base_url` 留空，安全组放行 8317 | 生成的是 `http://服务器IP:8317`，能用但不加密 |
| 域名 + 反向代理（推荐） | `public_base_url = https://fish.example.com` | 见下方 Nginx 示例，链接好看也安全 |
| 内网玩 | `public_base_url` 留空 | 同一局域网可用 |

Nginx 反代示例（注意要转发 WebSocket 的 Upgrade 头）：

```nginx
location / {
    proxy_pass http://127.0.0.1:8317;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 3600s;
}
```

配好了先用浏览器打开 `https://你的地址/health`，看到 `{"ok": true, ...}` 就通了。

---

## 三、指令

| 指令 | 作用 |
|---|---|
| `/鱼` | 帮助 |
| `/鱼 开房` | 开房间并把链接发到当前会话 |
| `/鱼 加入` | 再要一次房间链接 |
| `/鱼 状态` | 看房间里现在有谁 |
| `/鱼 关房` | 解散房间（房主断线也会自动解散） |
| `/鱼 自己玩` | 立刻让 bot 自己打一局，当场给你看战报 |
| `/鱼 战绩` | bot 的自娱累计战绩 |
| `/鱼 排行` | 群里玩家的历史最好成绩 |

别名：`/fish`、`/大鱼`、`/钓鱼`。

> 上面这些词**都能在配置里改**（见第五节）。把 `开房` 改成 `开黑|来一把`，
> 就变成 `/鱼 开黑`；主指令名也能加，比如 `小鱼` → `/小鱼 开房`。

同一个会话同时只保留一个房间。

---

## 四、怎么玩

1. 群友发 `/鱼 开房`，bot 回一个链接。
2. **第一个点开链接的人成为房主**，页面会显示房号、在线名单。
   bot「小鱼苗」已经以玩家身份在房间里等着了。
3. 房主点「开始游戏」。一局默认 90 秒。
4. 规则：
   - 体型差 12% 以上才能吃掉对方，吃掉得分 = 对方体型 + 10
   - 被吃掉后 2.5 秒原地复活（体型归位，分数不清零），复活后 2 秒无敌
   - 时间结束按总分排名，结果自动发回群里
5. 房主可以点「再来一局」重开。

操作：`WASD` / 方向键 / 按住鼠标拖动 / 手机触屏摇杆。

---

## 五、配置项

### 指令词（可以改成你自己的说法）

| 配置 | 默认 | 说明 |
|---|---|---|
| `command_names` | `["鱼"]` | 主指令名，可以加多个。内置的 `鱼`/`fish`/`钓鱼`/`大鱼` 永远可用 |
| `sub_commands.open` | `开房` | 开房指令词，支持同义词：`开黑\|来一把\|组团` |
| `sub_commands.join` | `加入` | 再要一次链接 |
| `sub_commands.status` | `状态` | 查房间 |
| `sub_commands.close` | `关房` | 解散房间 |
| `sub_commands.selfplay` | `自己玩` | 立刻自娱一局 |
| `sub_commands.stats` | `战绩` | 自娱战绩 |
| `sub_commands.board` | `排行` | 群排行榜 |
| `sub_commands.help` | `帮助` | 帮助 |

改法举例：把 `sub_commands.open` 写成 `开黑|来一把`，然后 `/鱼 开黑`、`/鱼 来一把` 都能开局，
原来的 `/鱼 开房` 也照样能用（内置同义词不会被覆盖，防止把自己锁在门外）。

主指令名想加新的（比如 `小鱼`），就填 `command_names = ["鱼", "小鱼"]`，
之后 `/小鱼 开房` 也能触发，帮助里的用法也会自动跟着变。

> 新增的主指令名由「全消息钩子」接管，只在机器人被唤醒时（`/前缀` 或 @）才响应，
> 不会把普通聊天里的「小鱼」当成指令。

### 人格点评 / 吐槽

| 配置 | 默认 | 说明 |
|---|---|---|
| `comment_enabled` | true | 点评总开关 |
| `comment_on_selfplay` | true | 自己打完之后点评 |
| `comment_on_match` | true | 群里打完（真人局）之后点评 |
| `persona_id` | 空 | 指定用哪个人格；留空 = 跟随当前会话的默认人格 |
| `comment_style` | 空 | 额外风格要求，会追加到人格设定后面 |
| `comment_max_chars` | 60 | 点评字数上限 |

点评会把**当前会话的人格设定**（`persona["prompt"]`）作为 system prompt，再加上局面摘要，
让模型用她自己的口吻说一句话。比如：

```
🏁 本局结束
🥇 阿珍 — 300 分，吃了 9 条，被吃 1 次
🥈 小鱼苗（bot） — 210 分，吃了 7 条，被吃 2 次

这把我赢得挺轻松，对面是在游还是在散步？
```

模型调用走 `context.llm_generate(chat_provider_id=..., system_prompt=人格)`（v4.5.7+），
老版本会自动退回 `provider.text_chat(...)`；两者都不可用时就只发干巴巴的战报，不会报错。

### 其它

| 配置 | 默认 | 说明 |
|---|---|---|
| `relay_host` / `relay_port` | `0.0.0.0` / `8317` | 中继监听地址和端口 |
| `public_base_url` | 空 | 公网地址，强烈建议填 |
| `max_players` | 2 | 每房间最多几个真人（bot 不占位） |
| `match_seconds` | 90 | 单局时长 |
| `room_ttl_minutes` | 180 | 空房间保留多久 |
| `bot_plays` | true | 开房时让 bot 进房一起玩 |
| `bot_name` | 小鱼苗 | bot 在游戏里的名字 |
| `bot_skill` | 0.75 | bot 水平，太高会把人虐哭 |
| `idle_enabled` | true | 空闲自娱开关 |
| `idle_minutes` | 90 | 多久没人说话算「无聊」 |
| `idle_cooldown_minutes` | 120 | 两次自娱最小间隔 |
| `idle_daily_limit` | 3 | 每天最多自娱几次（0=不限） |
| `report_umo` | 空 | 战报发到哪个会话；留空 = 最近一次私聊 bot 的人 |
| `selfplay_solo` | false | 自娱时是否不安排对手 |

> 想收到私聊战报：先私聊 bot 发一次 `/鱼`，它就会记住这个会话；
> 或者直接在 `report_umo` 里选中你的私聊。

---

## 六、目录结构

```
astrbot_plugin_bigfish/
├── main.py                 # 插件入口：指令、房间、空闲调度、私聊汇报
├── metadata.yaml
├── _conf_schema.json
├── requirements.txt
├── bigfish_core/           # 纯逻辑，不依赖 AstrBot
│   ├── engine.py           # 无头对局模拟（自娱用）
│   ├── ai.py               # bot 的「鱼脑」：找猎物、躲天敌、贴边修正
│   ├── report.py           # 战报排版
│   └── store.py            # 战绩/活跃时间/排行榜持久化
├── bigfish_net/
│   ├── relay.py            # aiohttp 中继：分角色、转发、服务前端
│   └── botclient.py        # bot 以真人玩家身份进房
├── web/
│   └── index.html          # 整个前端：模拟 + 渲染 + 输入（单文件，无外部依赖）
└── tests/                  # 本地测试，部署时可整目录删掉
    ├── _stub/astrbot/      # 测试用的 astrbot 替身包
    └── *.py / *.js
```

数据文件在 `AstrBot/data/plugin_data/astrbot_plugin_bigfish/state.json`，重装插件不会丢。

---

## 七、常见问题

**链接打不开 / 一直显示正在连接**
检查 `public_base_url`、防火墙/安全组、反代是否转发了 WebSocket（`Upgrade` 头）。
用 `curl https://你的地址/health` 验证。

**页面能打开，但一直卡在「等房主点开始」**
说明房主位已经被别人占了（第一个打开的人才是房主）。让那个人点开始，或者让所有人刷新后统一由一个人先开。

**bot 没进房间**
看日志里有没有 `bot 已进入房间`。如果中继启动失败（端口被占用），插件会在回复里提示。

**群里没人说话，bot 也不自己玩**
- 需要先有人私聊过 bot（或配置 `report_umo`），否则它不知道该汇报给谁
- `idle_minutes` 是按「任意会话的最后一条消息」算的，机器人重启后需要等一条新消息才开始计时
- 连着自娱有冷却和每日上限，别指望它一刻不停

**点评没出现 / 说完结就干巴巴的**
- 检查 `comment_enabled` 和对应的 `comment_on_match` / `comment_on_selfplay`
- 插件拿不到人格时（`persona_manager` 不可用）仍会调模型，只是没有角色设定；如果连模型提供商都没配，就只能发纯战报
- 想看它到底调了什么，日志级别调到 debug，会打印失败原因

**改了指令词没生效**
子指令词是每次指令实时读配置的，改完存一下就行；但 `command_names` 新增的主指令名
依赖「全消息钩子」，保存后建议**重载一次插件**，再试 `/新名字 开房`。

**能几个人一起玩**
`max_players` 调到 3~8 都行，房主的浏览器会同时模拟所有人，人多时房主机器压力会大一点。

**手机能玩吗**
能，页面自适应，触屏有虚拟摇杆。

**为什么游戏不是服务器权威**
这是故意的取舍：你的服务器扛不住实时模拟，所以把权威放到了房主浏览器。
代价是房主可以作弊（改自己的分数），朋友之间玩没问题；要防作弊就得把模拟搬回服务器。

---

## 八、本地测试（不需要装 AstrBot）

```bash
python tests/selftest.py         # 纯逻辑：跑几局无头对局看数值平衡
python tests/plugin_test.py      # 插件链路：指令→开房→自娱→播报→汇报（用 astrbot 替身）
python tests/e2e_test.py         # 端到端：中继 + bot 客户端 + 假房主真实对局 20 秒
node   tests/js_world_test.js    # 前端规则引擎单测（从 index.html 抠出来跑）
node   tests/js_client_test.js   # 前端房主端黑盒测试（DOM/WS 垫片驱动整个页面）
node   tests/js_guest_test.js    # 前端游客端黑盒测试（vm 独立上下文）
```

Python 端只需要 `aiohttp`（`pip install aiohttp`）。`tests/_stub/` 是测试用的 `astrbot` 替身包，
只在这些测试脚本里被加进 `sys.path`，和真实运行无关，部署到服务器时可以把 `tests/` 整个删掉。

GitHub Actions 会在每次 push 时自动跑这六套测试（见 `.github/workflows/test.yml`）。

---

## 九、已知限制

- 房主浏览器是权威端，房主关掉页面这局就结束
- 只支持一个房主 + 若干玩家，没有观战位
- 断线不会自动重连（刷新页面即可，重连后如果是新房主会从头开始）
- 帧同步是「状态广播 + 客户端插值」，公网 100ms 内的延迟手感正常，2G 网络下会飘

灵感来自经典的「大鱼吃小鱼」类 Flash 小游戏，代码全部原创，未使用第三方素材。
