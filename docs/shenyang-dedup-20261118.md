# 2026-11-18 沈阳站成绩去重

CPC Finder 新收录了盘锦两队的逐人参赛记录，但来源成绩的 `school` 是本部，三名成员的校区均为盘锦。旧匹配先限定校区，导致同场、同队、同名单的官方终榜成绩没有被命中，新增了两条重复成绩。

| 队伍 | 删除的重复 ID | 保留的官方终榜 ID | 正式排名 |
| --- | --- | --- | --- |
| 再一次 | `bde7d885ab37d0` | `xcpcio-icpc2026shenyang-V04` | 228 / 401 |
| 如果转身后退就能回到那个夏天 | `2904a971a125f0` | `xcpcio-icpc2026shenyang-B09` | 205 / 401 |

合并保留官方终榜记录的 ID、ICPC 类型、排名、奖项、名单及原来源，并将 CPC Finder 来源身份、原始记录、同步映射和审核历史关联到保留记录。沈阳站由 8 行恢复为 6 支队伍，合计 1 银、3 铜、2 铁。

同步现先检查完整的三人本地成员 ID：同日、同系列、同赛站、同队名、三个 ID 全部相同且唯一匹配时，允许来源校区字段不同。来源排名、奖项等不同仍生成差异，不自动覆盖。姓名相同、身份不完整或匹配有歧义时，不按此规则合并。

维护命令默认预览并回滚，实际执行必须提供新的备份路径，操作持有公开同步锁，并在同一事务中校验两组记录与外键：

```bash
python3 -m tools.merge_duplicate_results \
  --pair bde7d885ab37d0 xcpcio-icpc2026shenyang-V04 \
  --pair 2904a971a125f0 xcpcio-icpc2026shenyang-B09

python3 -m tools.merge_duplicate_results \
  --pair bde7d885ab37d0 xcpcio-icpc2026shenyang-V04 \
  --pair 2904a971a125f0 xcpcio-icpc2026shenyang-B09 \
  --apply --backup runtime/backups/before-shenyang-merge-20261118.sqlite3
```

生产副本验证：其他成绩、成员、账号及公开统计表保持不变；重复执行、重启后仍只有 6 队；重放这两队的 CPC Finder、RankLand、XCPCIO 共 6 条来源记录，新增成绩和新增差异均为 0。
