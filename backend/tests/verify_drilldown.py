"""看板指标下钻验收脚本（验收序列 M 与全部补充边界）。

直接在服务层验证，不依赖 HTTP 客户端：
  M1  DASH-0002 从 09:00 指标下钻，只返回 09:00-10:00 窗口内 5 条异常样本
  M2  切到 DASH-0001 后两者各自保留最近一次成功更新时间（08:45 / 09:00）
  B1  09:00 刷新失败保持 08:30 快照；09:30 成功批次只替换 DASH-0002
  B2  DASH-0001 按来源 2 条、DASH-0002 按任务 5 条，维度各自携带、样本不残留
  B3  快照切换后样本数不相乘、不复用
  B4  09:30 批次失败两者保持原值；09:45 成功后分别更新，历史快照保留
  B5  同一批次并发重复刷新只生效一次
  B6  下钻结论回写执行日志详情并生成工作台异常条目
"""
from __future__ import annotations

import copy
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.seed import SEED_ROWS  # noqa: E402
from app.services.dashboard import DashboardService  # noqa: E402
from app.store import store  # noqa: E402

service = DashboardService()
PASS = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS
    assert condition, f"[{name}] 断言失败 {detail}"
    PASS += 1
    print(f"  ✓ {name} {detail}")


def reset_store() -> None:
    """每个场景从种子深拷贝重建，避免场景间互相污染。"""
    # pylint: disable=protected-access
    store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
    # 列表/字典字段也要独立拷贝。
    for rows in store._tables.values():
        for row in rows:
            for key, value in list(row.items()):
                if isinstance(value, list):
                    row[key] = copy.deepcopy(value)


def entry_id_of(code: str) -> int:
    for row in store.rows("dashboard"):
        if row["指标编号"] == code:
            return int(row["id"])
    raise KeyError(code)


# ---------------------------------------------------------------- M + B2/B3
print("场景一：验收序列 M —— 09:00 下钻 5 条、切指标后各自保留更新时间")
reset_store()
view2, msg = service.drilldown(entry_id_of("DASH-0002"))
codes2 = view2["异常样本"]
check("M1a 下钻命中 5 条", len(codes2) == 5, f"实际 {len(codes2)}")
check(
    "M1b 只返回 09:00-10:00 区间样本",
    [r["日志编号"] for r in codes2] == ["LOG-2006", "LOG-2007", "LOG-2008", "LOG-2009", "LOG-2010"],
    str([r["日志编号"] for r in codes2]),
)
check("M1c 样本按开始时间升序",
      [r["日志编号"] for r in codes2] ==
      [r["日志编号"] for r in sorted(codes2, key=lambda r: r["开始时间"])]
      and codes2[0]["日志编号"] == "LOG-2006")
check("M1d 携带任务统计维度", view2["快照信息"]["统计维度"] == "任务")
check(
    "M1e 保留指标六项元信息",
    all(view2["指标信息"][k] for k in ["指标名称", "统计维度", "计算公式", "刷新频率", "展示类型", "数据源"]),
)
check("M1f 时间范围随快照携带", view2["快照信息"]["统计起始"] == "2026-09-01 09:00:00"
      and view2["快照信息"]["统计截止"] == "2026-09-01 10:00:00")

view1, _ = service.drilldown(entry_id_of("DASH-0001"))
codes1 = view1["异常样本"]
check("B2a DASH-0001 按来源统计 2 条", len(codes1) == 2, str([r["日志编号"] for r in codes1]))
check("B2b 08:45 窗口只含 LOG-2004/2005",
      {r["日志编号"] for r in codes1} == {"LOG-2004", "LOG-2005"})
check("B2c 携带来源统计维度", view1["快照信息"]["统计维度"] == "来源")
check("B3a 两指标样本集合不相交",
      {r["日志编号"] for r in codes1}.isdisjoint({r["日志编号"] for r in codes2}))
check("B3b 样本数未相乘", view1["下钻记录"]["异常样本数"] == 2 and view2["下钻记录"]["异常样本数"] == 5)

listed, _ = service.list_entries(page=1, size=10)
last_time = {row["指标编号"]: row["最近成功更新时间"] for row in listed}
check("M2a DASH-0001 保留 08:45 更新时间", last_time["DASH-0001"] == "2026-09-01 08:45:00",
      last_time["DASH-0001"])
check("M2b DASH-0002 保留 09:00 更新时间", last_time["DASH-0002"] == "2026-09-01 09:00:00",
      last_time["DASH-0002"])

