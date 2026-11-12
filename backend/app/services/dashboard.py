"""采集看板业务规则。

在原有指标登记与状态流转之外，这里承载看板指标下钻的完整链路：

1. 固定刷新批次：同批次号重复/并发刷新只生效一次；批次内每个指标读取同一快照口径。
2. 快照只增不删：刷新失败保留上一次成功快照，成功批次只替换本批次涉及指标的当前快照。
3. 指标下钻：带着快照的时间范围与统计维度读取异常执行日志，样本按时间排列，绝不跨快照复用。
4. 结论回写：下钻结论写回执行日志详情，并在工作台生成可下钻的异常条目（待处理清单）。
"""
from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta
from typing import Any

from app.store import store

MODULE = "dashboard"
BATCH_MODULE = "dashboard_batch"
SNAPSHOT_MODULE = "dashboard_snapshot"
DRILL_MODULE = "dashboard_drilldown"
WORKBENCH_MODULE = "dashboard_workbench"
LOG_MODULE = "log"

REQUIRED_FIELDS = ["指标编号", "指标名称", "统计维度"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用配置": "已启用", "停用配置": "已停用"}
NEGATIVE_ACTIONS = ["停用配置"]

# 下钻独立视图必须原样保留的指标元信息，切换指标时随快照一起带走。
METADATA_FIELDS = ["指标编号", "指标名称", "统计维度", "计算公式", "刷新频率", "展示类型", "数据源"]
DIMENSION_FIELD = {"来源": "来源标识", "任务": "关联任务"}

_TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")

# 进程内刷新锁：保证同一批次的并发请求被串行判定，第二个请求直接复用首个结果。
_refresh_lock = threading.RLock()


def _parse_dt(value: Any) -> datetime | None:
    """宽松解析时间字符串；解析不了就返回 None，由调用方决定过滤口径。"""
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _format_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _window_minutes(refresh_freq: str | None) -> int:
    """从「每15分钟 / 每30分钟 / 每60分钟」这类刷新频率里取窗口长度，缺省 60 分钟。"""
    match = re.search(r"(\d+)", str(refresh_freq or ""))
    if not match:
        return 60
    return max(int(match.group(1)), 1)


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


class DashboardService:
    # ---------------------------------------------------------------- 指标清单
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("指标编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        page_rows = [dict(row) for row in rows[start:start + size]]
        # 列表直接带出每个指标最近一次成功快照，页面无需逐行再查。
        for row in page_rows:
            current = self._current_snapshot(str(row.get("指标编号", "")))
            row["最近成功更新时间"] = current["批次时间"] if current else None
            row["当前快照编号"] = current["快照编号"] if current else None
            row["异常样本数"] = int(current["异常样本数"]) if current else 0
        return page_rows, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        detail = dict(entry)
        current = self._current_snapshot(str(entry.get("指标编号", "")))
        detail["最近成功更新时间"] = current["批次时间"] if current else None
        detail["当前快照编号"] = current["快照编号"] if current else None
        return detail

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": _next_id(rows)}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"看板指标 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于采集看板可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"看板指标已{action}"

    # ---------------------------------------------------------------- 刷新批次
    def list_batches(self) -> list[dict[str, Any]]:
        """批次执行履历，按批次时间倒序，方便核对失败批次与中断后续跑情况。"""
        return sorted(store.rows(BATCH_MODULE), key=lambda row: str(row.get("批次时间", "")), reverse=True)

    def refresh_batch(
        self,
        *,
        batch_time: str | None = None,
        metric_codes: list[str] | None = None,
        force_fail: bool = False,
        remark: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """触发一个固定刷新批次。

        - 批次号由批次时间决定，同批次号并发/重复刷新只生效一次，后续调用直接拿首个结果。
        - force_fail 模拟接口中断：批次记失败，不生成任何快照，各指标保留上次成功快照，
          下一批次换个批次时间即可继续，不依赖失败批次的半成品。
        - 成功批次只替换本批次涉及指标的当前快照，其它指标的快照原封不动。
        """
        with _refresh_lock:
            moment = _parse_dt(batch_time) or datetime.now().replace(microsecond=0)
            batch_no = f"BATCH-{moment.strftime('%Y%m%d-%H%M')}"

            existing = self._find_batch(batch_no)
            if existing is not None:
                # 同一批次并发重复刷新：首个结果为准，直接返回，不再产生第二份快照。
                return existing, f"批次 {batch_no} 已执行，按首次结果返回，重复刷新未再生效"

            targets = self._target_entries(metric_codes)
            if not targets:
                return None, "批次内没有可刷新的看板指标，请检查指标编号"

            batch_rows = store.rows(BATCH_MODULE)
            batch = {
                "id": _next_id(batch_rows),
                "批次号": batch_no,
                "批次时间": _format_dt(moment),
                "状态": "进行中",
                "涉及指标": [str(row.get("指标编号", "")) for row in targets],
                "结果说明": "",
            }
            batch_rows.append(batch)

            if force_fail:
                # 接口中断：只落失败批次，不动任何快照，前端继续展示上一次成功结果。
                batch["状态"] = "失败"
                batch["结果说明"] = remark or "刷新接口中断，本批次未生成快照，各指标保留上一次成功快照"
                return batch, batch["结果说明"]

            refreshed: list[str] = []
            for entry in targets:
                snapshot = self._build_snapshot(entry, batch_no, moment)
                store.rows(SNAPSHOT_MODULE).append(snapshot)
                refreshed.append(str(entry.get("指标编号", "")))

            batch["状态"] = "成功"
            batch["涉及指标"] = refreshed
            batch["结果说明"] = remark or f"批次成功，{('、'.join(refreshed))} 已切换到新快照"
            return batch, batch["结果说明"]

    # ---------------------------------------------------------------- 快照
    def list_snapshots(self, metric_code: str) -> list[dict[str, Any]]:
        """历史指标快照：只增不删，按批次时间倒序返回。"""
        rows = [
            dict(row)
            for row in store.rows(SNAPSHOT_MODULE)
            if str(row.get("指标编号", "")) == metric_code
        ]
        return sorted(rows, key=lambda row: str(row.get("批次时间", "")), reverse=True)

    def _current_snapshot(self, metric_code: str) -> dict[str, Any] | None:
        candidates = [
            row
            for row in store.rows(SNAPSHOT_MODULE)
            if str(row.get("指标编号", "")) == metric_code and row.get("is_current")
        ]
        # 冲突时以最近一次成功快照为准。
        candidates = [row for row in candidates if row.get("状态") == "成功"]
        if not candidates:
            return None
        return sorted(candidates, key=lambda row: str(row.get("批次时间", "")))[-1]

    def _find_snapshot(self, snapshot_id: int) -> dict[str, Any] | None:
        return store.find(SNAPSHOT_MODULE, snapshot_id)

    def _build_snapshot(self, entry: dict[str, Any], batch_no: str, moment: datetime) -> dict[str, Any]:
        metric_code = str(entry.get("指标编号", ""))
        dimension = str(entry.get("统计维度", ""))
        window = _window_minutes(entry.get("刷新频率"))
        start = moment - timedelta(minutes=window)
        end = moment

        samples = self._abnormal_logs_in_window(start, end)
        dim_field = DIMENSION_FIELD.get(dimension, "关联任务")
        agg: dict[str, int] = {}
        for sample in samples:
            dim_value = str(sample.get(dim_field) or "未归类")
            agg[dim_value] = agg.get(dim_value, 0) + 1
        summary = [
            {"维度值": dim_value, "异常数": count}
            for dim_value, count in sorted(agg.items(), key=lambda item: (-item[1], item[0]))
        ]

        # 新快照成功落库前，先把该指标旧的当前快照摘掉，但历史行保留。
        for old in store.rows(SNAPSHOT_MODULE):
            if str(old.get("指标编号", "")) == metric_code and old.get("is_current"):
                old["is_current"] = False

        snapshot_rows = store.rows(SNAPSHOT_MODULE)
        return {
            "id": _next_id(snapshot_rows),
            "快照编号": f"SNAP-{moment.strftime('%Y%m%d-%H%M')}-{metric_code}",
            "批次号": batch_no,
            "指标编号": metric_code,
            "批次时间": _format_dt(moment),
            "统计起始": _format_dt(start),
            "统计截止": _format_dt(end),
            "统计维度": dimension,
            "异常样本数": len(samples),
            "异常日志编号": [str(row.get("日志编号", "")) for row in samples],
            "汇总值": summary,
            "状态": "成功",
            "is_current": True,
        }

    # ---------------------------------------------------------------- 下钻
    def drilldown(
        self,
        entry_id: int,
        *,
        snapshot_id: int | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """从指标进入下钻独立视图。

        时间范围与统计维度全部取自快照：异常样本必须同时满足「属于该快照引用的日志」
        与「开始时间落在快照窗口内」，因此切换指标/快照后旧样本不会残留，也不会相乘复用。
        """
        with _refresh_lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"看板指标 {entry_id} 不存在或已归档"
            metric_code = str(entry.get("指标编号", ""))

            if snapshot_id is not None:
                snapshot = self._find_snapshot(snapshot_id)
                if snapshot is None or str(snapshot.get("指标编号", "")) != metric_code:
                    return None, "所选快照不属于该指标，无法下钻"
            else:
                snapshot = self._current_snapshot(metric_code)
            if snapshot is None:
                return None, f"{metric_code} 还没有成功快照，请先执行刷新批次"

            start = _parse_dt(snapshot.get("统计起始"))
            end = _parse_dt(snapshot.get("统计截止"))
            referenced = {str(code) for code in snapshot.get("异常日志编号", [])}
            samples = [
                row
                for row in self._abnormal_logs_in_window(start, end)
                if str(row.get("日志编号", "")) in referenced
            ]
            # 双保险：快照引用与窗口取交集，样本数以实际交集为准，绝不跨快照复用。
            sample_codes = [str(row.get("日志编号", "")) for row in samples]

            now = datetime.now().replace(microsecond=0)
            conclusion = (
                f"{metric_code} 依据快照 {snapshot['快照编号']} 下钻："
                f"按{snapshot['统计维度']}维度统计 {snapshot['统计起始']} 至 {snapshot['统计截止']} "
                f"窗口内异常执行日志 {len(samples)} 条。"
            )

            drill_rows = store.rows(DRILL_MODULE)
            drill = {
                "id": _next_id(drill_rows),
                "下钻编号": f"DRILL-{now.strftime('%Y%m%d-%H%M%S')}-{metric_code}",
                "指标编号": metric_code,
                "快照编号": snapshot["快照编号"],
                "批次号": snapshot["批次号"],
                "统计维度": snapshot["统计维度"],
                "统计起始": snapshot["统计起始"],
                "统计截止": snapshot["统计截止"],
                "异常样本数": len(samples),
                "异常日志编号": sample_codes,
                "下钻时间": _format_dt(now),
                "下钻结论": conclusion,
            }
            drill_rows.append(drill)

            # 下钻结论回写执行日志详情。
            for log_row in store.rows(LOG_MODULE):
                if str(log_row.get("日志编号", "")) in sample_codes:
                    log_row["下钻结论"] = conclusion
                    log_row["pending"] = True
                else:
                    log_row.setdefault("下钻结论", "")

            work_item = self._upsert_workbench(entry, snapshot, drill, sample_codes, now)

            view = {
                "下钻记录": drill,
                "指标信息": {field: entry.get(field) for field in METADATA_FIELDS},
                "快照信息": {
                    key: snapshot.get(key)
                    for key in ("快照编号", "批次号", "批次时间", "统计起始", "统计截止", "统计维度", "异常样本数", "汇总值")
                },
                "异常样本": samples,
                "工作台条目": work_item,
            }
            return view, f"下钻完成，命中 {len(samples)} 条异常样本，结论已回写执行日志并同步工作台"

    def list_drilldowns(self, metric_code: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(DRILL_MODULE)
        if metric_code:
            rows = [row for row in rows if str(row.get("指标编号", "")) == metric_code]
        return sorted(rows, key=lambda row: str(row.get("下钻时间", "")), reverse=True)

    # ---------------------------------------------------------------- 工作台
    def list_workbench(self, *, only_pending: bool = False) -> list[dict[str, Any]]:
        rows = store.rows(WORKBENCH_MODULE)
        if only_pending:
            rows = [row for row in rows if row.get("状态") == "待处理"]
        return sorted(rows, key=lambda row: str(row.get("生成时间", "")), reverse=True)

    def run_workbench_action(self, item_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        item = store.find(WORKBENCH_MODULE, item_id)
        if item is None:
            return None, f"工作台条目 {item_id} 不存在或已清理"
        if action == "标记处理":
            item["状态"] = "已处理"
            return item, "异常条目已标记处理，待处理清单同步移除"
        if action == "重新打开":
            item["状态"] = "待处理"
            return item, "异常条目已重新打开，可再次下钻"
        return None, f"动作「{action}」不属于工作台可执行范围"

    def _upsert_workbench(
        self,
        entry: dict[str, Any],
        snapshot: dict[str, Any],
        drill: dict[str, Any],
        sample_codes: list[str],
        now: datetime,
    ) -> dict[str, Any]:
        # 同一快照重复下钻只保留一个工作台条目，更新样本与下钻编号，避免待处理清单膨胀。
        for item in store.rows(WORKBENCH_MODULE):
            if str(item.get("快照编号", "")) == str(snapshot.get("快照编号", "")):
                item["下钻编号"] = drill["下钻编号"]
                item["异常样本数"] = len(sample_codes)
                item["异常日志编号"] = sample_codes
                item["生成时间"] = _format_dt(now)
                item["状态"] = "待处理"
                return item

        rows = store.rows(WORKBENCH_MODULE)
        item = {
            "id": _next_id(rows),
            "工作台编号": f"WB-{now.strftime('%Y%m%d-%H%M%S')}-{snapshot['指标编号']}",
            "指标编号": snapshot["指标编号"],
            "指标名称": entry.get("指标名称"),
            "快照编号": snapshot["快照编号"],
            "下钻编号": drill["下钻编号"],
            "统计维度": snapshot["统计维度"],
            "时间范围": f"{snapshot['统计起始']} ~ {snapshot['统计截止']}",
            "异常样本数": len(sample_codes),
            "异常日志编号": sample_codes,
            "状态": "待处理",
            "生成时间": _format_dt(now),
        }
        rows.append(item)
        return item

    # ---------------------------------------------------------------- 内部工具
    def _find_batch(self, batch_no: str) -> dict[str, Any] | None:
        for row in store.rows(BATCH_MODULE):
            if str(row.get("批次号", "")) == batch_no:
                return row
        return None

    def _target_entries(self, metric_codes: list[str] | None) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        if not metric_codes:
            return list(rows)
        wanted = {str(code).strip() for code in metric_codes if str(code).strip()}
        return [row for row in rows if str(row.get("指标编号", "")) in wanted]

    def _abnormal_logs_in_window(
        self,
        start: datetime | None,
        end: datetime | None,
    ) -> list[dict[str, Any]]:
        """取窗口内的异常执行日志，并按开始时间升序排列（首屏即按时间呈现样本）。"""
        samples: list[dict[str, Any]] = []
        for row in store.rows(LOG_MODULE):
            if not row.get("abnormal"):
                continue
            started = _parse_dt(row.get("开始时间"))
            if started is None:
                continue
            if start is not None and started < start:
                continue
            if end is not None and started > end:
                continue
            samples.append(row)
        return sorted(samples, key=lambda row: str(row.get("开始时间", "")))
