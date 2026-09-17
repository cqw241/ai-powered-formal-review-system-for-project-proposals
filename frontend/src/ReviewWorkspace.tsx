import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { applyHumanDecision, listReviews, listRules, startReview } from './api'
import EvidenceCompare from './EvidenceCompare'
import type {
  EvidenceBBox,
  HumanDecision,
  HumanDecisionAction,
  ReviewEvidence,
  ReviewItem,
  ReviewItemStatus,
  ReviewTask,
  ReviewTaskStatus,
  Rule,
} from './types'

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
  PASS: '通过',
  FAIL: '不通过',
  NEED_HUMAN_REVIEW: '待人工确认',
  NOT_APPLICABLE: '不适用',
  SYSTEM_ERROR: '系统错误',
}

const HUMAN_ACTION_LABEL: Record<HumanDecisionAction, string> = {
  CONFIRM: '确认问题',
  CORRECT_FIELD: '修正字段',
  MARK_NOT_APPLICABLE: '标记不适用',
}

const CORRECTABLE_FIELDS = new Set(['项目负责人', '项目名称', '申请经费', '申请总额', '申请金额'])

const BUILTIN_RULES: Array<{ code: string; name: string }> = [
  { code: 'RULE-001', name: '三类必需材料完整性' },
  { code: 'RULE-002', name: '项目名称跨文件一致' },
  { code: 'RULE-003', name: '项目负责人跨文件一致' },
  { code: 'RULE-004', name: '项目周期窗口与时长' },
  { code: 'RULE-006', name: '预算科目合计一致' },
  { code: 'RULE-007', name: '申请经费跨文件一致性' },
  { code: 'RULE-008', name: '大额设备必要性附件' },
  { code: 'RULE-009', name: '数据与伦理适用性' },
  { code: 'RULE-010', name: '承诺书签署日期' },
]

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

function correctableFields(item: ReviewItem): Array<{
  key: string
  materialId: string
  fieldName: string
  label: string
  originalValue: string
}> {
  const source = item.original_result ?? item.result
  const fields: Array<{
    key: string
    materialId: string
    fieldName: string
    label: string
    originalValue: string
  }> = []
  const seen = new Set<string>()
  for (const evidence of source?.evidence ?? []) {
    if (!evidence.material_id || !evidence.field_name || !CORRECTABLE_FIELDS.has(evidence.field_name)) {
      continue
    }
    const key = `${evidence.material_id}::${evidence.field_name}`
    if (seen.has(key)) {
      continue
    }
    seen.add(key)
    const file = evidence.original_filename ? ` · ${evidence.original_filename}` : ''
    const originalValue = evidence.raw_value || String(evidence.normalized_value ?? '')
    fields.push({
      key,
      materialId: evidence.material_id,
      fieldName: evidence.field_name,
      label: `${evidence.field_name}${file}${originalValue ? ` · 原值 ${originalValue}` : ''}`,
      originalValue,
    })
  }
  return fields
}

function evidenceLabel(evidence: ReviewEvidence): string {
  const field = evidence.field_name || '原文'
  const file = evidence.original_filename ? ` · ${evidence.original_filename}` : ''
  const page = evidence.page_number ? ` · 第 ${evidence.page_number} 页` : ''
  return `${field}${file}${page}`
}

function EvidenceRow({
  evidence,
  onOpen,
}: {
  evidence: ReviewEvidence
  onOpen?: (target: EvidenceTarget) => void
}) {
  const clickable = Boolean(evidence.material_id && evidence.page_number && onOpen)
  return (
    <button
      type="button"
      className={`evidence-row${clickable ? ' clickable' : ''}`}
      disabled={!clickable}
      onClick={() => {
        if (!evidence.material_id || !evidence.page_number || !onOpen) {
          return
        }
        onOpen({
          materialId: evidence.material_id,
          pageNumber: evidence.page_number,
          bbox: evidence.bbox,
        })
      }}
    >
      <strong>{evidenceLabel(evidence)}</strong>
      <span>{evidence.quote || evidence.raw_value || evidence.reason || '无摘录'}</span>
    </button>
  )
}

export type EvidenceTarget = {
  materialId: string
  pageNumber: number
  bbox: EvidenceBBox | null
}