# 再切回 DASH-0002 下钻，确认旧样本不残留、数量不变
view2_again, _ = service.drilldown(entry_id_of("DASH-0002"))
check("B3c 再次下钻仍为 5 条且不残留 DASH-0001 样本",
      [r["日志编号"] for r in view2_again["异常样本"]] ==
      ["LOG-2006", "LOG-2007", "LOG-2008", "LOG-2009", "LOG-2010"])

# ---------------------------------------------------------------- B6
print("场景二：下钻结论回写日志详情 + 工作台生成异常条目")
conclusion = view2_again["下钻记录"]["下钻结论"]
log2006 = next(r for r in store.rows("log") if r["日志编号"] == "LOG-2006")
log0001 = next(r for r in store.rows("log") if r["日志编号"] == "LOG-0001")
check("B6a 结论已回写命中日志", log2006["下钻结论"] == conclusion)
check("B6b 未命中日志不被回写", log0001.get("下钻结论", "") == "")
wb = service.list_workbench(only_pending=True)
check("B6c 工作台生成两条待处理异常", len(wb) == 2, f"实际 {len(wb)}")
wb2 = next(item for item in wb if item["指标编号"] == "DASH-0002")
check("B6d 工作台条目带时间范围与维度", wb2["统计维度"] == "任务" and "09:00:00" in wb2["时间范围"])
check("B6e 工作台样本数与快照一致", wb2["异常样本数"] == 5)
# 同快照重复下钻不膨胀
before = len(service.list_workbench())
service.drilldown(entry_id_of("DASH-0002"))
check("B6f 同快照重复下钻不新增工作台条目", len(service.list_workbench()) == before)
item, message = service.run_workbench_action(int(wb2["id"]), "标记处理")
check("B6g 标记处理后移出待处理", item["状态"] == "已处理"
      and len(service.list_workbench(only_pending=True)) == 1, message)

# ---------------------------------------------------------------- B1
print("场景三：09:00 刷新失败保持 08:30 快照，09:30 成功只替换 DASH-0002")
reset_store()
# 手工构造「09:00 尚未成功」的局面：移除 09:00 快照与批次，08:30 快照恢复为当前。
snap_rows = store.rows("dashboard_snapshot")
for row in list(snap_rows):
    if row["快照编号"] == "SNAP-20260901-0900-0002":
        snap_rows.remove(row)
for row in snap_rows:
    if row["快照编号"] == "SNAP-20260901-0830-0002":
        row["is_current"] = True
store.rows("dashboard_batch")[:] = [
    row for row in store.rows("dashboard_batch") if row["批次号"] != "BATCH-20260901-0900"
]
id2 = entry_id_of("DASH-0002")
before_snap_count = len(store.rows("dashboard_snapshot"))
fail_batch, fail_msg = service.refresh_batch(
    batch_time="2026-09-01 09:00:00", metric_codes=["DASH-0002"], force_fail=True)
check("B1a 失败批次状态为失败", fail_batch["状态"] == "失败", fail_msg)
check("B1b 失败不产生新快照", len(store.rows("dashboard_snapshot")) == before_snap_count)
current2 = service.list_snapshots("DASH-0002")
check("B1c 保持 08:30 快照", current2[0]["快照编号"] == "SNAP-20260901-0830-0002"
      and current2[0]["is_current"] is True)
# 失败批次里下钻仍读到旧快照
view_fail, _ = service.drilldown(id2)
check("B1d 接口中断时下钻保留上次结果", view_fail["快照信息"]["快照编号"] == "SNAP-20260901-0830-0002")

# 09:30 成功批次只刷 DASH-0002
ok_batch, ok_msg = service.refresh_batch(
    batch_time="2026-09-01 09:30:00", metric_codes=["DASH-0002"])
check("B1e 下一批次成功继续", ok_batch["状态"] == "成功", ok_msg)
snaps2 = service.list_snapshots("DASH-0002")
check("B1f 09:30 只替换 DASH-0002 当前快照", snaps2[0]["批次号"] == "BATCH-20260901-0930"
      and snaps2[0]["is_current"] is True)
snaps1 = service.list_snapshots("DASH-0001")
check("B1g DASH-0001 未受影响仍是 08:45",
      snaps1[0]["快照编号"] == "SNAP-20260901-0845-0001" and snaps1[0]["is_current"] is True)
check("B1h 历史快照保留（08:30 仍在且 is_current=False）",
      any(s["快照编号"] == "SNAP-20260901-0830-0002" and s["is_current"] is False for s in snaps2))

