/** 采集看板下钻链路的接口类型，与后端 dashboard 路由返回结构保持一致。 */

export interface DashboardMetric {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  指标编号: string
  指标名称: string
  统计维度: string
  计算公式: string
  刷新频率: string
  展示类型: string
  数据源: string
  是否展示: string
  最近成功更新时间: string | null
  当前快照编号: string | null
  异常样本数: number
}

export interface SummaryItem {
  维度值: string
  异常数: number
}

export interface Snapshot {
  id: number
  快照编号: string
  批次号: string
  指标编号: string
  批次时间: string
  统计起始: string
  统计截止: string
  统计维度: string
  异常样本数: number
  异常日志编号: string[]
  汇总值: SummaryItem[]
  状态: string
  is_current: boolean
}

export interface RefreshBatch {
  id: number
  批次号: string
  批次时间: string
  状态: '成功' | '失败' | '进行中'
  涉及指标: string[]
  结果说明: string
}

export interface AbnormalSample {
  id: number
  日志编号: string
  关联任务: string
  来源标识?: string
  开始时间: string
  结束时间: string
  采集行数: string
  成功条数: string
  失败条数: string
  错误信息: string
  下钻结论?: string
  status: string
}

export interface DrilldownRecord {
  id: number
  下钻编号: string
  指标编号: string
  快照编号: string
  批次号: string
  统计维度: string
  统计起始: string
  统计截止: string
  异常样本数: number
  异常日志编号: string[]
  下钻时间: string
  下钻结论: string
}

export interface DrilldownView {
  下钻记录: DrilldownRecord
  指标信息: Pick<
    DashboardMetric,
    '指标编号' | '指标名称' | '统计维度' | '计算公式' | '刷新频率' | '展示类型' | '数据源'
  >
  快照信息: {
    快照编号: string
    批次号: string
    批次时间: string
    统计起始: string
    统计截止: string
    统计维度: string
    异常样本数: number
    汇总值: SummaryItem[]
  }
  异常样本: AbnormalSample[]
  工作台条目: WorkbenchItem
  message?: string
}

export interface WorkbenchItem {
  id: number
  工作台编号: string
  指标编号: string
  指标名称: string
  快照编号: string
  下钻编号: string
  统计维度: string
  时间范围: string
  异常样本数: number
  异常日志编号: string[]
  状态: '待处理' | '已处理'
  生成时间: string
}

export interface ListEnvelope<T> {
  module: string
  total: number
  items: T[]
}
