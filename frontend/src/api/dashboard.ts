/** 采集看板下钻链路接口封装：批次刷新、快照、下钻、工作台。 */
import { request } from '@/api/client'
import type {
  DashboardMetric,
  DrilldownView,
  ListEnvelope,
  RefreshBatch,
  Snapshot,
  WorkbenchItem,
} from '@/types/dashboard'

const ENDPOINT = '/api/dashboard'

export interface PageEnvelope<T> {
  items: T[]
  total: number
  page: number
  size: number
}

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null
    throw new Error(payload?.detail ?? `接口返回 ${response.status}`)
  }
  return (await response.json()) as T
}

export async function fetchMetrics(): Promise<DashboardMetric[]> {
  const response = await request(`${ENDPOINT}?page=1&size=100`)
  const payload = await readJson<PageEnvelope<DashboardMetric>>(response)
  return payload.items
}

export async function refreshBatch(body: {
  batch_time?: string
  metric_codes?: string[]
  force_fail?: boolean
  remark?: string
}): Promise<{ ok: boolean; message: string; entry: RefreshBatch }> {
  const response = await request(`${ENDPOINT}/refresh`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  return readJson(response)
}

export async function fetchBatches(): Promise<RefreshBatch[]> {
  const response = await request(`${ENDPOINT}/batches`)
  return (await readJson<ListEnvelope<RefreshBatch>>(response)).items
}

export async function fetchSnapshots(metricCode: string): Promise<Snapshot[]> {
  const response = await request(`${ENDPOINT}/snapshots?metric_code=${encodeURIComponent(metricCode)}`)
  return (await readJson<ListEnvelope<Snapshot>>(response)).items
}

export async function fetchDrilldown(entryId: number, snapshotId?: number): Promise<DrilldownView> {
  const query = snapshotId ? `?snapshot_id=${snapshotId}` : ''
  const response = await request(`${ENDPOINT}/${entryId}/drilldown${query}`)
  return readJson(response)
}

export async function fetchWorkbench(onlyPending = false): Promise<WorkbenchItem[]> {
  const response = await request(`${ENDPOINT}/workbench?only_pending=${onlyPending ? 'true' : 'false'}`)
  return (await readJson<ListEnvelope<WorkbenchItem>>(response)).items
}

export async function runWorkbenchAction(
  itemId: number,
  action: '标记处理' | '重新打开',
): Promise<{ ok: boolean; message: string }> {
  const response = await request(`${ENDPOINT}/workbench/${itemId}/actions`, {
    method: 'POST',
    body: JSON.stringify({ values: { action } }),
  })
  return readJson(response)
}
