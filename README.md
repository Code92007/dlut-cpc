# DLUT CPC

大连理工大学程序设计竞赛队的成绩、成员、Rating 与训练记录站点。首版参考 [CVBB ICPC Team](https://cvbbacm.com/home) 的信息架构，采用与 `oj-submission-wall` 相同的轻量部署思路：Python 标准库后端、静态前端、Docker Compose 和反向代理。

> 本项目是队伍信息展示站点，不代表大连理工大学官方。校徽素材来自大连理工大学[学校 VI 页面](https://www.dlut.edu.cn/xxgk/dxwh/xxVI.htm)。

## 页面

- `/home`：2020 年以来金、银、铜、铁成绩趋势与最近参赛。
- `/honor`：按年份、成绩、关键词检索获牌及未获牌记录。
- `/rating`：从历年获奖名单归并出的完整成员目录，以及已确认的 Codeforces 账号。
- `/training`：牛客暑期多校、杭电多校和队内训练榜单。
- `/admin`：管理员登录与成员、账号、姓名别名、历史参赛成绩补录。访客只读。

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

选手目录收录大连理工大学主校区及盘锦校区，显式排除查询结果中名称相似但并非本校 CPC 队的“大连理工大学城市学院”。CPC Finder 的校内奖牌汇总作为成员页奖牌数的公开基准，队内数据库仍可补录更早成员、账号和历史赛事。

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

管理 API 使用服务端八小时会话、HttpOnly/SameSite Cookie、HTTPS Secure Cookie、同源校验与 CSRF 令牌。未登录访客不能执行写入操作；登录失败有限流，退出立即撤销会话，服务重启后需重新登录。内部备注不进入公开成员接口。

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
