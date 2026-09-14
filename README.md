# DLUT CPC

大连理工大学程序设计竞赛队的成绩、成员、Rating 与训练记录站点。首版参考 [CVBB ICPC Team](https://cvbbacm.com/home) 的信息架构，采用与 `oj-submission-wall` 相同的轻量部署思路：Python 标准库后端、静态前端、Docker Compose 和反向代理。

> 本项目是队伍信息展示站点，不代表大连理工大学官方。校徽素材来自大连理工大学[学校 VI 页面](https://www.dlut.edu.cn/xxgk/dxwh/xxVI.htm)。

## 页面

- `/home`：2020 年以来奖牌趋势与最近获奖。
- `/honor`：按年份、奖项、关键词检索获奖记录。
- `/rating`：成员与 Codeforces 账号关联表、队伍奖牌积分。
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

站点数据位于 `data/site.json`。首版奖牌汇总来自 [CPC Finder 的大连理工大学学校页](https://cpcfinder.com/school/9c417252-c487-4eae-8822-fcd1e74b9329)及其公开获奖 API，仅保留 2020 年及以后的金、银、铜牌记录。成员信息只在能通过官方名单或公开镜像榜核验时写入，例如 [ICPC 参赛公示](https://icpc.pku.edu.cn/docs/20230202164632701013.pdf)、[2024 上海站结果](https://icpc.pku.edu.cn/docs/20250313164218706132.pdf) 和 [QOJ/UCup 镜像榜](https://contest.ucup.ac/results/QOJ1821?v=1)。

同步 CPC Finder 并执行归一化去重：

```bash
python3 tools/sync_public_data.py --dry-run
python3 tools/sync_public_data.py
```

脚本使用 `日期 + 赛事 + 规范化队名` 作为去重键，处理全半角标点、空格和常见队名格式差异。来自 XCPCIO、Gym、QOJ 或官方名单的补充数据可以整理成 JSON 数组后合并：

```bash
python3 tools/sync_public_data.py \
  --supplement data/xcpcio.json \
  --supplement data/qoj.json
```

补充记录字段与 `site.json` 中的 `honors` 项一致。来源冲突时，包含成员的补充记录优先，非 CPC Finder 来源优先保留。

Codeforces handle 暂不根据姓名猜测。成员确认账号后，在 `ratingGroups[].members[]` 填入 `handle` 与 `rating` 即可显示对应颜色。

训练页当前包含明确标记的演示数据，用于确定首版界面；后续可从 DOMjudge、Codeforces Gym 或 QOJ 的公开榜单导入真实训练记录。

## Docker 部署

```bash
cp .env.example .env
docker compose up -d --build
docker compose ps
```

默认只监听宿主机 `127.0.0.1:8021`。仓库提供两份反向代理配置：

- `deploy/Caddyfile.dlut-cpc`：`dlut-cpc.wannafly.cn` 的 Caddy 示例，自动申请 HTTPS 证书。
- `deploy/nginx.dlut-cpc.conf`：Nginx HTTP 反代示例。

生产部署前需要先为 `dlut-cpc.wannafly.cn` 配置 DNS，并确认服务器上的 8021 端口未被占用。

## 更新

```bash
git pull --ff-only
docker compose up -d --build
```

数据同步后需要重新构建容器；也可以把 `data/site.json` 作为只读卷挂载到 `/app/data/site.json`，实现不重建镜像更新。
