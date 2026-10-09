# DLUT CPC 三工程联动说明

2026-10-09 · v4 首版。此文与本工程代码一同提交；后续调整保留 Git 历史，重大协议变更追加版本记录。

## 职责与数据流

DLUT CPC 是人员、已确认逐场队伍名单和真人认领审核的信源。OJ Wall 拉取这些数据，结合它自己的线上提交与核验后的现场逐题榜单；CF Bot 再读取 OJ Wall 的本人进度，与自己的口胡记录合并。

本工程不读取另两个工程的数据库，不维护提交或口胡副本，不新增统一登录服务。三个服务可分别部署，通过配置地址访问 HTTP 接口。

## 成员认领

用户在 OJ Wall 登录正式账号，选择 DLUT CPC 成员并填写核验说明。OJ Wall 服务提交自己的持久发布方 UUID、该账号的持久主体 UUID及目标成员 UUID；管理员通过命令核验身份后批准、拒绝或撤销。登录归 OJ Wall，身份判断归本工程。

同一成员只能有一个有效认证账号；同一客户端主体也只能有一个有效认证。审核材料不出现在对外审批快照中。批准、拒绝和撤销写入审核日志，不按同名自动认证。

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

所有接口要求 `Authorization: Bearer 服务凭据`。快照使用 schema 1、发布方 UUID、完整标识与记录数量；OJ Wall 核对后事务替换缓存。只发布 `rosterConfirmed` 的逐场名单，不能把一个成员曾加入过某队推断为参加该队所有比赛。

OJ Wall 默认每 5 分钟拉取；失败保留上次成功数据，24 小时无法核验时暂停现场归属统计。现场榜单的比赛、队伍行与题序仍需 OJ Wall 管理员核对。

## 持久数据与迁移

新增 `cpc_ids`、`cpc_claims`、`cpc_claim_audit`，分别保存服务/成员/参赛 UUID、认领与审核历史。原成员和逐场名单表继续是业务信源；人员合并通过已有 `member_redirects` 传播。

迁移保留整个运行 SQLite 库、`.env` 同步凭据及原运行配置。使用 SQLite 备份 API 或停写备份，不能漏掉 WAL。恢复后运行 `meta` 核对原 UUID，更新 OJ Wall 的地址，保持其预期 UUID不变；同一发布方不能同时有两个生产写入端。不需要重新审核已有认领。

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
```

跨工程流程、迁移与 HTTP 验收入口位于 qq-cf-bot 的 `scripts/test_cpc_flow.py`。

## 版本记录与相关方案

- v4 / 2026-10-09：首版采用管理员命令审核、完整快照及持久 UUID；不引入 SSO、消息队列或共享数据库。

完整设计及版本快照：[三工程方案](https://github.com/Code92007/qq-cf-bot/blob/main/docs/cpc-cross-project-integration.md)、[迭代记录](https://github.com/Code92007/qq-cf-bot/blob/main/docs/cpc-integration/CHANGELOG.md)、[统一运维说明](https://github.com/Code92007/qq-cf-bot/blob/main/docs/cpc-integration-operations.md)。
