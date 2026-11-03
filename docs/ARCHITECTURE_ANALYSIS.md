# 架构分析（对照 origin/main `07d039d`）

上一版报告基于旧 `main`（`b344564`）。主人随后推送了 90 个提交，HEAD 现为 `07d039d`（新增 `docs/系统需求与设计文档.md`）。本文件只记录相对上一版的变化、需求对照、工作流和第一波任务。旧报告里「没有 `relay_ops.py` / 没有需求文档 / cron 会自动回收 relay」已全部过时。

分析时跑过 `python3 -m pytest tests/test_relay_ops.py`：10 passed。未改应用代码。

权威需求是 `docs/系统需求与设计文档.md`。和代码不一致时，以该文档的业务规则为准，下面标出实现缺口。

---

## 1. 上一版 15 条缺陷与硬编码口令

结论先说：产品规则上修好的是「到期/用尽自动回收」和「relay 切换不限次数、替换走管理 IP 弹窗」。安全口令、无认证 `node-ready`、Vultr 孤儿实例、schema 漂移都还在。另外出现一条新的路由覆盖：`POST /admin/reset-flow` 被写了两次，Vultr 重置流量会打到只认 relay 的处理函数。

| # | 上一版 | 现在 | 位置 |
| --- | --- | --- | --- |
| 1 | `switch_remaining = limit - 1 - count`，客户端比服务端早锁一次 | **Vultr 仍在；relay 已改** | 下发公式仍是 `worker/src/index.js:337`，切换响应 `remaining` 仍是 `limit - count - 1`（`:692`）。服务端只对非 relay 做 `count >= limit`（`:509`）。客户端只在 `resource_type === "vultr"` 时用 `switch_remaining <= 0` 锁按钮（`client/src/components/NodesView.vue:378`） |
| 2 | 管理端切换把 `old_used_gb` 记在 `assignment_id=0`，客户按 assignment 求和加不进去 | **仍在** | `worker/src/index.js:1025`。用户切换仍写真实 `row.id`（`:689`） |
| 3 | `reset_flow` 只清 `used_gb`，不清历史 | **relay 已改，Vultr 被路由弄坏** | 新函数 `resetRelayFlow` 清 `used_gb` 并删除该 assignment 已完成的 `switch_requests`（`:1736-1748`）。路由在 `:1899` 就 return 了它；`:1920` 的旧 `resetFlow`（`:1397-1406`，只清 `used_gb`）是死代码。`app/api_client.py` 也有两个 `reset_flow`（`:191` 与 `:247`），后者覆盖前者，但都打同一个 URL。Vultr 详情里的重置（`app/ui/users_page.py:2387`）因此会得到「分配不存在」 |
| 4 | `/me/switch` 不看流量配额 | **relay 已看候选；Vultr 仍不看** | relay 的 `isUsable`（`worker/src/index.js:526-533`）拒绝禁用、到期、`used_gb >= quota_gb`。Vultr 分支（`:601` 之后）没有配额判断 |
| 5 | 到期比较粒度不一致 | **部分改善，切换入口仍用日期** | `/me/nodes` 用 `bjNow` 比 `effExpiry`（`:329`）。`syncCandidateIpStatus` 用到秒（`:1647-1651`）。`switchResource` 在区分 relay 之前用 `effectiveExpiry < bjToday()`（`:504-505`），日期字符串和带时分秒的到期比会偏一天内 |
| 6 | 一台 Vultr 分给多人时只更新第一行 assignment | **仍在（管理端）** | `processAdminSwitch` 使用 `.first()`（`:1074-1076`）。用户切换按 `assignment_id` 更新自己的行（`:849`） |
| 7 | `GET /admin/node-ready` 无认证 | **仍在** | `worker/src/index.js:1860-1864`。cloud-init 仍无 token 调用它（`worker/src/gost.js:280`） |
| 8 | 证书 token 普通字符串比较 | **仍在** | `certSave` 使用 `token !== expected`（`worker/src/index.js:194`）。管理员 token 仍是 `timingSafeEqual`（`:173`） |
| 9 | `createReplacement` / `checkNodeHealthy` 无调用方 | **仍在** | 定义在 `:736`、`:750`，全仓库无调用。客户端「不通再切」仍是再发一次完整销毁重建 |
| 10 | cron 注释写 10 分钟，配置是每分钟 | **注释仍错；行为已换** | 注释仍在 `:1938`。`scheduled` 已不再调用 `recycleExpiredRelays`（`:1955-1956`），改为 `syncCandidateIpStatus`（`:1951`）。死函数本体还在 `:1581-1624`，一旦重新挂上 cron 会按旧规则删分配 |
| 11 | `schema.sql` 落后于迁移 | **仍在，且又多一张表** | `worker/schema.sql` 仍只有 users/assignments/sessions/certificates。`switch_requests`、`recycle_log`、新的 `recycle_flow_log`（`worker/migrations/013_recycle_flow_log.sql`）都不在全量 schema 里。`001`/`002` 文件名仍然重复 |
| 12 | Webshare 替换 IP 不改 `relays[]` 和 D1 | **管理 IP 弹窗已改；列表上的替换按钮仍只刷新** | 跨账号选择在 `app/relay_ops.py`。保存时写 `config.relays` 并 `assign` 回 D1（`app/ui/users_page.py:1489-1544`）。`app/ui/webshare_page.py:1377` 的列表替换仍只调 `replace_proxy` 再刷新列表 |
| 13 | 脚本把 `sys.path` 写死成本机绝对路径 | **测试文件已改；旧脚本仍在，还多一个** | `tests/test_relay_ops.py:9` 用仓库相对路径。`scripts/daily_check.py:14`、`scripts/e2e_assign_test.py:7`、新的 `scripts/assign_yong_test.py:7` 仍是本机绝对路径 |
| 14 | Tauri `csp: null`，远程页可调本地代理 | **仍在** | `client/src-tauri/tauri.conf.json:27`。capability 仍允许 `https://user.yduas.cc` 调 Rust 命令 |
| 15 | 服务端 Xray v26.3.27，客户 sidecar CI 是 v26.10.21 | **仍在** | `worker/src/gost.js:77-81`；`.github/workflows/build-macos.yml:47`、`build-windows.yml:50` |

