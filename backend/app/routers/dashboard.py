"""采集看板接口。

除指标维护与状态流转外，提供看板指标下钻链路所需接口：
固定刷新批次、历史快照、带时间范围与统计维度的下钻独立视图、工作台异常条目。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.dashboard import DashboardService

router = APIRouter(prefix="/api/dashboard", tags=["采集看板"])

service = DashboardService()

LIST_FIELDS = ["指标编号", "指标名称", "统计维度", "计算公式", "刷新频率", "展示类型", "数据源", "是否展示"]
STATUSES = ["草稿", "待校验", "已启用", "已停用"]


class RefreshPayload(BaseModel):
    """刷新批次入参：批次时间决定批次号；force_fail 用于模拟接口中断。"""

    batch_time: str | None = None
    metric_codes: list[str] | None = None
    force_fail: bool = False
    remark: str | None = None


class DrilldownQuery(BaseModel):
    snapshot_id: int | None = None


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按指标编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、已启用、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按指标编号与状态过滤采集看板列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条看板指标，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="看板指标已登记", entry=entry)


@router.post("/refresh", response_model=ActionResult)
def refresh_batch(payload: RefreshPayload) -> ActionResult:
    """触发固定刷新批次：同批次号重复/并发刷新只生效一次，失败批次不替换任何快照。"""
    batch, message = service.refresh_batch(
        batch_time=payload.batch_time,
        metric_codes=payload.metric_codes,
        force_fail=payload.force_fail,
        remark=payload.remark,
    )
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=batch.get("状态") != "失败", message=message, entry=batch)


@router.get("/batches")
def list_batches() -> dict[str, Any]:
    """批次执行履历：成功与失败批次都保留，便于核对中断后从下一批次继续。"""
    items = service.list_batches()
    return {"module": "dashboard_batch", "total": len(items), "items": items}


@router.get("/snapshots")
def list_snapshots(
    metric_code: str = Query(description="指标编号，如 DASH-0002"),
) -> dict[str, Any]:
    """单个指标的历史快照，只增不删；当前快照以 is_current=true 标记。"""
    items = service.list_snapshots(metric_code)
    return {"module": "dashboard_snapshot", "total": len(items), "items": items}


@router.get("/workbench")
def list_workbench(
    only_pending: bool = Query(default=False, description="只看待处理异常条目"),
) -> dict[str, Any]:
    """工作台异常条目：下钻结论回写后生成，进入待处理清单。"""
    items = service.list_workbench(only_pending=only_pending)
    return {"module": "dashboard_workbench", "total": len(items), "items": items}


@router.post("/workbench/{item_id}/actions", response_model=ActionResult)
def run_workbench_action(item_id: int, payload: EntryPayload) -> ActionResult:
    """工作台条目支持标记处理与重新打开。"""
    action = str(payload.values.get("action") or "").strip()
    item, message = service.run_workbench_action(item_id, action)
    if item is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=item)


@router.get("/drilldowns")
def list_drilldowns(
    metric_code: str | None = Query(default=None, description="可选：只看某个指标的下钻记录"),
) -> dict[str, Any]:
    """下钻执行履历：每次下钻都留有结论、时间范围与统计维度。"""
    items = service.list_drilldowns(metric_code)
    return {"module": "dashboard_drilldown", "total": len(items), "items": items}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出采集看板清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "dashboard", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条看板指标明细（含最近一次成功快照时间）；不存在时给出可读错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"看板指标 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/drilldown")
def drilldown(
    entry_id: int,
    snapshot_id: int | None = Query(default=None, description="不传则下钻该指标当前快照"),
) -> dict[str, Any]:
    """下钻独立视图：携带快照时间范围与统计维度返回异常样本，样本按时间排列。"""
    view, message = service.drilldown(entry_id, snapshot_id=snapshot_id)
    if view is None:
        raise HTTPException(status_code=404, detail=message)
    view["message"] = message
    return view


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条看板指标执行提交校验、启用配置、停用配置；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
