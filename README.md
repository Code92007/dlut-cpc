# DLUT CPC

大连理工大学程序设计竞赛队的成绩、成员、Rating 与训练记录站点。首版参考 [CVBB ICPC Team](https://cvbbacm.com/home) 的信息架构，采用与 `oj-submission-wall` 相同的轻量部署思路：Python 标准库后端、静态前端、Docker Compose 和反向代理。

> 本项目是队伍信息展示站点，不代表大连理工大学官方。校徽素材来自大连理工大学[学校 VI 页面](https://www.dlut.edu.cn/xxgk/dxwh/xxVI.htm)。

## 页面

- `/home`：本地已收录的历年金、银、铜、铁成绩趋势与最近参赛。
- `/honor`：按年份、成绩、关键词检索获牌及未获牌记录。
- `/rating`：从历年获奖名单归并出的完整成员目录，以及已确认的 Codeforces 账号。
- `/training`：牛客暑期多校、杭电多校和队内训练榜单。
- `/admin`：管理员登录与成员、账号、姓名别名、历史参赛成绩补录。访客只读。
- `/pending`：获奖信息待确认成员，可按年份关键词、队伍及所属范围查找。

页面不依赖前端构建工具，图表使用原生 SVG 渲染。

## 本地运行

```bash
python3 app.py --check
python3 app.py
```

打开 `http://localhost:8000`。

运行测试：

```bash
python3 -m unittest discover -s tests -v
```

有 Node.js 的环境还可执行排序回归测试：`node --test tests/test_member_sort.mjs`。

## 数据

`data/site.json` 是可版本控制的公开数据快照，`runtime/dlut_cpc.sqlite3` 是运行时主数据库。首次启动会将快照导入 SQLite，之后自动同步只更新公开数据，不会删除人工录入的成员、账号关联或历史奖项。数据库支持同一奖项和成员关联多个来源，并用 CPC Finder 学生 UUID 区分同名成员。

当前成绩与队员名单来自 [CPC Finder 的大连理工大学学校页](https://cpcfinder.com/school/9c417252-c487-4eae-8822-fcd1e74b9329)、学校获奖 API、选手目录 API、选手参赛 API 和各赛事榜单 API，仅保留 2020 年及以后的成绩。同步脚本会逐项关联 `awardId`、`contestId`、`teamId` 与稳定的 `studentId`，核对学校后导入榜单中的三位队员；也可以用 [ICPC 参赛公示](https://icpc.pku.edu.cn/docs/20230202164632701013.pdf)、[2024 上海站结果](https://icpc.pku.edu.cn/docs/20250313164218706132.pdf)、XCPCIO、Gym 或经过核验的 QOJ 镜像榜补充或覆盖。

学校别名按队内指定范围处理：大连理工大学、本部、开发区校区及旧称软件学院统一为“大连理工大学”；城市学院、盘锦校区（盘锦学院）为两个独立维护范围。三者可在总览汇总，也可在成绩、成员和待确认页面分别筛选。保留原始榜单学校名，不因同名跨范围合并成员。CPC Finder 的校内奖牌汇总作为成员页公开基准，队内数据库仍可补录更早成员、账号和历史赛事。

### 一次性历史导入

`data/historical_honors.json` 保存从 [RankLand](https://rl.algoux.cn/search) 归档的 2010–2019 年 102 条明确获牌成绩（ICPC 81 条、CCPC 21 条），及 88 场榜单的检查报告。只接受 2020-01-01 前的区域赛、总决赛金银铜成绩，排除省赛、邀请赛、预选赛和女生赛。奖牌由官方 SRK 工具按来源榜单配置解析；18 场榜单的奖牌边界全为零、14 场赛事类型无法确认，记录在检查报告中而不猜测结果。这批明确获牌记录均属于本部/开发区；不能据此断言另两个范围没有历史获奖。

每条结果保存稳定赛事及队伍编号、原始校名、来源链接、分数和奖牌配置。首次更新启动从仓库中的本地归档导入 SQLite，并以 `historical_import:rankland-pre2020-v1` 标记完成。之后启动、公开同步均跳过此批次，不重新请求 RankLand，也不重新加入已完成的待确认项。日常运行不需要安装爬取依赖，来源网站下线不会影响已导入成绩和人工数据。归档仅收录获牌队，无法证明古早成员的完整铁牌次数；无完整公开统计的历史成员显示“铁待补”，不按 0 参与奖牌榜比较。

历史队名不能证明队员身份，因此先进入 `/pending`。10 条榜单保留的参赛姓名作为参考，不包含教练，也不自动关联到个人。管理员在 `/admin` 的“待确认成员”选择成绩后可直接填写队员姓名：同一所属范围内按姓名、显示名和报名别名匹配，唯一匹配时复用现有成员，无匹配时创建往届成员；多个同名或同别名候选时须从名单中选择具体成员 ID。榜单姓名会预填，但必须人工点击确认才写入。创建成员、人工成绩关联和完成状态在同一事务内提交，重复成员、无效输入或所属范围错误会全部回滚。确认只移出待确认列表，绝不删除比赛成绩；完整确认后才计入个人奖牌。本部与开发区可跨校区组队，城市学院、盘锦不能误关联到本部成绩。

归档已经随仓库提供，**部署时不需要再执行爬取**。仅维护者需要创建其他归档时使用可选工具：

```bash
python3 -m venv /tmp/rankland-import-venv
/tmp/rankland-import-venv/bin/pip install -r tools/requirements-rankland.txt
/tmp/rankland-import-venv/bin/python tools/import_rankland.py
```

默认输出已存在时直接停止，不请求外站。下载失败保留缓存但不写不完整快照；缓存和数据库位于忽略的运行时目录。手工确认不会修改仓库里的历史归档，生产数据库必须持续备份。

铁牌指有有效比赛名次、但没有金银铜牌的参赛成绩，包含来源标记为非正式的参赛记录，并在页面保留“非正式”标记。缺少名次或尚未确定结果不视为铁牌。铁牌次数来自选手逐场参赛记录，并与学校榜单核对；未查全的次数显示“铁待补”，不会当作 0。“奖牌榜顺序”依次按金、银、铜数量降序及铁牌数量升序排列；“获奖次数”不含铁牌。

同步 CPC Finder 并执行归一化去重：

```bash
python3 tools/sync_public_data.py --dry-run
python3 tools/sync_public_data.py
```

脚本使用 `日期 + 赛事 + 规范化队名` 作为去重键，处理全半角标点、空格和常见队名格式差异，同时更新 JSON 快照与 SQLite。来自 XCPCIO、Gym、QOJ 或官方名单的补充数据可以整理成 JSON 数组后合并：

```bash
python3 tools/sync_public_data.py \
  --supplement data/xcpcio.json \
  --supplement data/qoj.json
```

补充记录字段与 `site.json` 中的 `honors` 项一致。比赛名次等结果字段可优先采用官方或经过核验的独立榜单，但 CPC Finder 按 `awardId` 返回的成员名单不会被普通补充来源覆盖；只有显式标记 `memberRosterManual: true` 的人工修订可覆盖。每次公开同步会清除已失效的非人工来源链接，人工补录来源始终保留。

同步牛客 2025 暑期多校公开榜单，并合并 QOJ 上可核验的 2023 杭电多校 DLUT 记录：

```bash
python3 tools/sync_training_data.py --dry-run
python3 tools/sync_training_data.py
```

牛客记录按学校筛选并保留全榜名次、题目结果、通过时间与罚时；`data/hdu_training_2023.json` 保存 QOJ 公开镜像中的最终汇总记录。公开来源没有排名过程时，页面不会生成推测的排名变化曲线。

### 人工补录

账号关联的可版本控制快照放在 `data/site.json` 的 `accountBindings` 中，以 CPC Finder UUID 对应选手；该来源没有收录的成员可显式设置 `createMember: true`。每人可保存任意多个 Codeforces 账号。主号取所有账号中 `maxRating` 最高者，与加入顺序无关；最高分相同则按当前分、账号名字稳定排序。成员页分别显示主号最高分、当前分和副号列表，账号搜索包含副号。

管理员在“账号”页可追加、修改或二次确认删除已有账号。修改为不同账号后重新获取该账号评分，不沿用旧账号分数；删掉主号后自动从剩余账号中选取主号。删除记录保存在数据库的 `removed_member_handles` 中，启动与公开数据同步不会重新导入被删除的绑定；管理员明确重新追加时可以恢复。`accountCorrections` 是带唯一 ID 的一次性纠错记录，用于更新已有部署中的错误绑定，不反复覆盖管理员后续维护。杨君泓的错误绑定 `Lance_J` 已纠正为 `Farewell`。

“已补录名单”列出已确认的历史成绩及本地人工补录成绩，支持按比赛、队伍、成员和独立维护范围筛选。管理员可修改参赛名单，已有姓名 / 别名合并，未知姓名新建成员；原比赛、队伍及奖项不变，参赛关联和本地补录奖牌统计随之更新。修改事务失败时保留原名单；本地确认和修改后的名单不会被启动快照覆盖。游客仅可提交待确认成绩的补录申请，不能直接修改已确认名单。

`memberOverrides` 保存稳定选手 UUID、中文显示姓名与报名别名。例如 `Fangyu Bu` 显示为“卜方昱”，英文名仍可检索；映射同时作用于成员页和参赛成绩。管理员后续修改的显示名不会被启动快照覆盖。

拉取所有已绑定账号的当前分与历史最高分：

```bash
python3 tools/sync_codeforces.py
# 生产容器只更新已挂载数据库，不改容器内的公开快照
docker compose exec -T dlut-cpc python tools/sync_codeforces.py --database-only
```

Rating 来自 Codeforces 官方 `user.info` API。同步按账号名匹配，不依赖返回数组顺序；接口失败保留旧分数，没有评级的账号保存为空，不伪造 0。重启时仅导入不早于数据库评级更新时间的快照。CPC Finder Rating 是该平台选手资料中的独立分数，只用于展示与对应排序，不参与我们自己的奖牌榜排序。

### 网站管理员

部署或更新后，生成管理员密码并重启服务：

```bash
docker compose exec -T dlut-cpc python tools/manage_data.py init-admin
docker compose restart dlut-cpc
```

命令仅首次创建 `runtime/admin_password`（权限 `0600`），显示生成的随机密码；已存在时拒绝覆盖。用户名默认 `admin`，可通过 `ADMIN_USERNAME` 配置。密码文件在持久化卷中，不提交 Git，不从网站提供下载。也可以通过 `ADMIN_PASSWORD` 环境变量配置至少 12 位密码；未配置或密码过短时禁止登录。公开站点应始终通过 HTTPS 访问。

访问 `/admin` 登录后可补录未被 CPC Finder 收录的古早成员、追加主副账号、设置中文名和别名、录入历史成绩（金银铜铁）。成员选择含独立 ID，同名选手不会被自动合并；补录姓名已存在时需显式确认是独立同名成员。账号归属不能同时绑定两人。人工数据写入 SQLite，重建容器和公开同步均不会删除。

管理 API 使用服务端八小时会话、HttpOnly/SameSite Cookie、HTTPS Secure Cookie、同源校验与 CSRF 令牌。未登录访客不能修改正式成员或成绩，也不能查看私有审核队列；登录失败有限流，退出立即撤销会话，服务重启后需重新登录。内部备注不进入公开成员接口。

### 游客补录与审核

游客在 `/pending` 的成绩行点击“补录成员”，可填写姓名或选择已有成员，附上补录依据后“提交审核”。提交仅写入 SQLite 的 `roster_submissions`，不会创建正式成员、确认名单或增加个人奖牌；未审核姓名和说明只向管理员开放。公开列表仅显示待审核份数。

游客也可在 `/rating` 点击“补充 CF 账号”，选择带成员 ID 和所属范围的已有成员、填写账号及依据，提交到 SQLite 的 `account_submissions`。提交不会修改正式账号，也不触发外部 Rating 请求。管理员在“游客审核”中将“补录类型”切换为“CF 账号”，查看申请账号、现有主副号和依据后点击“通过”或“不通过”。通过后追加账号并拉取最高 / 当前 Rating，主副号仍按最高 Rating 排列；拉取失败不撤销已通过的绑定，可稍后刷新 Rating。审批再次核验账号归属，不能把他人的账号绑定给这位成员。游客不能删除、修改或转移已有账号，纠错仍由管理员在“账号”页处理。

管理员在 `/admin` 的“游客审核”页查看名单与依据，点击“通过”或“不通过”，不需要再手填一遍。通过时在同一事务内按该成绩所属范围复用或新建成员、确认参赛关联、写入审核人及时间；不通过仅保留审核记录，不改正式数据。名单已由其他提案或管理员确认后，同一成绩剩余提案自动失效，不能覆盖已确认名单。审核支持状态、所属范围筛选和每页 50 份的分页。

提交必须同源并使用 JSON，名单与 CF 账号申请共用每个来源 IP 十分钟最多 20 次的限流；每条成绩最多保留 5 份不同的待审核名单，每位成员最多保留 5 份不同的待审核账号申请，两类总待审核队列上限 1000 份。相同成员名单不计次序、空格、已知报名别名差异自动去重；相同成员的账号申请忽略账号大小写与两端空格去重。存在多个同名候选时要求指定成员 ID，城市学院与盘锦校区不能误合并到本部。限流计数保存在进程内，重启后重置；提案、审核历史和正式数据均持久化，重建容器或来源网站下线不会丢失。

Compose 默认 `TRUST_PROXY_HEADERS=1`，用于读取 Caddy 设置的真实客户端 IP，宿主机端口必须保持 `127.0.0.1` 绑定且只由可信反向代理访问。直接将服务端口暴露到外网时应设置 `TRUST_PROXY_HEADERS=0`，不接受客户端伪造的转发头。此次更新不需要新端口或更改 Caddy 配置；更新前备份生产 SQLite，启动会自动升级到 v8。

无法从公开网站找到的老成员直接写入 SQLite，不需要修改前端：

```bash
# 查看成员 ID
python3 tools/manage_data.py list-members --query 张三

# 新增老成员；默认允许同名成员独立存在
python3 tools/manage_data.py add-member \
  --name 张三 --entry-year 2007 --graduation-year 2011 \
  --source-name 队史补录

# 给成员绑定经本人或队内确认的 Codeforces 账号
python3 tools/manage_data.py set-handle \
  --member-id 76 --handle example_handle --rating 2100

# 再执行 set-handle 可追加副号，原账号会保留
python3 tools/manage_data.py set-handle --member-id 76 --handle another_handle

# 中文显示名与报名别名
python3 tools/manage_data.py set-name --member-id 76 --name 张三 --alias 'San Zhang'

# 新增历史奖项，并通过成员 ID 关联队员
python3 tools/manage_data.py add-honor \
  --event '2009 ICPC 亚洲区域赛' --series ICPC --date 2009-10-18 \
  --location 大连 --team 历史队伍 --medal 银牌 \
  --member-id 76 --member-id 77 --member-id 78 \
  --source-name 队史补录

python3 tools/manage_data.py list-missing
```

只有在确认补录对象就是现有同名成员时，`add-member` 才应添加 `--match-existing`。Codeforces handle 不根据姓名猜测，只收录人工或可靠来源确认的关联。

备份和导出：

```bash
python3 tools/manage_data.py backup --output backups/dlut-cpc-$(date +%F).sqlite3
python3 tools/manage_data.py export --output backups/site-merged.json
```

训练页不再包含演示场次。后续可继续从 DOMjudge、Codeforces Gym 或 QOJ 的公开榜单导入真实训练记录。

## Docker 部署

```bash
cp .env.example .env
docker compose up -d --build
docker compose ps
```

默认只监听宿主机 `127.0.0.1:8021`。`docker-compose.yml` 将宿主机 `./runtime` 挂载到容器 `/app/runtime`，重建容器不会丢失补录数据。仓库提供两份反向代理配置：

- `deploy/Caddyfile.dlut-cpc`：`dlut-cpc.wannafly.cn` 的 Caddy 示例，自动申请 HTTPS 证书。
- `deploy/nginx.dlut-cpc.conf`：Nginx HTTP 反代示例。

生产部署前需要先为 `dlut-cpc.wannafly.cn` 配置 DNS，并确认服务器上的 8021 端口未被占用。

## 更新

```bash
python3 tools/manage_data.py backup --output backups/dlut-cpc-before-update.sqlite3
git pull --ff-only
docker compose up -d --build
curl -fsS http://127.0.0.1:8021/healthz
```

公开数据同步后需要提交更新后的 `data/site.json` 并重建容器。生产环境中的人工补录只保存在已挂载的 `runtime/dlut_cpc.sqlite3`，应随服务器备份一并保留。
