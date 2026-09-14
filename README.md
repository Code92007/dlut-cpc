# DLUT CPC

大连理工大学程序设计竞赛队的成绩、成员、Rating 与训练记录站点。首版参考 [CVBB ICPC Team](https://cvbbacm.com/home) 的信息架构，采用与 `oj-submission-wall` 相同的轻量部署思路：Python 标准库后端、静态前端、Docker Compose 和反向代理。

> 本项目是队伍信息展示站点，不代表大连理工大学官方。校徽素材来自大连理工大学[学校 VI 页面](https://www.dlut.edu.cn/xxgk/dxwh/xxVI.htm)。

## 页面

- `/home`：2020 年以来奖牌趋势与最近获奖。
- `/honor`：按年份、奖项、关键词检索获奖记录。
- `/rating`：从历年获奖名单归并出的完整成员目录，以及已确认的 Codeforces 账号。
- `/training`：ICPC 风格训练榜单与排名变化图。

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

## 数据

`data/site.json` 是可版本控制的公开数据快照，`runtime/dlut_cpc.sqlite3` 是运行时主数据库。首次启动会将快照导入 SQLite，之后自动同步只更新公开数据，不会删除人工录入的成员、账号关联或历史奖项。数据库支持同一奖项和成员关联多个来源，并用 CPC Finder 学生 UUID 区分同名成员。

当前奖牌与队员名单来自 [CPC Finder 的大连理工大学学校页](https://cpcfinder.com/school/9c417252-c487-4eae-8822-fcd1e74b9329)、学校获奖 API 和各赛事榜单 API，仅保留 2020 年及以后的金、银、铜牌记录。同步脚本会逐项关联 `awardId`、`contestId` 与 `teamId`，再导入榜单中的三位队员；也可以用 [ICPC 参赛公示](https://icpc.pku.edu.cn/docs/20230202164632701013.pdf)、[2024 上海站结果](https://icpc.pku.edu.cn/docs/20250313164218706132.pdf)、XCPCIO、Gym 或 [QOJ/UCup 镜像榜](https://contest.ucup.ac/results/QOJ1821?v=1)补充或覆盖。

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

补充记录字段与 `site.json` 中的 `honors` 项一致。来源冲突时，包含成员的补充记录优先，官方或其他独立榜单优先于 CPC Finder，并保留全部来源用于追溯。

### 人工补录

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

训练页当前包含明确标记的演示数据，用于确定首版界面；后续可从 DOMjudge、Codeforces Gym 或 QOJ 的公开榜单导入真实训练记录。

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
