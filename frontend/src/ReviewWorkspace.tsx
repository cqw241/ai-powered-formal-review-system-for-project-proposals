import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { listReviews, listRules, startReview } from './api'
import type { ReviewItem, ReviewItemStatus, ReviewTask, ReviewTaskStatus, Rule } from './types'

const TASK_STATUS_LABEL: Record<ReviewTaskStatus, string> = {
  RUNNING: '运行中',
  COMPLETED: '已完成',
}

const ITEM_STATUS_LABEL: Record<ReviewItemStatus, string> = {
  RUNNING: '执行中',
  COMPLETED: '已完成',
  PENDING_CONFIRMATION: '待确认',
  FAILED: '失败',
  NOT_EXECUTED: '未执行',
}

const CHECK_STATUS_LABEL: Record<string, string> = {
  PASS: '一致',
  FAIL: '不一致',
  NEED_HUMAN_REVIEW: '待人工确认',
  SYSTEM_ERROR: '系统错误',
}

function formatDateTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  try {
    return new Intl.DateTimeFormat('zh-CN', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      timeZone: 'UTC',
      timeZoneName: 'short',
    }).format(date)
  } catch {
    return date.toISOString().replace('T', ' ').replace(/\.\d+Z$/, ' UTC')
  }
}

function formatYuan(value: number | null | undefined): string {
  if (value == null) {
    return '未知（非 0）'
  }
  return `${value.toLocaleString('zh-CN')} 元`
}

function comparatorLabel(value: string | null | undefined): string {
  if (value === 'LE') {
    return '不超过'
  }
  if (value === 'LT') {
    return '小于'
  }
  if (value === 'EQ') {
    return '等于'
  }
  if (value === 'GE') {
    return '不少于'
  }
  return value || '未设'
}

function itemKey(item: ReviewItem): string {
  return item.source_rule_id ?? item.rule_code
}

type Props = {
  projectId: string
  onTaskCreated?: () => void
}