硬编码口令（只报位置，值未变，文档第 9 节写「一律不写入仓库」但代码里还在）：

- `app/ipqs.py:10` `DEFAULT_KEY`，`:19` 在 config 没有 key 时使用。
- `app/config.py:24` `DEFAULTS["proxy_pass"]` 不是空字符串（同段还有默认 `proxy_user`）。
- `scripts/apply_relays.py:42` `cf_account_id` 的默认参数仍是写死的账号 id。
- `scripts/setup_kk_wifi.sh:7`、`setup_kk_wifi_v2.sh:7`、`setup_kk_wifi_40.sh:7`、`setup_yy_wifi.sh:6` 的 `PASSWD`。
- `scripts/e2e_assign_test.py` 仍写死服务器地址和测试用户 id。

上一版还指出、本轮已改变的行为（不算进上面 15 条，但协调者不要再按旧报告排期）：

- 到期/用尽不再自动删分配。cron 只把候选 `enabled=false`，全部禁用后 `POST :8080/stop`（`worker/src/index.js:1672-1688`）。这符合需求 §4.4。
- relay 切换会把当前 `used_gb` 封进旧候选，并把分配行 `used_gb` 置 0（`:583-598`）。
- `apply_relays.py:72-171` 会安装 `switch_server.py`。skip 条件加上了 `systemctl is-active`（`:189-191`），被 `/stop` 停掉的服务会重新拉起。
- 设置页可以维护 `webshare_accounts`（`app/config.py:64`，`app/ui/settings_page.py:287-305`）。
- 用户页新增 `_resolve_server_conn`（`app/ui/users_page.py:566`），回收停服务时按 `server_id` 找密码。
- 新增 `app/ui/persist.py`（`SizedDialog`），四个大页面都继承它。
- 体量：`users_page.py` 2963 行，`webshare_page.py` 1896，`nodes_page.py` 1146，`servers_page.py` 2859，`worker/src/index.js` 1958，`settings_page.py` 436。

---

## 2. 需求文档 vs 实现

### 已对齐

