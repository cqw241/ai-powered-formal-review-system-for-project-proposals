import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  createProject,
  getFundingReview,
  getPageText,
  getProject,
  listFundingReviews,
  listMaterials,
  listProjects,
  pageImageUrl,
  runFundingReview,
  uploadMaterial,
} from './api'
import PolicyWorkspace from './PolicyWorkspace'
import ReviewWorkspace from './ReviewWorkspace'
import type {
  BoundRule,
  EvidenceBBox,
  FundingReview,
  FundingSide,
  Material,
  MaterialCategory,
  Project,
} from './types'
import './App.css'

type View =
  | { kind: 'list' }
  | { kind: 'detail'; projectId: string }
  | { kind: 'policies' }

const CATEGORY_OPTIONS: Array<{ value: MaterialCategory; label: string }> = [
  { value: 'APPLICATION', label: '申报书 (APPLICATION)' },
  { value: 'BUDGET', label: '预算表 (BUDGET)' },
  { value: 'COMMITMENT', label: '承诺书 (COMMITMENT)' },
  { value: 'OTHER', label: '其他 (OTHER)' },
]

const CATEGORY_LABEL: Record<MaterialCategory, string> = {
  APPLICATION: '申报书',
  BUDGET: '预算表',
  COMMITMENT: '承诺书',
  OTHER: '其他',
}

const STATUS_LABEL: Record<Material['status'], string> = {
  PROCESSING: '处理中',
  READY: '就绪',
  FAILED: '失败',
}