type Props = {
  projectId: string
  onTaskCreated?: () => void
  onOpenEvidence?: (target: EvidenceTarget) => void
}

export default function ReviewWorkspace({ projectId, onTaskCreated, onOpenEvidence }: Props) {
  const [rules, setRules] = useState<Rule[]>([])
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [tasks, setTasks] = useState<ReviewTask[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [detailItemId, setDetailItemId] = useState<string | null>(null)
  const [operator, setOperator] = useState('')
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

  const visibleDetailId = displayTask?.items.some((item) => item.id === detailItemId)
    ? detailItemId
    : null

  function toggleRule(ruleId: string) {
    setSelectedIds((prev) =>
      prev.includes(ruleId) ? prev.filter((id) => id !== ruleId) : [...prev, ruleId],
    )
  }

  function handleTaskUpdated(task: ReviewTask) {
    setTasks((prev) => prev.map((row) => (row.id === task.id ? task : row)))
    setActiveId(task.id)
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
        一次审查内置 RULE-001/002/003/004/006/007/008/009/010。勾选已启用的 RULE-005 按项目类别用当时版本上限。开始后先落库「运行中」，刷新可续看。「失败」只表示执行出错；规则不通过是已完成；不适用单独展示。点击证据或「对照原文」可打开双文档对照：字段名、原值、单位与差异；扫描页高亮随缩放对齐。申请经费与总经费分别标明。可确认问题、修正提取字段或标记不适用；修正字段会按现有规则重算，机器原结果单独保留。
      </p>

      <div className="rule-picker" data-testid="review-rule-picker">
        {BUILTIN_RULES.map((rule) => (
          <label key={rule.code} className="rule-option locked">
            <input type="checkbox" checked disabled data-testid={`select-rule-${rule.code}`} />
            <span>
              <strong>{rule.code}</strong> {rule.name}
              <span className="muted"> · 内置，始终纳入</span>
            </span>
          </label>
        ))}
        {enabledRules.length === 0 ? (
          <p className="muted">当前没有已启用 RULE-005。开始审查仍会运行内置规则。</p>
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
        <label className="human-operator">
          操作者
          <input
            value={operator}
            onChange={(event) => setOperator(event.target.value)}
            placeholder="填写后再确认或修正"
            data-testid="human-operator"
          />
        </label>
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
                {item.original_result && item.human_decisions && item.human_decisions.length > 0 ? (
                  <p className="muted" data-testid={`original-result-${itemKey(item)}`}>
                    机器原结果：
                    <strong>
                      {CHECK_STATUS_LABEL[item.original_result.status] ?? item.original_result.status}
                    </strong>
                    {' · '}
                    {item.original_result.summary}
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
                {item.result?.evidence?.length || item.compare?.sides.length ? (
                  <div className="review-item-actions">
                    <button
                      type="button"
                      className="ghost-btn"
                      data-testid={`open-compare-${itemKey(item)}`}
                      onClick={() =>
                        setDetailItemId((prev) => (prev === item.id ? null : item.id))
                      }
                    >
                      {visibleDetailId === item.id ? '收起对照' : '对照原文'}
                    </button>
                  </div>
                ) : null}
                {item.result?.evidence?.length ? (
                  <ul className="evidence-list">
                    {item.result.evidence.map((evidence, index) => (
                      <li key={`${item.id}-ev-${index}`}>
                        <EvidenceRow evidence={evidence} onOpen={onOpenEvidence} />
                      </li>
                    ))}
                  </ul>
                ) : null}
                {visibleDetailId === item.id && item.compare ? (
                  <EvidenceCompare item={item} onOpenEvidence={onOpenEvidence} />
                ) : null}
                {displayTask.status === 'COMPLETED' ? (
                  <HumanDisposition
                    projectId={projectId}
                    taskId={displayTask.id}
                    item={item}
                    operator={operator}
                    disabled={running}
                    onUpdated={handleTaskUpdated}
                  />
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}

function HumanDisposition({
  projectId,
  taskId,
  item,
  operator,
  disabled,
  onUpdated,
}: {
  projectId: string
  taskId: string
  item: ReviewItem
  operator: string
  disabled: boolean
  onUpdated: (task: ReviewTask) => void
}) {
  const [note, setNote] = useState('')
  const [fieldKey, setFieldKey] = useState('')
  const [corrected, setCorrected] = useState('')
  const [busy, setBusy] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)
  const fields = useMemo(() => correctableFields(item), [item])
  const selected = fields.find((field) => field.key === fieldKey) ?? fields[0] ?? null
  const itemTestId = itemKey(item)
  const locked = disabled || busy || item.status === 'RUNNING' || item.status === 'NOT_EXECUTED'

  async function submit(action: HumanDecisionAction) {
    setLocalError(null)
    if (!operator.trim() || !note.trim()) {
      setLocalError('请填写操作者与说明')
      return
    }
    if (action === 'CORRECT_FIELD') {
      if (!selected) {
        setLocalError('当前条目没有可修正的提取字段')
        return
      }
      if (!corrected.trim()) {
        setLocalError('请填写修正后的值')
        return
      }
    }
    setBusy(true)
    try {
      const task = await applyHumanDecision(projectId, taskId, item.id, {
        action,
        operator: operator.trim(),
        note: note.trim(),
        field_name: action === 'CORRECT_FIELD' ? selected?.fieldName : null,
        material_id: action === 'CORRECT_FIELD' ? selected?.materialId : null,
        corrected_value: action === 'CORRECT_FIELD' ? corrected.trim() : null,
      })
      onUpdated(task)
      if (action === 'CORRECT_FIELD') {
        setCorrected('')
      }
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : '人工处置失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="human-disposition" data-testid={`human-disposition-${itemTestId}`}>
      <strong>人工确认与修正</strong>
      <div className="candidate-edit-grid">
        <label className="full-width">
          说明
          <textarea
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="记录确认、修正或不适用的原因"
            data-testid={`human-note-${itemTestId}`}
          />
        </label>
      </div>
      <div className="review-item-actions">
        <button
          type="button"
          className="ghost-btn"
          disabled={locked}
          data-testid={`confirm-item-${itemTestId}`}
          onClick={() => void submit('CONFIRM')}
        >
          确认问题
        </button>
        <button
          type="button"
          className="ghost-btn"
          disabled={locked}
          data-testid={`na-item-${itemTestId}`}
          onClick={() => void submit('MARK_NOT_APPLICABLE')}
        >
          标记不适用
        </button>
      </div>
      {fields.length > 0 ? (
        <div className="candidate-edit-grid">
          <label>
            修正字段
            <select
              value={selected?.key ?? ''}
              onChange={(event) => {
                setFieldKey(event.target.value)
                setCorrected('')
              }}
              data-testid={`correct-field-${itemTestId}`}
            >
              {fields.map((field) => (
                <option key={field.key} value={field.key}>
                  {field.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            修改后值
            <input
              value={corrected}
              onChange={(event) => setCorrected(event.target.value)}
              placeholder={selected?.originalValue ? `原值 ${selected.originalValue}` : '填写修正值'}
              data-testid={`correct-value-${itemTestId}`}
            />
          </label>
          <div className="review-item-actions">
            <button
              type="button"
              disabled={locked}
              data-testid={`correct-submit-${itemTestId}`}
              onClick={() => void submit('CORRECT_FIELD')}
            >
              {busy ? '重算中…' : '修正并重算'}
            </button>
          </div>
        </div>
      ) : (
        <p className="muted">此条没有可修正的姓名或金额字段；仍可确认问题或标记不适用。</p>
      )}
      {localError ? (
        <p className="msg error" role="alert">
          {localError}
        </p>
      ) : null}
      {(item.human_decisions ?? []).length > 0 ? (
        <ol className="human-history" data-testid={`human-history-${itemTestId}`}>
          {(item.human_decisions ?? []).map((decision) => (
            <li key={decision.id}>
              <HistoryEntry decision={decision} />
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  )
}

function HistoryEntry({ decision }: { decision: HumanDecision }) {
  return (
    <>
      <span className="status-chip">{HUMAN_ACTION_LABEL[decision.action]}</span>
      <span>
        {decision.operator} · {formatDateTime(decision.created_at)}
      </span>
      {decision.field_name ? (
        <span>
          {decision.field_name}：{decision.original_value || '（空）'} → {decision.corrected_value}
        </span>
      ) : null}
      <span className="muted">{decision.note}</span>
    </>
  )
}