- 多 Webshare 账号，候选 IP 自带 `ws_account`（ADR-001）。替换账号优先级在 `relay_ops.resolve_replace_account`，测试覆盖了 IP 级、host 反查、线路级、未知账号、全空。
- 独立线路 / 合并线路、管理 IP 弹窗（添加、删除、禁用、设为当前、改配额、改到期、续费、跨账号替换）。保存时把激活候选的 `quota_gb` 和 `client_expiry` 写回分配（`users_page.py:1532-1544`）。
- 改配额/到期视为续费：清候选封账、`reset_flow`、未过期则 `enabled=true`，并在恢复或续费时重新部署（`:1474-1554`）。符合 §4.5、ADR-003、ADR-006。
- 手动回收记 `recycle_flow_log`（migration 013，`recordRecycleFlow`，`api_client.record_recycle_flow`）。
- 客户端展示 `eff_expiry`、生效配额/已用、候选禁用（`NodesView.vue` 的 `isSwitchDisabled`、`candDisabled`）。relay 单线路不能切，次数限制只加在 Vultr 上。
- 替换端点改走 Webshare v3（`app/webshare.py:18`、`:263` 附近的注释）。
- 前端发布步骤写在需求 §2.1，与 `tauri.conf.json` 远程 URL、`/build-stamp` 一致。
- `pytest tests/test_relay_ops.py` 10 项通过。

### 部分对齐

- §4.1 三处配额。管理 IP 保存会写分配记录、`node_data.quota_gb` 和 `candidates[].quota_gb`。Worker 判定在候选配额为 0 时回退分配记录（`index.js:1656`）。Webshare 页「列表上直接替换」不走这条保存，三处会再次分裂。
- §4.2 到期。合并线路读候选 `expiry`，空则回退 `client_expiry`，这条在 `syncCandidateIpStatus` 里有。`switchResource` 开头仍用线路级字段和「今天」的日期比，和「精确到秒」不一致。
- §4.3 流量 = `used_gb + hist_gb`。`/me/nodes` 和 `listAllAssignments` 都算了 `hist_gb`（`:278`、`:1450`）。管理端 Vultr 切换的历史行 `assignment_id` 仍是 0，这段出站进不了和。relay 封账在候选 `used_gb` 上，不进 `switch_requests`，和文档「hist_gb = 切换时封账的 old_used_gb」不是同一条账。
- §4.4 全部禁用后停服务。会发 `/stop`，但 `switch_server.py:254` 即使内部失败也回 HTTP 200，Worker `:1679` 只看 `resp.ok`。停失败时代理继续计费，日志像成功。全部已禁用的线路每分钟都会再打一次 `/stop`。
- §4.7 替换「同国家、排除自己」。`available_pool` 按 host 排除当前候选（`relay_ops.py:67-68`），国家过滤在 UI 层，不在这个纯函数里。
- §3.3 服务器。Vultr 购买、手工机、切换、DNS 仍在 `servers_page.py`。需求点名的 inter-la-01 / zenix-e01 只出现在文档和 `apply_relays` 的示例命令里，不是代码里的类型。
- 架构图写「一切切换都经控制机 IPv6」。代码里 Vultr 可选 `CONTROL_URL`（`index.js` 的 `apiBase`），Cloudflare DNS 在 Worker 内直连（`:695` 附近），relay 切换是 Worker 直连 `http://服务器:8080`。

### 缺失或与文档相反

- §3.4「订阅」。客户端没有订阅 URL 端点；只有节点列表、复制链接、二维码。
- §8 写迁移 `001~013` 可由 schema 重建。`schema.sql` 不含 013，也不能单独建出 `switch_requests`。
- 文档第 9 节要求凭据不进仓库。第 1 节列出的口令位置都还在。
- Vultr 用尽/到期不会停机，也不会禁止 `/me/switch`。文档的自动禁用主要描述 relay。
- `recycleExpiredRelays` 仍留在源码里。文档说已删除。

### 新发现