const REVIEW_STATUS_LABEL: Record<string, string> = {
  PASS: '一致',
  FAIL: '不一致',
  NEED_HUMAN_REVIEW: '待人工确认',
  SYSTEM_ERROR: '系统错误',
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

function sideLabel(side: FundingSide | null, fallback: string): string {
  if (!side) {
    return fallback
  }
  return side.field_label || side.original_filename || fallback
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

export default function App() {
  const [view, setView] = useState<View>({ kind: 'list' })
  const viewRef = useRef(view)
  viewRef.current = view

  const [projects, setProjects] = useState<Project[]>([])
  const [listLoading, setListLoading] = useState(true)
  const [listError, setListError] = useState<string | null>(null)

  const [name, setName] = useState('')
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [createNotice, setCreateNotice] = useState<string | null>(null)

  const [detail, setDetail] = useState<Project | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)

  const [materials, setMaterials] = useState<Material[]>([])
  const [materialsLoading, setMaterialsLoading] = useState(false)
  const [materialsError, setMaterialsError] = useState<string | null>(null)

  const [uploadCategory, setUploadCategory] = useState<MaterialCategory>('APPLICATION')
  const [uploadFiles, setUploadFiles] = useState<FileList | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [uploadNotice, setUploadNotice] = useState<string | null>(null)

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [pageNumber, setPageNumber] = useState(1)
  const [pageText, setPageText] = useState<string | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [highlight, setHighlight] = useState<{
    materialId: string
    pageNumber: number
    bbox: EvidenceBBox
  } | null>(null)

  const [fundingReview, setFundingReview] = useState<FundingReview | null>(null)
  const [fundingHistory, setFundingHistory] = useState<FundingReview[]>([])
  const [fundingLoading, setFundingLoading] = useState(false)
  const [fundingError, setFundingError] = useState<string | null>(null)
  const [fundingRunning, setFundingRunning] = useState(false)

  const isActiveDetail = useCallback((projectId: string) => {
    const current = viewRef.current
    return current.kind === 'detail' && current.projectId === projectId
  }, [])

  const loadProjects = useCallback(async () => {
    setListLoading(true)
    setListError(null)
    try {
      const data = await listProjects()
      setProjects(data)
    } catch (error) {
      setListError(error instanceof Error ? error.message : '加载项目列表失败')
    } finally {
      setListLoading(false)
    }
  }, [])

  const loadMaterials = useCallback(
    async (projectId: string) => {
      if (isActiveDetail(projectId)) {
        setMaterialsLoading(true)
        setMaterialsError(null)
      }
      try {
        const data = await listMaterials(projectId)
        if (isActiveDetail(projectId)) {
          setMaterials(data)
          setMaterialsError(null)
        }
        return data
      } catch (error) {
        if (isActiveDetail(projectId)) {
          setMaterialsError(error instanceof Error ? error.message : '加载材料列表失败')
          setMaterials([])
        }
        return [] as Material[]
      } finally {
        if (isActiveDetail(projectId)) {
          setMaterialsLoading(false)
        }
      }
    },
    [isActiveDetail],
  )

  const loadFundingReview = useCallback(
    async (projectId: string) => {
      if (isActiveDetail(projectId)) {
        setFundingLoading(true)
        setFundingError(null)
      }
      try {
        const [data, history] = await Promise.all([
          getFundingReview(projectId),
          listFundingReviews(projectId),
        ])
        if (isActiveDetail(projectId)) {
          setFundingReview(data)
          setFundingHistory(history)
          setFundingError(null)
        }
        return data
      } catch (error) {
        if (isActiveDetail(projectId)) {
          setFundingError(error instanceof Error ? error.message : '加载经费核对结果失败')
          setFundingReview(null)
          setFundingHistory([])
        }
        return null
      } finally {
        if (isActiveDetail(projectId)) {
          setFundingLoading(false)
        }
      }
    },
    [isActiveDetail],
  )

  useEffect(() => {
    void loadProjects()
  }, [loadProjects])

  useEffect(() => {
    if (view.kind !== 'detail') {
      setMaterialsLoading(false)
      setUploading(false)
      return
    }
    const projectId = view.projectId
    let cancelled = false
    setDetailLoading(true)
    setDetailError(null)
    setDetail(null)
    setMaterials([])
    setMaterialsError(null)
    setSelectedId(null)
    setPageNumber(1)
    setPageText(null)
    setPreviewError(null)
    setUploadError(null)
    setUploadNotice(null)
    setUploadFiles(null)
    setUploading(false)
    setHighlight(null)
    setFundingReview(null)
    setFundingHistory([])
    setFundingError(null)
    setFundingLoading(false)
    setFundingRunning(false)

    void Promise.all([getProject(projectId), loadMaterials(projectId), loadFundingReview(projectId)])
      .then(([project]) => {
        if (!cancelled && isActiveDetail(projectId)) {
          setDetail(project)
        }
      })
      .catch((error: unknown) => {
        if (!cancelled && isActiveDetail(projectId)) {
          setDetailError(error instanceof Error ? error.message : '加载项目失败')
        }
      })
      .finally(() => {
        if (!cancelled && isActiveDetail(projectId)) {
          setDetailLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [view, loadMaterials, loadFundingReview, isActiveDetail])

  const selectedMaterial = useMemo(
    () => materials.find((item) => item.id === selectedId) ?? null,
    [materials, selectedId],
  )

  // Only READY materials may trigger page text / image requests.
  const previewMaterial = useMemo(
    () => (selectedMaterial?.status === 'READY' ? selectedMaterial : null),
    [selectedMaterial],
  )

  useEffect(() => {
    if (!selectedMaterial) {
      setPageText(null)
      setPreviewError(null)
      setPreviewLoading(false)
      return
    }

    if (!previewMaterial) {
      setPageText(null)
      setPreviewError(
        selectedMaterial.status === 'FAILED'
          ? selectedMaterial.error_summary || '材料解析失败'
          : '材料仍在处理中，请稍后刷新。',
      )
      setPreviewLoading(false)
      return
    }

    const total = previewMaterial.page_count ?? 0
    if (total < 1) {
      setPageText(null)
      setPreviewError('材料没有可预览的页面')
      setPreviewLoading(false)
      return
    }

    const safePage = Math.min(Math.max(pageNumber, 1), total)
    if (safePage !== pageNumber) {
      setPageNumber(safePage)
      return
    }

    let cancelled = false
    setPreviewLoading(true)
    setPreviewError(null)
    void getPageText(previewMaterial.id, safePage)
      .then((result) => {
        if (!cancelled) {
          setPageText(result.text)
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setPageText(null)
          setPreviewError(error instanceof Error ? error.message : '读取页面文本失败')
        }
      })
      .finally(() => {
        if (!cancelled) {
          setPreviewLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [selectedMaterial, previewMaterial, pageNumber])

  const canSubmit = useMemo(() => name.trim().length > 0 && !creating, [name, creating])
  const canUpload = useMemo(
    () => Boolean(uploadFiles && uploadFiles.length > 0 && !uploading && detail),
    [uploadFiles, uploading, detail],
  )

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) {
      setCreateError('请填写项目名称')
      return
    }
    setCreating(true)
    setCreateError(null)
    setCreateNotice(null)
    try {
      const project = await createProject(trimmed)
      setName('')
      setCreateNotice(`已创建项目「${project.name}」`)
      setProjects((prev) => [project, ...prev.filter((item) => item.id !== project.id)])
      setView({ kind: 'detail', projectId: project.id })
    } catch (error) {
      setCreateError(error instanceof Error ? error.message : '创建项目失败')
    } finally {
      setCreating(false)
    }
  }

  async function handleUpload(event: React.FormEvent) {
    event.preventDefault()
    if (!detail || !uploadFiles || uploadFiles.length === 0) {
      setUploadError('请选择至少一个 PDF 文件')
      return
    }

    const projectId = detail.id
    setUploading(true)
    setUploadError(null)
    setUploadNotice(null)

    const files = Array.from(uploadFiles)
    const successes: string[] = []
    const failures: string[] = []

    for (const file of files) {
      try {
        const material = await uploadMaterial(projectId, file, uploadCategory)
        if (material.status === 'FAILED') {
          failures.push(
            `${material.original_filename}：${material.error_summary || '解析失败'}`,
          )
        } else {
          successes.push(`${material.original_filename}（${STATUS_LABEL[material.status]}）`)
        }
      } catch (error) {
        failures.push(`${file.name}：${error instanceof Error ? error.message : '上传失败'}`)
      }
    }

    const refreshed = await loadMaterials(projectId)
    // Ignore late results after the user navigated to another project.
    if (!isActiveDetail(projectId)) {
      return
    }

    if (successes.length > 0) {
      setUploadNotice(`已上传 ${successes.length} 个文件：${successes.join('；')}`)
      const ready = refreshed.find((item) => item.status === 'READY')
      if (ready && (!selectedId || !refreshed.some((item) => item.id === selectedId && item.status === 'READY'))) {
        setSelectedId(ready.id)
        setPageNumber(1)
      }
    }
    if (failures.length > 0) {
      setUploadError(failures.join('；'))
    }
    setUploadFiles(null)
    setUploading(false)
  }

  function openMaterial(material: Material) {
    setSelectedId(material.id)
    setPageNumber(1)
    setHighlight(null)
    // Non-READY materials only show status/error; preview effect will not fetch pages.
  }

  function openEvidence(side: {
    material_id?: string | null
    page_number?: number | null
    bbox?: EvidenceBBox | null
  } | null) {
    if (!side?.material_id || !side.page_number) {
      return
    }
    setSelectedId(side.material_id)
    setPageNumber(side.page_number)
    setHighlight(
      side.bbox
        ? {
            materialId: side.material_id,
            pageNumber: side.page_number,
            bbox: side.bbox,
          }
        : null,
    )
  }

  async function handleFundingReview() {
    if (!detail) {
      return
    }
    const projectId = detail.id
    setFundingRunning(true)
    setFundingError(null)
    try {
      const result = await runFundingReview(projectId)
      if (!isActiveDetail(projectId)) {
        return
      }
      setFundingReview(result)
      setFundingHistory((prev) => [result, ...prev.filter((item) => item.id !== result.id)])
    } catch (error) {
      if (isActiveDetail(projectId)) {
        setFundingError(error instanceof Error ? error.message : '申请经费核对失败')
      }
    } finally {
      if (isActiveDetail(projectId)) {
        setFundingRunning(false)
      }
    }
  }

  const totalPages = previewMaterial?.page_count ?? 0
  const finding = fundingReview?.finding ?? null
  const reviewStatus = fundingReview?.status ?? null
  const boundRules = fundingReview?.bound_rules ?? []
  const canRunFunding = Boolean(detail && !fundingRunning)

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">高校项目申报 · 形式审查辅助</p>
          <h1>规证AI</h1>
        </div>
        <p className="header-note">
          本阶段支持项目材料上传预览、申请经费核对、规则启用，以及在项目中选择规则发起审查任务。
        </p>
        <nav className="top-nav" aria-label="主导航">
          <button
            type="button"
            className={`nav-btn${view.kind === 'list' || view.kind === 'detail' ? ' active' : ''}`}
            onClick={() => setView({ kind: 'list' })}
          >
            项目
          </button>
          <button
            type="button"
            className={`nav-btn${view.kind === 'policies' ? ' active' : ''}`}
            onClick={() => setView({ kind: 'policies' })}
            data-testid="nav-policies"
          >
            政策与候选要求
          </button>
        </nav>
      </header>

      <main className="app-main">
        {view.kind === 'policies' ? (
          <PolicyWorkspace onBack={() => setView({ kind: 'list' })} />
        ) : null}

        {view.kind === 'list' ? (
          <section className="panel">
            <div className="panel-head">
              <h2>项目列表</h2>
              <button type="button" className="ghost-btn" onClick={() => void loadProjects()} disabled={listLoading}>
                {listLoading ? '刷新中…' : '刷新'}
              </button>
            </div>

            <form className="create-form" onSubmit={handleCreate}>
              <label htmlFor="project-name">新建项目</label>
              <div className="create-row">
                <input
                  id="project-name"
                  name="name"
                  value={name}
                  onChange={(event) => {
                    setName(event.target.value)
                    setCreateError(null)
                    setCreateNotice(null)
                  }}
                  placeholder="例如：青年科研创新培育计划-样例"
                  maxLength={200}
                  disabled={creating}
                  autoComplete="off"
                />
                <button type="submit" disabled={!canSubmit}>
                  {creating ? '创建中…' : '创建项目'}
                </button>
              </div>
              {createError ? <p className="msg error" role="alert">{createError}</p> : null}
              {createNotice ? <p className="msg ok">{createNotice}</p> : null}
            </form>

            {listError ? <p className="msg error" role="alert">{listError}</p> : null}

            {listLoading && projects.length === 0 ? (
              <p className="muted">正在加载项目…</p>
            ) : null}

            {!listLoading && !listError && projects.length === 0 ? (
              <div className="empty-state">
                <strong>还没有项目</strong>
                <p>创建第一个项目后，刷新页面或重启后端仍可继续查看。</p>
              </div>
            ) : null}

            {projects.length > 0 ? (
              <ul className="project-list">
                {projects.map((project) => (
                  <li key={project.id}>
                    <button
                      type="button"
                      className="project-card"
                      onClick={() => setView({ kind: 'detail', projectId: project.id })}
                    >
                      <span className="project-name">{project.name}</span>
                      <span className="project-meta">
                        <span className="status-chip">尚未审查</span>
                        <time dateTime={project.created_at}>{formatDateTime(project.created_at)}</time>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
          </section>
        ) : null}

        {view.kind === 'detail' ? (
          <section className="panel">
            <div className="panel-head">
              <h2>项目详情</h2>
              <button type="button" className="ghost-btn" onClick={() => setView({ kind: 'list' })}>
                返回列表
              </button>
            </div>

            {detailLoading ? <p className="muted">正在加载项目…</p> : null}
            {detailError ? <p className="msg error" role="alert">{detailError}</p> : null}

            {detail ? (
              <>
                <dl className="detail-grid">
                  <div>
                    <dt>项目名称</dt>
                    <dd>{detail.name}</dd>
                  </div>
                  <div>
                    <dt>项目 ID</dt>
                    <dd className="mono">{detail.id}</dd>
                  </div>
                  <div>
                    <dt>创建时间</dt>
                    <dd>
                      <time dateTime={detail.created_at}>{formatDateTime(detail.created_at)}</time>
                    </dd>
                  </div>
                  <div>
                    <dt>审查状态</dt>
                    <dd>
                      {reviewStatus ? (
                        <span className={`status-chip status-review-${String(reviewStatus).toLowerCase()}`}>
                          {REVIEW_STATUS_LABEL[reviewStatus] ?? reviewStatus}
                        </span>
                      ) : (
                        <span className="status-chip">尚未审查</span>
                      )}
                      <span className="muted inline-note">
                        工作台一次审查名称、负责人、周期、经费、预算合计与签署日期。下方「开始核对」仍可单独跑
                        RULE-007。
                      </span>
                    </dd>
                  </div>
                </dl>

                <ReviewWorkspace
                  key={detail.id}
                  projectId={detail.id}
                  onTaskCreated={() => void loadFundingReview(detail.id)}
                  onOpenEvidence={(target) =>
                    openEvidence({
                      material_id: target.materialId,
                      page_number: target.pageNumber,
                      bbox: target.bbox,
                    })
                  }
                />

                <div className="funding-section">
                  <div className="panel-head subhead">
                    <h3>申请经费核对</h3>
                    <button
                      type="button"
                      onClick={() => void handleFundingReview()}
                      disabled={!canRunFunding}
                    >
                      {fundingRunning ? '核对中…' : fundingReview ? '重新核对' : '开始核对'}
                    </button>
                  </div>
                  <p className="muted funding-hint">
                    提取申报书「申请经费」与预算表「申请总额」，统一换算为元后比较。分类经费上限在上方审查工作台的 RULE-005 中判定；此处仍是 RULE-007。停用后新审查不再绑定该规则，旧核对仍显示当时版本。
                  </p>
                  {fundingHistory.length > 1 ? (
                    <div className="review-history" data-testid="funding-review-history">
                      <span className="muted">历史核对</span>
                      {fundingHistory.map((item, index) => {
                        const active = item.id === fundingReview?.id
                        return (
                          <button
                            key={item.id}
                            type="button"
                            className={`ghost-btn${active ? ' active' : ''}`}
                            onClick={() => setFundingReview(item)}
                            data-testid={`funding-review-${item.id}`}
                          >
                            {index === 0 ? '最新' : `v${fundingHistory.length - index}`} ·{' '}
                            {REVIEW_STATUS_LABEL[item.status] ?? item.status}
                            {item.bound_rules?.length
                              ? ` · ${item.bound_rules.length} 条绑定`
                              : ' · 无绑定'}
                          </button>
                        )
                      })}
                    </div>
                  ) : null}
                  {fundingError ? (
                    <p className="msg error" role="alert">
                      {fundingError}
                    </p>
                  ) : null}
                  {fundingLoading && !fundingReview ? (
                    <p className="muted">正在加载已有核对结果…</p>
                  ) : null}
                  {!fundingLoading && !fundingReview && !fundingError ? (
                    <div className="empty-state compact">
                      <strong>尚未核对申请经费</strong>
                      <p>请先上传申报书与预算表 PDF，再点击「开始核对」。</p>
                    </div>
                  ) : null}
                  {finding ? (
                    <article
                      className={`issue-card status-review-${String(finding.status).toLowerCase()}`}
                      data-testid="funding-issue-card"
                    >
                      <header className="issue-card-head">
                        <div>
                          <p className="eyebrow">RULE-007 · 问题卡</p>
                          <h4>申请经费跨文件一致性</h4>
                        </div>
                        <span className={`status-chip status-review-${String(finding.status).toLowerCase()}`}>
                          {REVIEW_STATUS_LABEL[finding.status] ?? finding.status}
                        </span>
                      </header>

                      <dl className="issue-grid">
                        <div>
                          <dt>检查字段</dt>
                          <dd>{finding.check_field}</dd>
                        </div>
                        <div>
                          <dt>当前状态</dt>
                          <dd>{REVIEW_STATUS_LABEL[finding.status] ?? finding.status}</dd>
                        </div>
                        <div>
                          <dt>差额</dt>
                          <dd>
                            {finding.difference_yuan == null
                              ? '无（待确认/不可比）'
                              : formatYuan(finding.difference_yuan)}
                          </dd>
                        </div>
                        <div className="issue-reason">
                          <dt>原因</dt>
                          <dd>{finding.reason}</dd>
                        </div>
                      </dl>

                      <div className="issue-sides">
                        {(['left', 'right'] as const).map((key) => {
                          const side = finding[key]
                          const title = key === 'left' ? '申报书侧' : '预算表侧'
                          const clickable = Boolean(side?.material_id && side.page_number)
                          return (
                            <div key={key} className="issue-side">
                              <h5>{title}</h5>
                              <p className="issue-side-meta">
                                {side?.original_filename ?? '未提取'}
                                {side?.page_number ? ` · 第 ${side.page_number} 页` : ''}
                              </p>
                              <dl>
                                <div>
                                  <dt>字段</dt>
                                  <dd>{sideLabel(side, '申请经费')}</dd>
                                </div>
                                <div>
                                  <dt>原始值</dt>
                                  <dd>
                                    {clickable ? (
                                      <button
                                        type="button"
                                        className="evidence-link"
                                        onClick={() => openEvidence(side)}
                                        title="打开对应文件与页码并高亮"
                                      >
                                        {side?.raw_value ?? '无法读取'}
                                      </button>
                                    ) : (
                                      <span>{side?.raw_value ?? '无法读取'}</span>
                                    )}
                                  </dd>
                                </div>
                                <div>
                                  <dt>规范化金额</dt>
                                  <dd>{formatYuan(side?.normalized_amount_yuan ?? side?.amount_yuan)}</dd>
                                </div>
                                <div>
                                  <dt>单位</dt>
                                  <dd>
                                    原文 {side?.raw_unit || side?.display_unit || '未知'}
                                    {side?.normalized_unit ? ` → ${side.normalized_unit}` : ''}
                                  </dd>
                                </div>
                              </dl>
                            </div>
                          )
                        })}
                      </div>
                    </article>
                  ) : null}
                  {fundingReview ? (
                    <aside className="bound-rules" data-testid="bound-rules">
                      <div className="panel-head subhead">
                        <h3>本次绑定规则</h3>
                        <span className="muted">
                          {boundRules.length > 0
                            ? `${boundRules.length} 条（快照于本次核对）`
                            : '本次未绑定已启用规则'}
                        </span>
                      </div>
                      {boundRules.length === 0 ? (
                        <p className="muted">停用后的规则不会进入新审查；旧核对仍保留当时版本。</p>
                      ) : (
                        <ul className="bound-rule-list">
                          {boundRules.map((rule: BoundRule) => (
                            <li
                              key={`${rule.rule_id}-${rule.version_id}`}
                              className="bound-rule-card"
                              data-testid={`bound-rule-${rule.rule_code}`}
                            >
                              <div className="candidate-head">
                                <span className="status-chip">{rule.rule_code}</span>
                                <span className="status-chip">v{rule.version_number}</span>
                                <strong>{rule.name}</strong>
                              </div>
                              <dl className="issue-grid">
                                <div>
                                  <dt>类别</dt>
                                  <dd>{rule.category || '未标注（不因此判 FAIL）'}</dd>
                                </div>
                                <div>
                                  <dt>比较对象</dt>
                                  <dd>{rule.compare_field}</dd>
                                </div>
                                <div>
                                  <dt>要求</dt>
                                  <dd>
                                    {comparatorLabel(rule.comparator)}{' '}
                                    {formatYuan(rule.amount_yuan)}
                                    {rule.amount_raw ? `（原文 ${rule.amount_raw}）` : ''}
                                  </dd>
                                </div>
                                <div>
                                  <dt>申请经费对照</dt>
                                  <dd>{formatYuan(rule.application_amount_yuan)}</dd>
                                </div>
                              </dl>
                              {rule.source_clause || rule.source_quote ? (
                                <p className="candidate-quote">
                                  {rule.source_clause ? `${rule.source_clause} · ` : ''}
                                  {rule.source_quote || '无摘录'}
                                </p>
                              ) : null}
                              {rule.display_note ? (
                                <p className="muted">{rule.display_note}</p>
                              ) : null}
                            </li>
                          ))}
                        </ul>
                      )}
                    </aside>
                  ) : null}
                </div>

                <div className="materials-section">
                  <div className="panel-head subhead">
                    <h3>材料上传</h3>
                    <button
                      type="button"
                      className="ghost-btn"
                      onClick={() => void loadMaterials(detail.id)}
                      disabled={materialsLoading || uploading}
                    >
                      {materialsLoading ? '刷新中…' : '刷新材料'}
                    </button>
                  </div>

                  <form className="upload-form" onSubmit={(event) => void handleUpload(event)}>
                    <div className="upload-row">
                      <label htmlFor="material-category">类别</label>
                      <select
                        id="material-category"
                        value={uploadCategory}
                        onChange={(event) => setUploadCategory(event.target.value as MaterialCategory)}
                        disabled={uploading}
                      >
                        {CATEGORY_OPTIONS.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="upload-row">
                      <label htmlFor="material-files">PDF 文件（可多选）</label>
                      <input
                        id="material-files"
                        type="file"
                        accept="application/pdf,.pdf"
                        multiple
                        disabled={uploading}
                        onChange={(event) => {
                          setUploadFiles(event.target.files)
                          setUploadError(null)
                          setUploadNotice(null)
                        }}
                      />
                    </div>
                    <button type="submit" disabled={!canUpload}>
                      {uploading ? '上传处理中…' : '上传材料'}
                    </button>
                    {uploading ? (
                      <p className="msg muted" role="status">
                        正在同步上传并解析，请稍候…
                      </p>
                    ) : null}
                    {uploadError ? <p className="msg error" role="alert">{uploadError}</p> : null}
                    {uploadNotice ? <p className="msg ok">{uploadNotice}</p> : null}
                  </form>

                  {materialsError ? <p className="msg error" role="alert">{materialsError}</p> : null}

                  <h3 className="section-title">材料列表</h3>
                  {materialsLoading && materials.length === 0 ? (
                    <p className="muted">正在加载材料…</p>
                  ) : null}
                  {!materialsLoading && materials.length === 0 ? (
                    <div className="empty-state compact">
                      <strong>尚未上传材料</strong>
                      <p>可上传申报书、预算表等 PDF；刷新或重启后端后仍可预览。</p>
                    </div>
                  ) : null}

                  {materials.length > 0 ? (
                    <ul className="material-list">
                      {materials.map((material) => {
                        const active = material.id === selectedId
                        return (
                          <li key={material.id}>
                            <button
                              type="button"
                              className={`material-card${active ? ' active' : ''}`}
                              onClick={() => openMaterial(material)}
                            >
                              <span className="material-name">{material.original_filename}</span>
                              <span className="material-meta">
                                <span className="status-chip">{CATEGORY_LABEL[material.category]}</span>
                                <span className={`status-chip status-${material.status.toLowerCase()}`}>
                                  {STATUS_LABEL[material.status]}
                                </span>
                                <span>
                                  {material.page_count != null ? `${material.page_count} 页` : '页数未知'}
                                </span>
                              </span>
                              {material.status === 'FAILED' && material.error_summary ? (
                                <span className="material-error">{material.error_summary}</span>
                              ) : null}
                            </button>
                          </li>
                        )
                      })}
                    </ul>
                  ) : null}

                  <h3 className="section-title">页面预览</h3>
                  {!selectedMaterial ? (
                    <p className="muted">点击就绪材料后，可在此翻页查看页图与原生文本。</p>
                  ) : !previewMaterial ? (
                    <p className="msg error" role="alert">
                      {selectedMaterial.status === 'FAILED'
                        ? `无法预览：${selectedMaterial.error_summary || '解析失败'}`
                        : '材料仍在处理中，请稍后刷新。'}
                    </p>
                  ) : (
                    <div className="preview-panel">
                      <div className="preview-toolbar">
                        <button
                          type="button"
                          className="ghost-btn"
                          disabled={pageNumber <= 1 || previewLoading}
                          onClick={() => setPageNumber((prev) => Math.max(1, prev - 1))}
                        >
                          上一页
                        </button>
                        <span className="page-indicator">
                          第 {pageNumber} 页 / 共 {totalPages} 页
                        </span>
                        <button
                          type="button"
                          className="ghost-btn"
                          disabled={pageNumber >= totalPages || previewLoading}
                          onClick={() => setPageNumber((prev) => Math.min(totalPages, prev + 1))}
                        >
                          下一页
                        </button>
                      </div>

                      {previewError ? <p className="msg error" role="alert">{previewError}</p> : null}

                      <div className="preview-image-wrap">
                        <div className="preview-image-frame">
                          <img
                            key={`${previewMaterial.id}-${pageNumber}`}
                            src={pageImageUrl(previewMaterial.id, pageNumber)}
                            alt={`${previewMaterial.original_filename} 第 ${pageNumber} 页`}
                            className="preview-image"
                          />
                          {highlight &&
                          highlight.materialId === previewMaterial.id &&
                          highlight.pageNumber === pageNumber &&
                          highlight.bbox.page_width > 0 &&
                          highlight.bbox.page_height > 0 ? (
                            <div
                              className="evidence-highlight"
                              style={{
                                left: `${(highlight.bbox.x0 / highlight.bbox.page_width) * 100}%`,
                                top: `${(highlight.bbox.y0 / highlight.bbox.page_height) * 100}%`,
                                width: `${((highlight.bbox.x1 - highlight.bbox.x0) / highlight.bbox.page_width) * 100}%`,
                                height: `${((highlight.bbox.y1 - highlight.bbox.y0) / highlight.bbox.page_height) * 100}%`,
                              }}
                              data-testid="evidence-highlight"
                            />
                          ) : null}
                        </div>
                      </div>

                      <div className="preview-text">
                        <div className="preview-text-head">本页原生文本</div>
                        {previewLoading ? <p className="muted">正在读取文本…</p> : null}
                        {!previewLoading && pageText != null ? (
                          <pre>{pageText || '（本页无原生文本）'}</pre>
                        ) : null}
                      </div>
                    </div>
                  )}
                </div>
              </>
            ) : null}
          </section>
        ) : null}
      </main>
    </div>
  )
}