export default function ReviewWorkspace({ projectId, onTaskCreated }: Props) {
  const [rules, setRules] = useState<Rule[]>([])
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [tasks, setTasks] = useState<ReviewTask[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const selectionReady = useRef(false)

  const enabledRules = useMemo(() => rules.filter((item) => item.enabled), [rules])
  const inFlightTask = useMemo(
    () => tasks.find((item) => item.status === 'RUNNING') ?? null,
    [tasks],
  )
  const activeTask = useMemo(
    () => tasks.find((item) => item.id === activeId) ?? tasks[0] ?? null,
    [tasks, activeId],
  )
  const displayTask = running ? inFlightTask : activeTask
  const taskStatusLabel = running || displayTask?.status === 'RUNNING'
    ? TASK_STATUS_LABEL.RUNNING
    : displayTask
      ? TASK_STATUS_LABEL[displayTask.status]
      : '尚未审查'
  const taskStatusClass =
    running || displayTask?.status === 'RUNNING'
      ? 'running'
      : displayTask
        ? displayTask.status.toLowerCase()
        : 'idle'

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [ruleList, reviewList] = await Promise.all([listRules(), listReviews(projectId)])
      setRules(ruleList)
      setTasks(reviewList)
      const enabledIds = ruleList.filter((item) => item.enabled).map((item) => item.id)
      if (!selectionReady.current) {
        selectionReady.current = true
        setSelectedIds(enabledIds)
      }
      setActiveId((prev) => {
        if (prev && reviewList.some((item) => item.id === prev)) {
          return prev
        }
        return reviewList[0]?.id ?? null
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载审查工作台失败')
      setRules([])
      setTasks([])
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    void load()
  }, [load])

  function toggleRule(ruleId: string) {
    setSelectedIds((prev) =>
      prev.includes(ruleId) ? prev.filter((id) => id !== ruleId) : [...prev, ruleId],
    )
  }

  async function handleStart() {
    setRunning(true)
    setError(null)
    try {
      const task = await startReview(projectId, selectedIds)
      setTasks((prev) => [task, ...prev.filter((item) => item.id !== task.id)])
      setActiveId(task.id)
      onTaskCreated?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : '发起审查失败')
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="review-workspace" data-testid="review-workspace">
      <div className="panel-head subhead">
        <h3>审查工作台</h3>
        <button type="button" className="ghost-btn" onClick={() => void load()} disabled={loading}>
          {loading ? '刷新中…' : '刷新'}
        </button>
      </div>
      <p className="muted funding-hint">
        内置 RULE-007 始终执行。开始后先落库「运行中」与逐项占位，刷新可续看进度；完成后再写终态。「失败」只表示执行出错；金额不一致是已完成，核对结论为「不一致」。勾选但无执行器的规则为未执行（绑定当时版本，不按上限裁决）。无已启用规则时仍可只跑
        RULE-007。
      </p>

      <div className="rule-picker" data-testid="review-rule-picker">
        <label className="rule-option locked">
          <input type="checkbox" checked disabled data-testid="select-rule-RULE-007" />
          <span>
            <strong>RULE-007</strong> 申请经费跨文件一致性
            <span className="muted"> · 内置，始终纳入</span>
          </span>
        </label>
        {enabledRules.length === 0 ? (
          <p className="muted">当前没有已启用规则，开始审查将只运行 RULE-007。</p>
        ) : (
          enabledRules.map((rule) => {
            const version = rule.current_version
            const checked = selectedIds.includes(rule.id)
            return (
              <label key={rule.id} className="rule-option">
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggleRule(rule.id)}
                  disabled={running}
                  data-testid={`select-rule-${rule.id}`}
                />
                <span>
                  <strong>{rule.rule_code}</strong> {rule.name}
                  {version ? (
                    <span className="muted">
                      {' '}
                      · v{version.version_number}
                      {version.category ? ` · ${version.category}` : ''}
                      {version.amount_yuan != null ? ` · ${formatYuan(version.amount_yuan)}` : ''}
                    </span>
                  ) : null}
                </span>
              </label>
            )
          })
        )}
      </div>

      <div className="review-actions">
        <button type="button" onClick={() => void handleStart()} disabled={running} data-testid="start-review">
          {running ? '审查中…' : '开始审查'}
        </button>
        <span className={`status-chip status-task-${taskStatusClass}`} data-testid="review-task-status">
          {taskStatusLabel}
        </span>
      </div>

      {error ? (
        <p className="msg error" role="alert">
          {error}
        </p>
      ) : null}

      {tasks.length > 1 ? (
        <div className="review-history" data-testid="review-task-history">
          <span className="muted">历史任务</span>
          {tasks.map((task, index) => {
            const active = task.id === displayTask?.id
            return (
              <button
                key={task.id}
                type="button"
                className={`ghost-btn${active ? ' active' : ''}`}
                onClick={() => setActiveId(task.id)}
              >
                {index === 0 ? '最新' : `任务 ${tasks.length - index}`} · {TASK_STATUS_LABEL[task.status]} ·{' '}
                {task.items.length} 项
              </button>
            )
          })}
        </div>
      ) : null}

      {loading && !displayTask ? <p className="muted">正在加载审查任务…</p> : null}

      {running && !displayTask ? (
        <div className="empty-state compact" data-testid="review-running-placeholder">
          <strong>审查进行中</strong>
          <p>任务已开始。点「刷新」可查看已落库的运行中条目，不会继续显示上一次结果。</p>
        </div>
      ) : null}

      {!loading && !displayTask && !running ? (
        <div className="empty-state compact">
          <strong>尚未发起审查</strong>
          <p>选择启用规则后点击「开始审查」。刷新页面后仍可查看已完成的任务。</p>
        </div>
      ) : null}

      {displayTask ? (
        <div className="review-task" data-testid={`review-task-${displayTask.id}`}>
          <p className="muted">
            任务 {formatDateTime(displayTask.created_at)} · {TASK_STATUS_LABEL[displayTask.status]} ·{' '}
            {displayTask.items.length} 项
          </p>
          <ul className="review-item-list">
            {displayTask.items.map((item) => (
              <li
                key={item.id}
                className={`review-item status-item-${item.status.toLowerCase()}`}
                data-testid={`review-item-${itemKey(item)}`}
              >
                <header className="candidate-head">
                  <span className="status-chip">{item.rule_code}</span>
                  {item.version_number != null ? (
                    <span className="status-chip">v{item.version_number}</span>
                  ) : null}
                  <strong>{item.name}</strong>
                  <span
                    className={`status-chip status-item-${item.status.toLowerCase()}`}
                    data-testid={`review-item-status-${itemKey(item)}`}
                  >
                    {ITEM_STATUS_LABEL[item.status]}
                  </span>
                </header>
                {item.check_status ? (
                  <p>
                    核对结论：
                    <strong>{CHECK_STATUS_LABEL[item.check_status] ?? item.check_status}</strong>
                  </p>
                ) : null}
                <p className="candidate-quote">{item.summary}</p>
                {item.snapshot ? (
                  <dl className="issue-grid">
                    <div>
                      <dt>类别</dt>
                      <dd>{item.snapshot.category || '未标注（不因此判 FAIL）'}</dd>
                    </div>
                    <div>
                      <dt>比较对象</dt>
                      <dd>{item.snapshot.compare_field}</dd>
                    </div>
                    <div>
                      <dt>要求</dt>
                      <dd>
                        {comparatorLabel(item.snapshot.comparator)} {formatYuan(item.snapshot.amount_yuan)}
                        {item.snapshot.amount_raw ? `（原文 ${item.snapshot.amount_raw}）` : ''}
                      </dd>
                    </div>
                  </dl>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}