1. **P0 路由覆盖。** `POST /admin/reset-flow` 注册两次（`index.js:1899` 和 `:1920`）。先注册的 `resetRelayFlow` 只更新 `resource_type='relay'`。Vultr 重置（`users_page.py:2387`）失败。relay 续费处用 `except Exception: pass` 吞掉失败（`:1482-1486`），界面仍把候选标成启用并可能重新部署，历史流量还在，客户看起来「续费了但仍用尽」。
2. **P0 停服务假成功。** 见上，`switch_server.py:254` + `index.js:1679`。手动回收走 SSH 并检查 `is-active`（`users_page.py:625` 一带）；cron 这条自动停用没有同等校验。
3. **P0 Vultr 孤儿实例。** 创建成功、删除失败只 `console.error`（`index.js:681-686`）。没有对账任务。删除失败后客户端重试会再创建一台。
4. **主 IP 与激活 IP 可能不是同一个。** 保存时 `config.relays` 的 host 取 `candidates[0]`（`users_page.py:1496-1501`），配额和到期取 `active_idx`（`:1535-1537`）。激活索引不是 0 时，部署用的上游和客户端「当前 IP」会不一致。
5. **测试注释和断言相反。** `tests/test_relay_ops.py:74-83` 的 docstring 写「当前线路自己的 IP 也允许」，断言是 `len(out) == 0`。函数在 `:67` 无条件排除 `cur_hosts`。测试是绿的，注释会误导下一个改 `available_pool` 的人。
6. **`api_client.py` 重复定义 `reset_flow`。** `:191` 是死代码。`list_recycle_log` 的 docstring（`:209`）仍写「自动回收后记录」，和 ADR-002 相反。

---

## 3. 工作流（相对上一版）

四个编码代理的边界不变。文件所有权有三处要改。

| 工作流 | 独占 | 本轮调整 |
| --- | --- | --- |
| A 控制面 | `worker/**`、`scripts/switch_server.py`、`app/api_client.py` | 新增路由 ` /admin/recycle-flow`、`resetRelayFlow`。A 负责拆开重复的 `reset-flow`，并让 `schema.sql` 含 013 |
| B Vultr 管理端 | `servers_page.py`、`vultr.py`、`domain.py`、`cloudflare.py`、`gencreds.py`、本地代理一组、`main_window.py` | `settings_page.py` 不再整份归 B。Vultr/CF/本机端口仍归 B；其中的 Webshare 账号表（`_collect_ws_accounts` 一带）归 C。两边不要同时重排这个文件 |
| C Webshare + relay + 用户 | `webshare_page.py`、`nodes_page.py`、`users_page.py`、`webshare.py`、`relay.py`、`share.py`、`ipqs.py`、`apply_relays.py` | **加上 `app/relay_ops.py` 和 `tests/test_relay_ops.py`。** 纯函数和测试必须同一次提交。设置页里的账号表也归 C |
| D 客户客户端 | `client/**` | 构建产物仍交给 A 放进 `worker/assets` 再 deploy。D 不提交 `worker/assets` |

`app/ui/persist.py` 被 B 和 C 的对话框一起用。协调者拥有这个文件的行为（恢复/保存尺寸）。代理只新增 `_size_key`，不改基类算法。

不要把 `relay_ops.py` 分给 A。它不碰网络和 D1，测试桩是 `key_for_host_fn`。A 若改切换封账，只改 Worker，不改账号选择。

软路由脚本和 `rebuild_*` / `assign_yong_test.py` 继续冻结。

横切契约在上一版 10 条上加两条：

11. `webshare_accounts` 的元素形状 `{name, api_key}`，以及候选上的 `ws_account` / `quota_gb` / `expiry` / `enabled` / `used_gb`。C 写，A 读。
12. `POST /admin/reset-flow` 与 `POST /admin/recycle-flow` 的 body。只有 A 改路径；C 的续费和回收是调用方。

---

## 4. 第一波任务

同一工作流内按编号顺序做。P0 是安全或会继续烧钱（孤儿实例、停了服务仍在转发、续费清零失败）。

### A 控制面

**A1（P0）拆开重置流量路由。**  
目标：relay 续费继续清 `used_gb` 和该 assignment 的 done `switch_requests`；Vultr 重置走只清 `used_gb` 的旧语义（或单独路径），不再 404。  
文件：`worker/src/index.js`（`:1397`、`:1736`、`:1899`、`:1920`），`app/api_client.py`（删掉 `:191` 的重复定义，或改成两个函数名）。  
验收：对 relay id，`used_gb` 为 0 且该 id 的 done `switch_requests` 被删；对 vultr 实例 id，返回成功且只动 `used_gb`，不误删别的切换史。`users_page.py:2387` 不再弹出「分配不存在」。  
依赖：无。C 的续费在 A1 合并前不要改重置 URL。

