<template>
  <section class="page" data-module="dashboard">
    <header class="page-head">
      <div>
        <h2>采集看板管理</h2>
        <p class="page-desc">
          维护看板指标与固定刷新批次；指标按批次读取同一快照，下钻携带时间范围与统计维度定位异常样本。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记看板指标</button>
        <button class="btn" type="button" @click="exportRows">导出采集看板清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">看板指标总数</span>
        <strong class="stat-value">{{ rows.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待处理异常条目</span>
        <strong class="stat-value">{{ pendingWorkbench.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">失败刷新批次</span>
        <strong class="stat-value">{{ failedBatchCount }}</strong>
      </article>
    </div>

    <!-- 固定刷新批次：同批次时间重复/并发刷新只生效一次，可勾选模拟接口中断 -->
    <form class="panel" @submit.prevent="submitRefresh">
      <h3 class="panel-title">固定刷新批次</h3>
      <div class="filter-bar">
        <label class="filter-item">
          <span>批次时间</span>
          <input v-model="refreshForm.batchTime" type="datetime-local" step="60" required />
        </label>
        <label class="filter-item">
          <span>刷新指标（不选则全部）</span>
          <span class="check-group">
            <label v-for="metric in rows" :key="metric.指标编号" class="check-pill">
              <input v-model="refreshForm.metricCodes" type="checkbox" :value="metric.指标编号" />
              {{ metric.指标编号 }}
            </label>
          </span>
        </label>
        <label class="filter-item check-pill">
          <input v-model="refreshForm.forceFail" type="checkbox" />
          <span>模拟接口中断（批次失败、保留上次快照）</span>
        </label>
        <button class="btn primary" type="submit" :disabled="refreshing">
          {{ refreshing ? '刷新中…' : '触发刷新批次' }}
        </button>
      </div>
      <p v-if="refreshMessage" :class="refreshOk ? 'ok-text' : 'error-text'">{{ refreshMessage }}</p>
    </form>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>最近成功更新时间</th>
          <th>异常样本数</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in filteredRows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ cell(row, column) }}</td>
          <td>{{ row.最近成功更新时间 ?? '暂无成功快照' }}</td>
          <td>
            <button class="link" type="button" @click="openDrilldown(row)">
              {{ row.异常样本数 }} 条（下钻）
            </button>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" @click="openSnapshots(row)">历史快照</button>
          </td>
        </tr>
        <tr v-if="!filteredRows.length">
          <td :colspan="columns.length + 3" class="empty-state">暂无采集看板数据，可先登记看板指标</td>
        </tr>
      </tbody>
    </table>

    <!-- 历史指标快照：只增不删，可对任意历史快照再次下钻 -->
    <section v-if="snapshotMetric" class="panel">
      <header class="panel-head">
        <h3 class="panel-title">{{ snapshotMetric.指标编号 }} 历史指标快照（{{ snapshots.length }} 份）</h3>
        <button class="btn ghost" type="button" @click="snapshotMetric = null">关闭</button>
      </header>
      <table class="data-table inner-table">
        <thead>
          <tr>
            <th>快照编号</th><th>批次时间</th><th>统计时间范围</th><th>统计维度</th>
            <th>异常样本数</th><th>状态</th><th>当前快照</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="snap in snapshots" :key="snap.快照编号">
            <td>{{ snap.快照编号 }}</td>
            <td>{{ snap.批次时间 }}</td>
            <td>{{ snap.统计起始 }} ~ {{ snap.统计截止 }}</td>
            <td><span class="dim-tag">{{ snap.统计维度 }}</span></td>
            <td>{{ snap.异常样本数 }}</td>
            <td>{{ snap.状态 }}</td>
            <td>{{ snap.is_current ? '是' : '否' }}</td>
            <td>
              <button class="link" type="button" @click="openDrilldown(snapshotMetric, snap.id)">
                按此快照下钻
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 工作台：下钻结论回写后生成的异常条目，进入待处理清单 -->
    <section class="panel">
      <header class="panel-head">
        <h3 class="panel-title">工作台异常清单（待处理 {{ pendingWorkbench.length }} / 全部 {{ workbench.length }}）</h3>
        <button class="btn ghost" type="button" @click="loadWorkbench">刷新清单</button>
      </header>
      <table class="data-table inner-table">
        <thead>
          <tr>
            <th>工作台编号</th><th>指标</th><th>统计维度</th><th>时间范围</th>
            <th>异常样本数</th><th>状态</th><th>生成时间</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in workbench" :key="item.工作台编号">
            <td>{{ item.工作台编号 }}</td>
            <td>{{ item.指标编号 }} {{ item.指标名称 }}</td>
            <td><span class="dim-tag">{{ item.统计维度 }}</span></td>
            <td>{{ item.时间范围 }}</td>
            <td>{{ item.异常样本数 }}</td>
            <td :class="item.状态 === '待处理' ? 'warn-text' : 'muted-text'">{{ item.状态 }}</td>
            <td>{{ item.生成时间 }}</td>
            <td class="row-actions">
              <button class="link" type="button" @click="reopenDrilldown(item)">查看下钻</button>
              <button v-if="item.状态 === '待处理'" class="link" type="button" @click="resolveItem(item)">
                标记处理
              </button>
              <button v-else class="link" type="button" @click="reopenItem(item)">重新打开</button>
            </td>
          </tr>
          <tr v-if="!workbench.length">
            <td colspan="8" class="empty-state">暂无异常条目，从指标异常样本数发起下钻后会自动生成</td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 刷新批次履历：失败批次保留，证明中断后从下一批次继续 -->
    <section class="panel">
      <h3 class="panel-title">刷新批次履历</h3>
      <table class="data-table inner-table">
        <thead>
          <tr><th>批次号</th><th>批次时间</th><th>状态</th><th>涉及指标</th><th>结果说明</th></tr>
        </thead>
        <tbody>
          <tr v-for="batch in batches" :key="batch.批次号">
            <td>{{ batch.批次号 }}</td>
            <td>{{ batch.批次时间 }}</td>
            <td :class="batch.状态 === '失败' ? 'warn-text' : 'ok-text'">{{ batch.状态 }}</td>
            <td>{{ batch.涉及指标.join('、') }}</td>
            <td>{{ batch.结果说明 }}</td>
          </tr>
          <tr v-if="!batches.length">
            <td colspan="5" class="empty-state">暂无刷新批次</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>共 {{ rows.length }} 条采集看板记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import {
  fetchBatches,
  fetchMetrics,
  fetchSnapshots,
  fetchWorkbench,
  refreshBatch,
  runWorkbenchAction,
} from '@/api/dashboard'
import { request } from '@/api/client'
import type {
  DashboardMetric,
  RefreshBatch,
  Snapshot,
  WorkbenchItem,
} from '@/types/dashboard'

const ENDPOINT = '/api/dashboard'
const router = useRouter()

const columns = [
  '指标编号', '指标名称', '统计维度', '计算公式', '刷新频率', '展示类型', '数据源', '是否展示',
]
const actions = ['提交校验', '启用配置', '停用配置']
const filterFields = columns.slice(0, 3)

const rows = ref<DashboardMetric[]>([])
const batches = ref<RefreshBatch[]>([])
const workbench = ref<WorkbenchItem[]>([])
const snapshots = ref<Snapshot[]>([])
const snapshotMetric = ref<DashboardMetric | null>(null)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const refreshing = ref(false)
const refreshMessage = ref('')
const refreshOk = ref(true)

const refreshForm = reactive({
  batchTime: '',
  metricCodes: [] as string[],
  forceFail: false,
})

const filteredRows = computed(() => {
  const entries = Object.entries(filters.value).filter(([, value]) => value.trim())
  if (!entries.length) return rows.value
  return rows.value.filter((row) =>
    entries.every(([field, keyword]) =>
      String((row as unknown as Record<string, unknown>)[field] ?? '').includes(keyword.trim())),
  )
})

const pendingWorkbench = computed(() => workbench.value.filter((item) => item.状态 === '待处理'))
const failedBatchCount = computed(() => batches.value.filter((batch) => batch.状态 === '失败').length)

function cell(row: DashboardMetric, column: string): string {
  const value = (row as unknown as Record<string, unknown>)[column]
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

function resetFilters() {
  filters.value = {}
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '看板指标登记入口尚未接入审批流'
}

function toApiTime(value: string): string {
  // datetime-local 提交形如 2026-09-01T09:30，接口需要 “YYYY-MM-DD HH:MM:SS”。
  return value.replace('T', ' ').length === 16 ? `${value.replace('T', ' ')}:00` : value.replace('T', ' ')
}

async function submitRefresh() {
  errorMessage.value = ''
  refreshMessage.value = ''
  refreshing.value = true
  try {
    const result = await refreshBatch({
      batch_time: toApiTime(refreshForm.batchTime),
      metric_codes: refreshForm.metricCodes.length ? [...refreshForm.metricCodes] : undefined,
      force_fail: refreshForm.forceFail,
    })
    refreshOk.value = result.ok
    refreshMessage.value = result.message
    await Promise.all([reload(), loadBatches()])
  } catch (error) {
    refreshOk.value = false
    refreshMessage.value = error instanceof Error ? error.message : '刷新批次触发失败'
  } finally {
    refreshing.value = false
  }
}

async function runAction(action: string, row: DashboardMetric) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    if (!response.ok) {
      throw new Error('采集看板动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集看板操作失败'
  }
}

async function openSnapshots(metric: DashboardMetric) {
  snapshotMetric.value = metric
  snapshots.value = await fetchSnapshots(metric.指标编号)
}

function openDrilldown(metric: DashboardMetric, snapshotId?: number) {
  const query = snapshotId ? { snapshot: String(snapshotId) } : undefined
  void router.push({ name: 'dashboard-drill', params: { id: metric.id }, query })
}

async function reopenDrilldown(item: WorkbenchItem) {
  const metric = rows.value.find((row) => row.指标编号 === item.指标编号)
  if (!metric) {
    errorMessage.value = `${item.指标编号} 指标已不存在，无法打开下钻`
    return
  }
  const snap = snapshots.value.find((row) => row.快照编号 === item.快照编号)
  openDrilldown(metric, snap?.id)
}

async function resolveItem(item: WorkbenchItem) {
  const result = await runWorkbenchAction(item.id, '标记处理')
  errorMessage.value = result.ok ? '' : result.message
  await loadWorkbench()
}

async function reopenItem(item: WorkbenchItem) {
  const result = await runWorkbenchAction(item.id, '重新打开')
  errorMessage.value = result.ok ? '' : result.message
  await loadWorkbench()
}

async function reload() {
  errorMessage.value = ''
  try {
    rows.value = await fetchMetrics()
    if (snapshotMetric.value) {
      const latest = rows.value.find((row) => row.id === snapshotMetric.value?.id)
      if (latest) {
        snapshotMetric.value = latest
        snapshots.value = await fetchSnapshots(latest.指标编号)
      }
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集看板列表读取失败'
  }
}

async function loadBatches() {
  batches.value = await fetchBatches()
}

async function loadWorkbench() {
  workbench.value = await fetchWorkbench()
}

onMounted(async () => {
  await Promise.all([reload(), loadBatches(), loadWorkbench()])
})
</script>
