# DLUT CPC 三工程联动说明

2026-10-09 · v4.2。此文与本工程代码一同提交；后续调整保留 Git 历史，重大协议变更追加版本记录。

## 职责与数据流

DLUT CPC 是人员、已确认逐场队伍名单和真人认领审核的信源。OJ Wall 拉取这些数据，结合它自己的线上提交与核验后的现场逐题榜单；CF Bot 再读取 OJ Wall 的本人进度，与自己的口胡记录合并。

本工程不读取另两个工程的数据库，不维护提交或口胡副本，不新增统一登录服务。三个服务可分别部署，通过配置地址访问 HTTP 接口。

## 成员认领

用户在 OJ Wall 登录正式账号，选择 DLUT CPC 成员并填写核验说明。OJ Wall 服务提交自己的持久发布方 UUID、该账号的持久主体 UUID及目标成员 UUID；管理员登录本工程 `/admin`，进入“成员认证”页签，核验身份后批准、拒绝或撤销。登录归 OJ Wall，身份判断归本工程。

同一成员只能有一个有效认证账号；同一客户端主体也只能有一个有效认证。审核材料不出现在对外审批快照中。批准、拒绝和撤销写入审核日志，不按同名自动认证。

网页默认显示待审核申请，可按状态筛选，查看账号、成员、校区、核验说明与审核历史。操作时再次确认，审核者取当前登录管理员；结果由 OJ Wall 默认每 5 分钟同步，用户也可点击“更新认证状态”。网页复用原管理员会话、同源检查和 CSRF 校验，无新增管理员密码或跨站登录。服务同步凭据不能用于网页审核。

管理员命令继续可用，与网页共用同一审核规则和日志：

```sh
docker compose exec -T dlut-cpc python tools/cpc_admin.py meta
docker compose exec -T dlut-cpc python tools/cpc_admin.py list
docker compose exec -T dlut-cpc python tools/cpc_admin.py review 申请UUID approved --reviewer 管理员名称
```

拒绝改为 `rejected`，撤销改为 `revoked`。合并两个已认证的成员档案前，先人工处理误认领，不能靠合并自动选择哪个账号有效。

## 配置与接口

在 `.env` 配置随机 `CPC_SYNC_TOKEN`，并将同一凭据配置到 OJ Wall；留空时接口禁用。凭据不得提交 Git、放入 URL 或浏览器。先一致备份运行库，再更新容器；不能用开发 seed 覆盖生产库。

| 接口 | 用途 |
| --- | --- |
| GET `/api/integration/v1/meta` | 服务发布方 UUID |
| GET `/api/integration/v1/roster/snapshot` | 成员、确认参赛记录及成员重定向完整快照 |
| GET `/api/integration/v1/claims/snapshot?client=发布方UUID` | 指定 OJ Wall 的审批完整快照 |
| POST `/api/integration/v1/claims` | 提交认领申请，申请 UUID 支持幂等重试 |

上表接口要求 `Authorization: Bearer 服务凭据`。快照使用 schema 1、发布方 UUID、完整标识与记录数量；OJ Wall 核对后事务替换缓存。只发布 `rosterConfirmed` 的逐场名单，不能把一个成员曾加入过某队推断为参加该队所有比赛。

浏览器管理接口是 GET `/api/admin/cpc-claims` 和 POST `/api/admin/review-cpc-claim`（`claimId`、`status`）；只接受本工程现有管理员会话，POST 另需同源及 `X-CSRF-Token`。核验材料与审核历史只通过管理接口提供，不加入服务间公开字段。

OJ Wall 默认每 5 分钟拉取；失败保留上次成功数据，24 小时无法核验时暂停现场归属统计。OJ Wall 检测已批准认领后自动核对原榜单逐题成绩，正式与打星队伍的赛中 AC 同样同步。

## 原榜单入口复用

名单快照复用点击比赛名所用的 `data/contest_ranklists.json`，给每场已确认名单附可选 `ranklist_url`、原来源及原榜单行 ID、DLUT 校名别名、CPC Finder 比赛 ID；人员附稳定 CPC Finder 选手 ID。链接只指向来源，逐题抓取和 AC 证据由 OJ Wall 负责，不新增共享数据库或跨工程目录依赖。

OJ Wall 遍历已认证成员的全部已确认参赛名单，因此没有 CPC Finder 选手页也可使用 DLUT 保存的 RankLand/XCPCIO 原榜单。名单包括打星及无奖牌记录；未确认队员的名单仍不发布。CPC Finder 只有总解题数时不能当作逐题通过。

## 持久数据与迁移

新增 `cpc_ids`、`cpc_claims`、`cpc_claim_audit`，分别保存服务/成员/参赛 UUID、认领与审核历史。原成员和逐场名单表继续是业务信源；人员合并通过已有 `member_redirects` 传播。

迁移保留整个运行 SQLite 库、`data/contest_ranklists.json` 与 seed/source 数据、`.env` 同步凭据及原运行配置。使用 SQLite 备份 API 或停写备份，不能漏掉 WAL。恢复后运行 `meta` 核对原 UUID，更新 OJ Wall 的地址，保持其预期 UUID不变；同一发布方不能同时有两个生产写入端。不需要重新审核已有认领。

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
```

跨工程流程、迁移与 HTTP 验收入口位于 qq-cf-bot 的 `scripts/test_cpc_flow.py`。

## 版本记录与相关方案

- v4.2 / 2026-10-09：名单快照复用原榜单链接和学校别名，发布可选来源 ID，OJ Wall 在认证批准后自动抓取现场成绩；沿用 schema 1 和持久 UUID。
- v4 / 2026-10-09：首版采用管理员命令审核、完整快照及持久 UUID；不引入 SSO、消息队列或共享数据库。
- v4.1 / 2026-10-09：在现有 `/admin` 增加成员认证审核页，保留命令方式。共用原认领表和审核日志，服务间协议及迁移方式不变。

完整设计及版本快照：[三工程方案](https://github.com/Code92007/qq-cf-bot/blob/main/docs/cpc-cross-project-integration.md)、[迭代记录](https://github.com/Code92007/qq-cf-bot/blob/main/docs/cpc-integration/CHANGELOG.md)、[统一运维说明](https://github.com/Code92007/qq-cf-bot/blob/main/docs/cpc-integration-operations.md)。
