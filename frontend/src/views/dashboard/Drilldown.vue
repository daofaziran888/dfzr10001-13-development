<template>
  <section class="page drill-page" data-module="dashboard-drilldown">
    <header class="page-head">
      <div>
        <h2>指标下钻独立视图</h2>
        <p class="page-desc">
          下钻口径固定在所选快照：时间范围与统计维度随快照携带，切换指标后旧样本不会残留或复用。
        </p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn" :to="{ name: 'dashboard' }">返回采集看板</RouterLink>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <template v-else-if="view">
      <!-- 首屏：统计维度与时间范围最醒目 -->
      <section class="drill-hero panel">
        <div class="hero-main">
          <span class="hero-label">统计维度</span>
          <strong class="hero-dimension">{{ view.快照信息.统计维度 }}</strong>
          <span class="hero-window">
            时间范围：{{ view.快照信息.统计起始 }} ~ {{ view.快照信息.统计截止 }}
          </span>
        </div>
        <div class="hero-meta">
          <div><span class="stat-label">异常样本数</span><strong>{{ view.异常样本.length }}</strong></div>
          <div><span class="stat-label">快照编号</span><strong class="mono">{{ view.快照信息.快照编号 }}</strong></div>
          <div><span class="stat-label">批次时间</span><strong>{{ view.快照信息.批次时间 }}</strong></div>
        </div>
      </section>

      <section class="panel">
        <h3 class="panel-title">指标信息（下钻全程保留）</h3>
        <dl class="meta-grid">
          <div v-for="field in metadataFields" :key="field" class="meta-cell">
            <dt>{{ field }}</dt>
            <dd>{{ view.指标信息[field] ?? '—' }}</dd>
          </div>
        </dl>
      </section>

      <section class="panel">
        <h3 class="panel-title">按「{{ view.快照信息.统计维度 }}」维度的汇总值</h3>
        <table class="data-table inner-table">
          <thead>
            <tr><th>维度值</th><th>异常数</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in view.快照信息.汇总值" :key="item.维度值">
              <td>{{ item.维度值 }}</td>
              <td>{{ item.异常数 }}</td>
            </tr>
            <tr v-if="!view.快照信息.汇总值.length">
              <td colspan="2" class="empty-state">该窗口内无异常样本</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="panel">
        <header class="panel-head">
          <h3 class="panel-title">异常执行日志样本（按开始时间排列）</h3>
          <span class="muted-text">共 {{ view.异常样本.length }} 条，仅包含快照时间范围内的异常日志</span>
        </header>
        <table class="data-table inner-table">
          <thead>
            <tr>
              <th>日志编号</th><th>采集任务</th><th>来源标识</th><th>开始时间</th><th>结束时间</th>
              <th>失败条数</th><th>错误信息</th><th>下钻结论回写</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="sample in view.异常样本" :key="sample.日志编号">
              <td class="mono">{{ sample.日志编号 }}</td>
              <td>{{ sample.关联任务 }}</td>
              <td>{{ sample.来源标识 ?? '—' }}</td>
              <td>{{ sample.开始时间 }}</td>
              <td>{{ sample.结束时间 }}</td>
              <td>{{ sample.失败条数 }}</td>
              <td>{{ sample.错误信息 }}</td>
              <td class="conclusion-cell">{{ sample.下钻结论 || '已回写' }}</td>
            </tr>
            <tr v-if="!view.异常样本.length">
              <td colspan="8" class="empty-state">该快照窗口内没有异常样本</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="panel">
        <h3 class="panel-title">下钻执行记录</h3>
        <dl class="meta-grid">
          <div class="meta-cell"><dt>下钻编号</dt><dd class="mono">{{ view.下钻记录.下钻编号 }}</dd></div>
          <div class="meta-cell"><dt>下钻时间</dt><dd>{{ view.下钻记录.下钻时间 }}</dd></div>
          <div class="meta-cell meta-wide"><dt>下钻结论</dt><dd>{{ view.下钻记录.下钻结论 }}</dd></div>
          <div class="meta-cell"><dt>工作台条目</dt><dd>{{ view.工作台条目.工作台编号 }}（{{ view.工作台条目.状态 }}）</dd></div>
        </dl>
      </section>
    </template>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { fetchDrilldown } from '@/api/dashboard'
import type { DrilldownView } from '@/types/dashboard'

type MetadataField =
  | '指标编号'
  | '指标名称'
  | '统计维度'
  | '计算公式'
  | '刷新频率'
  | '展示类型'
  | '数据源'

const metadataFields: MetadataField[] = [
  '指标编号', '指标名称', '统计维度', '计算公式', '刷新频率', '展示类型', '数据源',
]

const route = useRoute()
const view = ref<DrilldownView | null>(null)
const errorMessage = ref('')

async function load() {
  view.value = null
  errorMessage.value = ''
  const entryId = Number(route.params.id)
  if (!Number.isInteger(entryId)) {
    errorMessage.value = '指标编号缺失，无法进入下钻视图'
    return
  }
  const snapshotRaw = route.query.snapshot
  const snapshotId = snapshotRaw ? Number(snapshotRaw) : undefined
  try {
    view.value = await fetchDrilldown(entryId, snapshotId)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '下钻数据读取失败'
  }
}

// 路由参数变化（例如从工作台打开另一份快照）时整体重载，保证旧样本不残留。
watch(() => route.fullPath, load)
onMounted(load)
</script>