**A2（P0）停服务以 JSON `ok` 为准，失败要看得见。**  
目标：cron 在全部候选禁用后真正停掉 gost 和 sing-box；失败不要当成成功。  
文件：`scripts/switch_server.py:251-254`（失败用非 200 或 Worker 读 `ok`），`worker/src/index.js:1672-1688`。  
验收：unit 不存在时接口仍成功（幂等）；`systemctl` 真失败时 Worker 日志不是「已停服务」，且下一分钟会重试。不在这里重新启用 `recycleExpiredRelays`。  
依赖：无。可与 A1 并行，只是同一文件，建议 A 一个人顺序做。

**A3（P0）Vultr 删除失败要留下可查询记录，避免静默孤儿。**  
目标：创建成功且 DELETE 非 404 失败时，把 `old_id`、`new_id`、HTTP 状态写入 D1 或至少一条可被管理端拉到的列表，而不是只有 `console.error`。  
文件：`worker/src/index.js:681-686` 和 `:953` 附近的管理端同样路径；可加一张小表或复用 `switch_requests` 的一列。  
验收：模拟删除 500 后，管理端能看到「旧实例可能仍在计费」；不在删除失败时自动再创建。  
依赖：B 只做展示，等 A 给出字段。

**A4（P0）给 `node-ready` 加与证书相同的共享密钥。**  
目标：不知道 `new_id` 的人不能把 stage 改成 `installed`。  
文件：`worker/src/index.js:1860`，`worker/src/gost.js:280`。cloud-init 已有 `CERT_TOKEN` 时可以复用请求头，避免新 secret。  
验收：无头的 GET 返回 401；带正确头且 `new_id` 匹配 pending 行时 stage 变为 `installed`。  
依赖：无。已部署的旧 cloud-init 在密钥上线后会 401，5 分钟超时仍会放行（`:832` 一带），要在任务说明里写上这个窗口。

**A5 管理端切换的 `old_used_gb` 记到真实 assignment。**  
目标：修第 2 条。有分配时 `assignment_id` 用该行 id；多人共享时更新所有匹配行或明确只支持一行并在接口里拒绝第二行。  
文件：`worker/src/index.js:1025`、`:1074`。  
验收：管理端切换完成后，`/me/nodes` 的 `hist_gb` 包含这次销毁前的出站。  
依赖：A3 的记录格式若共用 `switch_requests`，先做 A3。

**A6 补全 `schema.sql`，删掉或隔离 `recycleExpiredRelays`。**  
目标：新库执行 schema 后含 `switch_requests`、`recycle_log`、`recycle_flow_log` 和 users 的 switch 列。死函数不再位于会被误挂回 cron 的位置。  
文件：`worker/schema.sql`，`worker/src/index.js:1581`。  
验收：对照 migrations 001–013 列清单一致；`scheduled` 里仍然只有 sweep、syncRelayTraffic、syncCandidateIpStatus。  
依赖：无。

### B Vultr 管理端

**B1（P0）重置流量在 A1 之后改调用正确的函数。**  
目标：Vultr 详情「重置流量」命中只处理 vultr 的接口，并继续写 `server_flow_reset`，避免 5 分钟内被同步盖回。  
文件：`app/ui/users_page.py:2337-2387`（这段在用户页，但是 Vultr 语义；C 不要改这一函数，由 B 改调用，或 A 在 `api_client` 提供 `reset_vultr_flow` 后 B 改这一处）。若坚持文件所有权，A 在 `api_client.py` 增加函数，B 只改 `:2387` 这一行——该行在 C 的文件里，**这一行指定给 B，C 本波不要动 `_reset_flow`**。  
验收：重置后 D1 `used_gb` 为 0，下一次带宽同步不会立刻写回重置前的数。  
依赖：A1。

**B2 管理端能看见 A3 的孤儿实例。**  
目标：服务器页刷新时列出「删除失败仍可能计费」的旧 id。  
文件：`app/ui/servers_page.py` 的刷新/日志。  
验收：A3 的记录出现在界面上，并带旧 id，不自动再点一次切换。  
依赖：A3。

**B3 不要改 `settings_page.py` 的账号表。**  
本波 B 无设置页任务。若要改 Vultr key 区域，只动该区域，避开 `_collect_ws_accounts`。

### C Webshare + relay + 用户

**C1（P0）续费清零失败不得启用并部署。**  
目标：`reset_flow` 失败时不把 `enabled` 改回 true，不调用 `_deploy_after_save`，界面显示错误。  
文件：`app/ui/users_page.py:1474-1554`。  
验收：接口 404/500 时候选保持禁用，状态栏是失败而不是「已保存」。接口成功时行为与现在相同（清零、启用、IP 变化或续费才部署）。  
依赖：A1。C1 在 A1 合并后做；A1 未合并前 C 不要改 URL。

