import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  disableRule,
  enableCandidateRule,
  enableRule,
  getPolicy,
  getPolicyPageText,
  listPolicies,
  policyPageImageUrl,
  updatePolicyCandidate,
  updateRule,
  uploadPolicy,
} from './api'
import type { PolicyCandidate, PolicyDetail, PolicySummary, Rule } from './types'

const STATUS_LABEL: Record<PolicySummary['status'], string> = {
  PROCESSING: '处理中',
  READY: '就绪',
  FAILED: '失败',
}

const KIND_LABEL: Record<string, string> = {
  FUNDING_CAP: '经费上限',
  CLAUSE: '条款候选',
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

function shouldShowNormalizedAmount(candidate: PolicyCandidate): boolean {
  // "未知（非 0）" only applies to amount fields, not generic clause drafts.
  return (
    candidate.kind === 'FUNDING_CAP' ||
    Boolean(candidate.amount_raw) ||
    candidate.amount_yuan != null
  )
}

type DraftFields = {
  title: string
  category: string
  amount_raw: string
  source_clause: string
  source_page: string
  source_quote: string
}

type RuleDraftFields = {
  name: string
  category: string
  comparator: string
  amount_raw: string
  source_clause: string
  source_page: string
  source_quote: string
}

function toDraft(candidate: PolicyCandidate): DraftFields {
  return {
    title: candidate.title ?? '',
    category: candidate.category ?? '',
    amount_raw: candidate.amount_raw ?? '',
    source_clause: candidate.source_clause ?? '',
    source_page: candidate.source_page != null ? String(candidate.source_page) : '',
    source_quote: candidate.source_quote ?? '',
  }
}

function toRuleDraft(rule: Rule): RuleDraftFields {
  const version = rule.current_version
  return {
    name: version?.name ?? rule.name ?? '',
    category: version?.category ?? '',
    comparator: version?.comparator ?? 'LE',
    amount_raw: version?.amount_raw ?? '',
    source_clause: version?.source_clause ?? '',
    source_page: version?.source_page != null ? String(version.source_page) : '',
    source_quote: version?.source_quote ?? '',
  }
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

type Props = {
  onBack: () => void
}

export default function PolicyWorkspace({ onBack }: Props) {
  const [policies, setPolicies] = useState<PolicySummary[]>([])
  const [listLoading, setListLoading] = useState(true)
  const [listError, setListError] = useState<string | null>(null)

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<PolicyDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)

  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [uploadNotice, setUploadNotice] = useState<string | null>(null)

  const [pageNumber, setPageNumber] = useState(1)
  const [pageText, setPageText] = useState<string | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)

  const [activeCandidateId, setActiveCandidateId] = useState<string | null>(null)
  const [draft, setDraft] = useState<DraftFields | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saveNotice, setSaveNotice] = useState<string | null>(null)

  const [ruleDraft, setRuleDraft] = useState<RuleDraftFields | null>(null)
  const [ruleSaving, setRuleSaving] = useState(false)
  const [ruleError, setRuleError] = useState<string | null>(null)
  const [ruleNotice, setRuleNotice] = useState<string | null>(null)
  const [ruleBusyId, setRuleBusyId] = useState<string | null>(null)

  const loadPolicies = useCallback(async () => {
    setListLoading(true)
    setListError(null)
    try {
      const data = await listPolicies()
      setPolicies(data)
      return data
    } catch (error) {
      setListError(error instanceof Error ? error.message : '加载政策列表失败')
      setPolicies([])
      return [] as PolicySummary[]
    } finally {
      setListLoading(false)
    }
  }, [])

  const loadDetail = useCallback(async (policyId: string) => {
    setDetailLoading(true)
    setDetailError(null)
    try {
      const data = await getPolicy(policyId)
      setDetail(data)
      setSelectedId(policyId)
      return data
    } catch (error) {
      setDetail(null)
      setDetailError(error instanceof Error ? error.message : '加载政策详情失败')
      return null
    } finally {
      setDetailLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadPolicies()
  }, [loadPolicies])

  useEffect(() => {
    if (!detail || detail.status !== 'READY') {
      setPageText(null)
      setPreviewError(
        detail?.status === 'FAILED'
          ? detail.error_summary || '政策解析失败'
          : detail
            ? '政策仍在处理中'
            : null,
      )
      setPreviewLoading(false)
      return
    }

    const total = detail.page_count ?? 0
    if (total < 1) {
      setPageText(null)
      setPreviewError('政策没有可预览的页面')
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
    void getPolicyPageText(detail.id, safePage)
      .then((result) => {
        if (!cancelled) {
          setPageText(result.text)
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setPageText(null)
          setPreviewError(error instanceof Error ? error.message : '读取政策页文本失败')
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
  }, [detail, pageNumber])

  const totalPages = detail?.page_count ?? 0
  const canUpload = Boolean(uploadFile && !uploading)

  const activeCandidate = useMemo(
    () => detail?.candidates.find((item) => item.id === activeCandidateId) ?? null,
    [detail, activeCandidateId],
  )

  function openPolicy(policyId: string) {
    setPageNumber(1)
    setActiveCandidateId(null)
    setDraft(null)
    setSaveError(null)
    setSaveNotice(null)
    setRuleDraft(null)
    setRuleError(null)
    setRuleNotice(null)
    void loadDetail(policyId)
  }

  function openCandidate(candidate: PolicyCandidate) {
    setActiveCandidateId(candidate.id)
    setDraft(toDraft(candidate))
    setSaveError(null)
    setSaveNotice(null)
    setRuleError(null)
    setRuleNotice(null)
    setRuleDraft(candidate.rule ? toRuleDraft(candidate.rule) : null)
    if (candidate.source_page) {
      setPageNumber(candidate.source_page)
    }
  }

  function attachRule(candidateId: string, rule: Rule) {
    setDetail((prev) =>
      prev
        ? {
            ...prev,
            candidates: prev.candidates.map((item) =>
              item.id === candidateId ? { ...item, rule } : item,
            ),
          }
        : prev,
    )
    if (activeCandidateId === candidateId) {
      setRuleDraft(toRuleDraft(rule))
    }
  }

  async function handleUpload(event: React.FormEvent) {
    event.preventDefault()
    if (!uploadFile) {
      setUploadError('请选择政策 PDF')
      return
    }
    setUploading(true)
    setUploadError(null)
    setUploadNotice(null)
    try {
      const created = await uploadPolicy(uploadFile)
      setUploadNotice(
        created.status === 'FAILED'
          ? `上传完成但解析失败：${created.error_summary || '未知原因'}`
          : `已上传并提取 ${created.candidates.length} 条候选要求`,
      )
      setUploadFile(null)
      await loadPolicies()
      if (created.status === 'READY') {
        setPageNumber(1)
        setActiveCandidateId(null)
        setDraft(null)
        setDetail(created)
        setSelectedId(created.id)
        const funding = created.candidates.find((item) => item.kind === 'FUNDING_CAP')
        if (funding) {
          openCandidate(funding)
        }
      }
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : '上传政策失败')
    } finally {
      setUploading(false)
    }
  }

  async function handleSaveCandidate(event: React.FormEvent) {
    event.preventDefault()
    if (!detail || !activeCandidate || !draft) {
      return
    }
    setSaving(true)
    setSaveError(null)
    setSaveNotice(null)
    try {
      const pageValue = draft.source_page.trim()
      const sourcePage = pageValue ? Number.parseInt(pageValue, 10) : null
      if (pageValue && (!Number.isFinite(sourcePage) || (sourcePage ?? 0) < 1)) {
        throw new Error('来源页码须为正整数')
      }
      const updated = await updatePolicyCandidate(detail.id, activeCandidate.id, {
        title: draft.title.trim() || activeCandidate.title,
        category: draft.category.trim() || null,
        amount_raw: draft.amount_raw.trim() || null,
        source_clause: draft.source_clause.trim() || null,
        source_page: sourcePage,
        source_quote: draft.source_quote.trim() || null,
      })
      setDetail((prev) =>
        prev
          ? {
              ...prev,
              candidates: prev.candidates.map((item) => (item.id === updated.id ? updated : item)),
            }
          : prev,
      )
      setDraft(toDraft(updated))
      setSaveNotice('已保存；刷新后仍会保留。')
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : '保存候选要求失败')
    } finally {
      setSaving(false)
    }
  }

  async function handleEnable(candidate: PolicyCandidate) {
    if (!detail) {
      return
    }
    setRuleBusyId(candidate.id)
    setRuleError(null)
    setRuleNotice(null)
    try {
      const rule = await enableCandidateRule(detail.id, candidate.id)
      attachRule(candidate.id, rule)
      setActiveCandidateId(candidate.id)
      setDraft(toDraft(candidate))
      setRuleNotice(`已启用 ${rule.rule_code}（v${rule.current_version_number}）。刷新后仍会保留。`)
    } catch (error) {
      setRuleError(error instanceof Error ? error.message : '启用规则失败')
    } finally {
      setRuleBusyId(null)
    }
  }

  async function handleToggleEnabled(candidate: PolicyCandidate) {
    if (!candidate.rule) {
      return
    }
    setRuleBusyId(candidate.id)
    setRuleError(null)
    setRuleNotice(null)
    try {
      const rule = candidate.rule.enabled
        ? await disableRule(candidate.rule.id)
        : await enableRule(candidate.rule.id)
      attachRule(candidate.id, rule)
      setRuleNotice(
        rule.enabled
          ? `已重新启用 ${rule.rule_code}（v${rule.current_version_number}）`
          : `已停用 ${rule.rule_code}；版本记录仍保留，新审查不再绑定。`,
      )
    } catch (error) {
      setRuleError(error instanceof Error ? error.message : '更新启用状态失败')
    } finally {
      setRuleBusyId(null)
    }
  }

  async function handleSaveRule(event: React.FormEvent) {
    event.preventDefault()
    if (!activeCandidate?.rule || !ruleDraft) {
      return
    }
    setRuleSaving(true)
    setRuleError(null)
    setRuleNotice(null)
    try {
      const pageValue = ruleDraft.source_page.trim()
      const sourcePage = pageValue ? Number.parseInt(pageValue, 10) : null
      if (pageValue && (!Number.isFinite(sourcePage) || (sourcePage ?? 0) < 1)) {
        throw new Error('来源页码须为正整数')
      }
      const updated = await updateRule(activeCandidate.rule.id, {
        name: ruleDraft.name.trim() || activeCandidate.rule.name,
        category: ruleDraft.category.trim() || null,
        comparator: ruleDraft.comparator.trim() || 'LE',
        amount_raw: ruleDraft.amount_raw.trim() || null,
        source_clause: ruleDraft.source_clause.trim() || null,
        source_page: sourcePage,
        source_quote: ruleDraft.source_quote.trim() || null,
      })
      attachRule(activeCandidate.id, updated)
      setRuleNotice(`已保存为 v${updated.current_version_number}；历史版本不变。`)
    } catch (error) {
      setRuleError(error instanceof Error ? error.message : '保存规则失败')
    } finally {
      setRuleSaving(false)
    }
  }

  return (
    <section className="panel" data-testid="policy-workspace">
      <div className="panel-head">
        <h2>政策与候选要求</h2>
        <button type="button" className="ghost-btn" onClick={onBack}>
          返回项目列表
        </button>
      </div>

      <p className="muted funding-hint">
        上传模拟申报指南 PDF 后可预览原文并编辑候选草稿。经费上限候选可启为 RULE-005；编辑已启用规则会新增版本，停用后不再进入新审查。
      </p>

      <form className="upload-form" onSubmit={(event) => void handleUpload(event)}>
        <div className="upload-row">
          <label htmlFor="policy-file">政策 PDF</label>
          <input
            id="policy-file"
            type="file"
            accept="application/pdf,.pdf"
            disabled={uploading}
            onChange={(event) => {
              setUploadFile(event.target.files?.[0] ?? null)
              setUploadError(null)
              setUploadNotice(null)
            }}
          />
        </div>
        <button type="submit" disabled={!canUpload}>
          {uploading ? '上传并提取中…' : '上传政策'}
        </button>
        {uploadError ? (
          <p className="msg error" role="alert">
            {uploadError}
          </p>
        ) : null}
        {uploadNotice ? <p className="msg ok">{uploadNotice}</p> : null}
      </form>

      <div className="panel-head subhead">
        <h3>已上传政策</h3>
        <button
          type="button"
          className="ghost-btn"
          onClick={() => void loadPolicies()}
          disabled={listLoading}
        >
          {listLoading ? '刷新中…' : '刷新列表'}
        </button>
      </div>

      {listError ? (
        <p className="msg error" role="alert">
          {listError}
        </p>
      ) : null}
      {listLoading && policies.length === 0 ? <p className="muted">正在加载政策…</p> : null}
      {!listLoading && policies.length === 0 ? (
        <div className="empty-state compact">
          <strong>尚未上传政策</strong>
          <p>可上传 docs/kickoff/development_batch/policy/ 下的模拟指南 PDF。</p>
        </div>
      ) : null}

      {policies.length > 0 ? (
        <ul className="material-list">
          {policies.map((policy) => {
            const active = policy.id === selectedId
            return (
              <li key={policy.id}>
                <button
                  type="button"
                  className={`material-card${active ? ' active' : ''}`}
                  onClick={() => openPolicy(policy.id)}
                  data-testid={`policy-card-${policy.id}`}
                >
                  <span className="material-name">{policy.title || policy.original_filename}</span>
                  <span className="material-meta">
                    <span className={`status-chip status-${policy.status.toLowerCase()}`}>
                      {STATUS_LABEL[policy.status]}
                    </span>
                    <span>{policy.page_count != null ? `${policy.page_count} 页` : '页数未知'}</span>
                    <span>{policy.candidate_count} 条候选</span>
                    <time dateTime={policy.created_at}>{formatDateTime(policy.created_at)}</time>
                  </span>
                  {policy.status === 'FAILED' && policy.error_summary ? (
                    <span className="material-error">{policy.error_summary}</span>
                  ) : null}
                </button>
              </li>
            )
          })}
        </ul>
      ) : null}

      {detailLoading ? <p className="muted">正在加载政策详情…</p> : null}
      {detailError ? (
        <p className="msg error" role="alert">
          {detailError}
        </p>
      ) : null}

      {detail ? (
        <>
          <h3 className="section-title">原文预览</h3>
          {detail.status !== 'READY' ? (
            <p className="msg error" role="alert">
              {detail.status === 'FAILED'
                ? `无法预览：${detail.error_summary || '解析失败'}`
                : '政策仍在处理中'}
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
              {previewError ? (
                <p className="msg error" role="alert">
                  {previewError}
                </p>
              ) : null}
              <div className="preview-image-wrap">
                <div className="preview-image-frame">
                  <img
                    key={`${detail.id}-${pageNumber}`}
                    src={policyPageImageUrl(detail.id, pageNumber)}
                    alt={`${detail.original_filename} 第 ${pageNumber} 页`}
                    className="preview-image"
                  />
                </div>
              </div>
              <div className="preview-text">
                <div className="preview-text-head">本页原生文本</div>
                {previewLoading ? <p className="muted">正在读取文本…</p> : null}
                {!previewLoading && pageText != null ? (
                  <pre data-testid="policy-page-text">{pageText || '（本页无原生文本）'}</pre>
                ) : null}
              </div>
            </div>
          )}

          <h3 className="section-title">候选要求（可编辑草稿）</h3>
          {detail.candidates.length === 0 ? (
            <div className="empty-state compact">
              <strong>未提取到候选要求</strong>
              <p>可手工确认政策原文后，在后续分支补充规则。</p>
            </div>
          ) : (
            <ul className="candidate-list" data-testid="policy-candidate-list">
              {detail.candidates.map((candidate) => {
                const active = candidate.id === activeCandidateId
                const rule = candidate.rule
                const busy = ruleBusyId === candidate.id
                return (
                  <li key={candidate.id} className="candidate-row">
                    <button
                      type="button"
                      className={`candidate-card${active ? ' active' : ''}${
                        candidate.kind === 'FUNDING_CAP' ? ' funding-cap' : ''
                      }`}
                      onClick={() => openCandidate(candidate)}
                      data-testid={`candidate-${candidate.id}`}
                      data-kind={candidate.kind}
                      data-category={candidate.category ?? ''}
                    >
                      <span className="candidate-head">
                        <span className="status-chip">
                          {KIND_LABEL[candidate.kind] ?? candidate.kind}
                        </span>
                        {candidate.source_clause ? (
                          <span className="status-chip">{candidate.source_clause}</span>
                        ) : null}
                        {rule ? (
                          <span
                            className={`status-chip ${rule.enabled ? 'status-review-pass' : ''}`}
                            data-testid={`rule-status-${candidate.id}`}
                          >
                            {rule.enabled ? '已启用' : '已停用'} · {rule.rule_code} v
                            {rule.current_version_number}
                          </span>
                        ) : candidate.kind === 'FUNDING_CAP' ? (
                          <span className="status-chip">草稿未启用</span>
                        ) : null}
                        <strong>{candidate.title}</strong>
                      </span>
                      <span className="candidate-meta">
                        {candidate.category ? <span>类别：{candidate.category}</span> : null}
                        {candidate.amount_raw ? (
                          <span>
                            金额：{candidate.amount_raw}
                            {candidate.comparator === 'LE' ? '（上限）' : ''}
                          </span>
                        ) : null}
                        {shouldShowNormalizedAmount(candidate) ? (
                          <span data-testid="candidate-normalized-amount">
                            规范化：{formatYuan(candidate.amount_yuan)}
                          </span>
                        ) : null}
                        {candidate.source_page ? <span>来源页：第 {candidate.source_page} 页</span> : null}
                      </span>
                      {candidate.source_quote ? (
                        <span className="candidate-quote">{candidate.source_quote}</span>
                      ) : null}
                    </button>
                    {candidate.kind === 'FUNDING_CAP' ? (
                      <div className="candidate-actions">
                        {rule ? (
                          <button
                            type="button"
                            className="ghost-btn"
                            disabled={busy}
                            onClick={() => void handleToggleEnabled(candidate)}
                            data-testid={`rule-toggle-${candidate.id}`}
                          >
                            {busy ? '处理中…' : rule.enabled ? '停用规则' : '重新启用'}
                          </button>
                        ) : (
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => void handleEnable(candidate)}
                            data-testid={`rule-enable-${candidate.id}`}
                          >
                            {busy ? '启用中…' : '启用为规则'}
                          </button>
                        )}
                      </div>
                    ) : null}
                  </li>
                )
              })}
            </ul>
          )}

          {activeCandidate && draft ? (
            <form
              className="candidate-edit-form"
              onSubmit={(event) => void handleSaveCandidate(event)}
              data-testid="candidate-edit-form"
            >
              <div className="panel-head subhead">
                <h3>编辑候选</h3>
                <span className="muted">修改后保存，刷新页面仍保留</span>
              </div>
              <div className="candidate-edit-grid">
                <label>
                  标题
                  <input
                    value={draft.title}
                    onChange={(event) => setDraft({ ...draft, title: event.target.value })}
                    disabled={saving}
                  />
                </label>
                <label>
                  类别
                  <input
                    value={draft.category}
                    onChange={(event) => setDraft({ ...draft, category: event.target.value })}
                    disabled={saving}
                    data-testid="candidate-category-input"
                  />
                </label>
                <label>
                  金额原文
                  <input
                    value={draft.amount_raw}
                    onChange={(event) => setDraft({ ...draft, amount_raw: event.target.value })}
                    disabled={saving}
                    data-testid="candidate-amount-input"
                    placeholder="例如 15万元；无法解析时规范化为未知"
                  />
                </label>
                <label>
                  来源条款
                  <input
                    value={draft.source_clause}
                    onChange={(event) => setDraft({ ...draft, source_clause: event.target.value })}
                    disabled={saving}
                  />
                </label>
                <label>
                  来源页码
                  <input
                    value={draft.source_page}
                    onChange={(event) => setDraft({ ...draft, source_page: event.target.value })}
                    disabled={saving}
                    inputMode="numeric"
                  />
                </label>
                <label className="full-width">
                  原文摘录
                  <textarea
                    value={draft.source_quote}
                    onChange={(event) => setDraft({ ...draft, source_quote: event.target.value })}
                    disabled={saving}
                    rows={3}
                    data-testid="candidate-quote-input"
                  />
                </label>
              </div>
              <div className="create-row">
                <button type="submit" disabled={saving}>
                  {saving ? '保存中…' : '保存修改'}
                </button>
              </div>
              {saveError ? (
                <p className="msg error" role="alert">
                  {saveError}
                </p>
              ) : null}
              {saveNotice ? <p className="msg ok">{saveNotice}</p> : null}
            </form>
          ) : null}

          {activeCandidate?.rule && ruleDraft ? (
            <form
              className="candidate-edit-form"
              onSubmit={(event) => void handleSaveRule(event)}
              data-testid="rule-edit-form"
            >
              <div className="panel-head subhead">
                <h3>编辑已启用规则</h3>
                <span className="muted">
                  {activeCandidate.rule.rule_code} ·{' '}
                  {activeCandidate.rule.enabled ? '已启用' : '已停用'} · 当前 v
                  {activeCandidate.rule.current_version_number}
                  ；保存将新增版本，不改历史版本
                </span>
              </div>
              <div className="candidate-edit-grid">
                <label>
                  名称
                  <input
                    value={ruleDraft.name}
                    onChange={(event) => setRuleDraft({ ...ruleDraft, name: event.target.value })}
                    disabled={ruleSaving}
                    data-testid="rule-name-input"
                  />
                </label>
                <label>
                  类别
                  <input
                    value={ruleDraft.category}
                    onChange={(event) => setRuleDraft({ ...ruleDraft, category: event.target.value })}
                    disabled={ruleSaving}
                    data-testid="rule-category-input"
                  />
                </label>
                <label>
                  比较对象
                  <input value="申请经费" disabled readOnly />
                </label>
                <label>
                  要求
                  <select
                    value={ruleDraft.comparator}
                    onChange={(event) =>
                      setRuleDraft({ ...ruleDraft, comparator: event.target.value })
                    }
                    disabled={ruleSaving}
                    data-testid="rule-comparator-input"
                  >
                    <option value="LE">不超过（LE）</option>
                    <option value="LT">小于（LT）</option>
                    <option value="EQ">等于（EQ）</option>
                    <option value="GE">不少于（GE）</option>
                  </select>
                </label>
                <label>
                  金额原文
                  <input
                    value={ruleDraft.amount_raw}
                    onChange={(event) =>
                      setRuleDraft({ ...ruleDraft, amount_raw: event.target.value })
                    }
                    disabled={ruleSaving}
                    data-testid="rule-amount-input"
                    placeholder="例如 28万元；无法解析时规范化为未知"
                  />
                </label>
                <label>
                  来源条款
                  <input
                    value={ruleDraft.source_clause}
                    onChange={(event) =>
                      setRuleDraft({ ...ruleDraft, source_clause: event.target.value })
                    }
                    disabled={ruleSaving}
                  />
                </label>
                <label>
                  来源页码
                  <input
                    value={ruleDraft.source_page}
                    onChange={(event) =>
                      setRuleDraft({ ...ruleDraft, source_page: event.target.value })
                    }
                    disabled={ruleSaving}
                    inputMode="numeric"
                  />
                </label>
                <label className="full-width">
                  政策出处 / 原文摘录
                  <textarea
                    value={ruleDraft.source_quote}
                    onChange={(event) =>
                      setRuleDraft({ ...ruleDraft, source_quote: event.target.value })
                    }
                    disabled={ruleSaving}
                    rows={3}
                  />
                </label>
              </div>
              <p className="muted">
                当前要求：{comparatorLabel(ruleDraft.comparator)}{' '}
                {activeCandidate.rule.current_version?.amount_yuan == null
                  ? '未知（非 0）'
                  : formatYuan(activeCandidate.rule.current_version.amount_yuan)}
              </p>
              <div className="create-row">
                <button type="submit" disabled={ruleSaving}>
                  {ruleSaving ? '保存中…' : '保存为新版本'}
                </button>
              </div>
              {ruleError ? (
                <p className="msg error" role="alert">
                  {ruleError}
                </p>
              ) : null}
              {ruleNotice ? <p className="msg ok">{ruleNotice}</p> : null}
            </form>
          ) : ruleError || ruleNotice ? (
            <>
              {ruleError ? (
                <p className="msg error" role="alert">
                  {ruleError}
                </p>
              ) : null}
              {ruleNotice ? <p className="msg ok">{ruleNotice}</p> : null}
            </>
          ) : null}
        </>
      ) : null}
    </section>
  )
}
