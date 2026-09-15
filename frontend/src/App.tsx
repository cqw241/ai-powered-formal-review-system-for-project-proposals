import { useCallback, useEffect, useMemo, useState } from 'react'
import { createProject, getProject, listProjects } from './api'
import type { Project } from './types'
import './App.css'

type View =
  | { kind: 'list' }
  | { kind: 'detail'; projectId: string }

function formatDateTime(value: string): string {
  // Backend serializes created_at as UTC ISO-8601 with a trailing Z.
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZoneName: 'short',
  }).format(date)
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
    void getProject(view.projectId)
      .then((project) => {
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
  }, [view])

  const canSubmit = useMemo(() => name.trim().length > 0 && !creating, [name, creating])

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
      await loadProjects()
      setView({ kind: 'detail', projectId: project.id })
    } catch (error) {
      setCreateError(error instanceof Error ? error.message : '创建项目失败')
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">高校项目申报 · 形式审查辅助</p>
          <h1>规证AI</h1>
        </div>
        <p className="header-note">
          本阶段仅支持项目创建与持久化查看。尚未开始审查的项目不会显示为“审查通过”。
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
                    <span className="muted inline-note">B02/B03 才会接入材料上传与金额核对。</span>
                  </dd>
                </div>
              </dl>
            ) : null}
          </section>
        )}
      </main>
    </div>
  )
}