# ---------------------------------------------------------------- B4/B5
print("场景四：09:30 双指标失败保持原值，09:45 成功分别更新；并发重复刷新幂等")
reset_store()
_, _ = service.refresh_batch(
    batch_time="2026-09-01 09:30:00", metric_codes=["DASH-0001", "DASH-0002"], force_fail=True)
listed, _ = service.list_entries(page=1, size=10)
last_time = {row["指标编号"]: row["最近成功更新时间"] for row in listed}
check("B4a 09:30 失败后 DASH-0001 保持 08:45", last_time["DASH-0001"] == "2026-09-01 08:45:00")
check("B4b 09:30 失败后 DASH-0002 保持 09:00", last_time["DASH-0002"] == "2026-09-01 09:00:00")

# 并发 5 个相同批次时间的刷新，只允许生效一次
results: list[dict] = []
errors: list[str] = []
barrier = threading.Barrier(5)

def worker() -> None:
    barrier.wait()
    batch, _msg = service.refresh_batch(batch_time="2026-09-01 09:45:00",
                                        metric_codes=["DASH-0001", "DASH-0002"])
    if batch is None:
        errors.append(_msg)
    else:
        results.append(batch)

threads = [threading.Thread(target=worker) for _ in range(5)]
for t in threads:
    t.start()
for t in threads:
    t.join()
batch_0945 = [b for b in store.rows("dashboard_batch") if b["批次号"] == "BATCH-20260901-0945"]
check("B5a 并发刷新只生成一个批次", len(batch_0945) == 1 and not errors, f"批次数 {len(batch_0945)}")
check("B5b 五个请求拿到同一批次号", len({b["id"] for b in results}) == 1 and len(results) == 5)
new_snaps = [s for s in store.rows("dashboard_snapshot") if s["批次号"] == "BATCH-20260901-0945"]
check("B5c 并发下每个指标只生成一份新快照", len(new_snaps) == 2, f"实际 {len(new_snaps)}")

# 串行重复调用同批次同样幂等
dup_batch, dup_msg = service.refresh_batch(batch_time="2026-09-01 09:45:00")
check("B5d 重复刷新直接复用首次结果", dup_batch["id"] == batch_0945[0]["id"] and "已执行" in dup_msg)

listed, _ = service.list_entries(page=1, size=10)
last_time = {row["指标编号"]: row["最近成功更新时间"] for row in listed}
check("B4c 09:45 成功后两者分别更新到同一批次",
      last_time["DASH-0001"] == "2026-09-01 09:45:00"
      and last_time["DASH-0002"] == "2026-09-01 09:45:00")
# 分别更新的语义：各自窗口独立、样本独立统计
v1, _ = service.drilldown(entry_id_of("DASH-0001"))
v2, _ = service.drilldown(entry_id_of("DASH-0002"))
check("B4d 两指标新快照窗口与维度各自独立",
      v1["快照信息"]["统计维度"] == "来源" and v2["快照信息"]["统计维度"] == "任务"
      and v1["快照信息"]["快照编号"] != v2["快照信息"]["快照编号"])
check("B4e 历史快照全部保留", len(store.rows("dashboard_snapshot")) == 5)

# ---------------------------------------------------------------- 历史快照下钻
print("场景五：按指定历史快照下钻，口径跟随快照而不是当前值")
reset_store()
id2 = entry_id_of("DASH-0002")
history = service.list_snapshots("DASH-0002")
old_snapshot = next(s for s in history if s["快照编号"] == "SNAP-20260901-0830-0002")
view_old, _ = service.drilldown(id2, snapshot_id=int(old_snapshot["id"]))
check("H1a 历史快照下钻样本为该窗口 3 条",
      [r["日志编号"] for r in view_old["异常样本"]] == ["LOG-2001", "LOG-2002", "LOG-2003"])
check("H1b 历史下钻携带 08:30 时间范围",
      view_old["快照信息"]["统计起始"] == "2026-09-01 08:00:00"
      and view_old["快照信息"]["统计截止"] == "2026-09-01 08:30:00")
id1 = entry_id_of("DASH-0001")
foreign = next(s for s in store.rows("dashboard_snapshot") if s["指标编号"] == "DASH-0002")
view_bad, bad_msg = service.drilldown(id1, snapshot_id=int(foreign["id"]))
check("H1c 跨指标快照被拒绝", view_bad is None and "不属于该指标" in bad_msg, bad_msg)

print(f"\n全部 {PASS} 项验收通过 ✅")