**C2 保存时主上游用激活候选，而不是 `candidates[0]`。**  
目标：`active_idx != 0` 时，`relays[].host`、部署上游、`node_data.ip` 与激活 IP 一致。  
文件：`app/ui/users_page.py:1496-1530`。  
验收：激活第 2 个候选后保存，config 与 D1 的 host/ip 都是第 2 个；`relay_ops` 测试仍 10 通过。  
依赖：无。不要和 C1 改同一函数时互相覆盖，建议同一人先 C2 再 C1，或合成一个提交。

**C3 列表上的「替换 IP」要么走 `relay_ops` 并回写已生成线路，要么按钮标明「只换账号池、不改已分配线路」。**  
目标：消除第 12 条的半截行为。  
文件：`app/ui/webshare_page.py` 替换成功回调，必要时调用与管理 IP 相同的回写。  
验收：替换一个已经生成 relay 的 IP 后，`relays[]` 和已分配 `node_data` 的 host 变为新 IP，或 UI 明确写「已分配线路请用管理 IP」。  
依赖：无。不改 `relay_ops` 的函数签名；缺能力就加函数并加测试。

**C4 修正 `available_pool` 测试注释，补上「国家不在此函数过滤」的测试。**  
目标：注释和断言一致。  
文件：`tests/test_relay_ops.py:74`。  
验收：`pytest tests/test_relay_ops.py` 全绿；docstring 不再写「也允许」。  
依赖：无。可与 C2 并行（不同文件）。

**C5 账号表只在设置页维护，生成中转时用选中的 `webshare_accounts` 项。**  
目标：确认下拉框与 `key_for_host` 使用同一列表。本波只修发现的不一致，不重做 UI。  
文件：`app/ui/settings_page.py` 的账号表、`app/webshare.py` 的账号解析。  
验收：两个账号时，管理 IP 替换下拉能选到第二个账号的 key；`resolve_replace_account` 测试仍绿。  
依赖：无。B 本波不改这些行。

### D 客户客户端

**D1 Vultr 剩余次数与服务端 `count >= limit` 一致。**  
目标：在 A 未改 `:337` 之前，客户端不要用 `limit - 1 - count` 提前锁死。可以显示服务端给的 `switch_remaining`，但禁用条件改为「服务端 429」或自己用 `switch_limit` 与已用次数计算 `count >= limit`。  
文件：`client/src/components/NodesView.vue:368-378`。若下发字段本身少 1，D 应改用 `switch_limit` 与节点上的已用次数；若节点对象没有已用次数，**依赖 A 在 `/me/nodes` 增加 `switch_count` 或把 `:337` 改成 `limit - count`**。把改公式放进 A 的 A1 之后一个小任务 A1b 更干净：A 改 `:337` 和 `:692`，D 只确认按钮与 429 一致。  
验收：limit=5、当天 0 次时按钮可点；成功 5 次后按钮禁用且接口 429；relay 仍不受次数限制。  
依赖：A 改 `:337`（记为 A1b，无其他依赖）。

**D2 账号禁用文案保持「账号已禁用」。**  
目标：确认 `user.disabled` 路径（`index.js:1810` 等）在客户端显示需求里的那句，而不是「未登录」。本波只查 `LoginView` / `NodesView` 的错误展示，不改协议。  
文件：`client/src/components/LoginView.vue`、`NodesView.vue` 的请求错误。  
验收：`status=disabled` 时界面含「账号已禁用」。  
依赖：无。

**D3 不改 CSP 的本波不做功能。** 第 14 条留给单独的安全任务，不塞进第一波功能，避免和热更新模型缠在一起。若要做，单独立项：收紧 capability 的远程 URL 权限，而不是在 D1 里顺手改。

### 建议的合并顺序

1. A1 + A1b（重置路由、剩余次数公式）  
2. C1、D1（依赖上面）  
3. A2、A4 可与 2 并行（A 内部需错开 `index.js`）  
4. A3 然后 B2  
5. C2、C3、C4、C5、D2 无跨工作流依赖，可立即开始，但 C1 与 C2 同文件，交给同一个 C，不要两个人同时改 `_save`
