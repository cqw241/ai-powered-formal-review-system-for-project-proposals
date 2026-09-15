import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  createProject,
  getPageText,
  getProject,
  listMaterials,
  listProjects,
  pageImageUrl,
  uploadMaterial,
} from './api'
import type { Material, MaterialCategory, Project } from './types'
import './App.css'

type View =
  | { kind: 'list' }
  | { kind: 'detail'; projectId: string }

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

  const loadMaterials = useCallback(async (projectId: string) => {
    setMaterialsLoading(true)
    setMaterialsError(null)
    try {
      const data = await listMaterials(projectId)
      setMaterials(data)
      return data
    } catch (error) {
      setMaterialsError(error instanceof Error ? error.message : '加载材料列表失败')
      setMaterials([])
      return [] as Material[]
    } finally {
      setMaterialsLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadProjects()
  }, [loadProjects])

  useEffect(() => {
    if (view.kind !== 'detail') {
      return
    }
    let cancelled = false
    setDetailLoading(true)
    setDetailError(null)
    setDetail(null)
    setMaterials([])
    setSelectedId(null)
    setPageNumber(1)
    setPageText(null)
    setPreviewError(null)
    setUploadError(null)
    setUploadNotice(null)
    setUploadFiles(null)

    void Promise.all([getProject(view.projectId), loadMaterials(view.projectId)])
      .then(([project]) => {
        if (!cancelled) {
          setDetail(project)
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setDetailError(error instanceof Error ? error.message : '加载项目失败')
        }
      })
      .finally(() => {
        if (!cancelled) {
          setDetailLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [view, loadMaterials])

  const selectedMaterial = useMemo(
    () => materials.find((item) => item.id === selectedId) ?? null,
    [materials, selectedId],
  )

  useEffect(() => {
    if (!selectedMaterial || selectedMaterial.status !== 'READY') {
      setPageText(null)
      setPreviewError(
        selectedMaterial?.status === 'FAILED'
          ? selectedMaterial.error_summary || '材料解析失败'
          : null,
      )
      setPreviewLoading(false)
      return
    }

    const total = selectedMaterial.page_count ?? 0
    if (total < 1) {
      setPageText(null)
      setPreviewError('材料没有可预览的页面')
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
    void getPageText(selectedMaterial.id, safePage)
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
  }, [selectedMaterial, pageNumber])

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

    setUploading(true)
    setUploadError(null)
    setUploadNotice(null)

    const files = Array.from(uploadFiles)
    const successes: string[] = []
    const failures: string[] = []

    for (const file of files) {
      try {
        const material = await uploadMaterial(detail.id, file, uploadCategory)
        successes.push(`${material.original_filename}（${STATUS_LABEL[material.status]}）`)
        if (material.status === 'FAILED') {
          failures.push(
            `${material.original_filename}：${material.error_summary || '解析失败'}`,
          )
        }
      } catch (error) {
        failures.push(`${file.name}：${error instanceof Error ? error.message : '上传失败'}`)
      }
    }

    const refreshed = await loadMaterials(detail.id)
    if (successes.length > 0) {
      setUploadNotice(`已处理 ${successes.length} 个文件：${successes.join('；')}`)
      const ready = refreshed.find((item) => item.status === 'READY')
      if (ready && (!selectedId || !refreshed.some((item) => item.id === selectedId))) {
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
    if (material.status !== 'READY') {
      setSelectedId(material.id)
      setPageNumber(1)
      return
    }
    setSelectedId(material.id)
    setPageNumber(1)
  }

  const totalPages = selectedMaterial?.page_count ?? 0

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">高校项目申报 · 形式审查辅助</p>
          <h1>规证AI</h1>
        </div>
        <p className="header-note">
          本阶段支持项目持久化与 PDF 材料上传预览。尚未开始审查的项目不会显示为“审查通过”。
        </p>
      </header>

      <main className="app-main">
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
        ) : (
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
                      <span className="status-chip">尚未审查</span>
                      <span className="muted inline-note">B03 才会接入金额核对与问题卡。</span>
                    </dd>
                  </div>
                </dl>

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
                  ) : selectedMaterial.status !== 'READY' ? (
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
                        <img
                          key={`${selectedMaterial.id}-${pageNumber}`}
                          src={pageImageUrl(selectedMaterial.id, pageNumber)}
                          alt={`${selectedMaterial.original_filename} 第 ${pageNumber} 页`}
                          className="preview-image"
                        />
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
        )}
      </main>
    </div>
  )
}
