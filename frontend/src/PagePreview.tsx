import { useEffect, useMemo, useState } from 'react'
import { getPageText, pageImageUrl } from './api'
import { bboxToPercentStyle } from './evidenceHighlight'
import type { EvidenceBBox } from './types'

const ZOOM_MIN = 0.75
const ZOOM_MAX = 2.5
const ZOOM_STEP = 0.25

type Props = {
  materialId: string
  filename: string
  pageNumber: number
  pageCount?: number | null
  bbox?: EvidenceBBox | null
  onPageChange?: (page: number) => void
  compact?: boolean
  testId?: string
}

export default function PagePreview({
  materialId,
  filename,
  pageNumber,
  pageCount,
  bbox,
  onPageChange,
  compact = false,
  testId = 'page-preview',
}: Props) {
  const [zoom, setZoom] = useState(1)
  const [internalPage, setInternalPage] = useState(pageNumber)
  const [text, setText] = useState<string | null>(null)
  const [resolvedCount, setResolvedCount] = useState(pageCount ?? 0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const page = onPageChange ? pageNumber : internalPage

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    void getPageText(materialId, page)
      .then((result) => {
        if (cancelled) {
          return
        }
        setText(result.text)
        setResolvedCount(result.page_count)
      })
      .catch((err: unknown) => {
        if (cancelled) {
          return
        }
        setText(null)
        setError(err instanceof Error ? err.message : '读取页面文本失败')
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [materialId, page])

  const totalPages = resolvedCount || pageCount || 0
  const highlightStyle = useMemo(
    () => (bbox && page === pageNumber ? bboxToPercentStyle(bbox) : null),
    [bbox, page, pageNumber],
  )

  function changePage(next: number) {
    if (totalPages < 1) {
      return
    }
    const safe = Math.min(Math.max(next, 1), totalPages)
    if (onPageChange) {
      onPageChange(safe)
      return
    }
    setInternalPage(safe)
  }

  function changeZoom(next: number) {
    const clamped = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(next * 100) / 100))
    setZoom(clamped)
  }

  return (
    <div className={`preview-panel${compact ? ' compact' : ''}`} data-testid={testId}>
      <div className="preview-toolbar">
        <button
          type="button"
          className="ghost-btn"
          disabled={page <= 1 || loading}
          onClick={() => changePage(page - 1)}
        >
          上一页
        </button>
        <span className="page-indicator">
          {filename} · 第 {page} 页{totalPages ? ` / 共 ${totalPages} 页` : ''}
        </span>
        <button
          type="button"
          className="ghost-btn"
          disabled={loading || (totalPages > 0 && page >= totalPages)}
          onClick={() => changePage(page + 1)}
        >
          下一页
        </button>
        <span className="preview-zoom-controls">
          <button
            type="button"
            className="ghost-btn"
            disabled={zoom <= ZOOM_MIN}
            onClick={() => changeZoom(zoom - ZOOM_STEP)}
            data-testid={`${testId}-zoom-out`}
          >
            缩小
          </button>
          <span className="page-indicator" data-testid={`${testId}-zoom-label`}>
            {Math.round(zoom * 100)}%
          </span>
          <button
            type="button"
            className="ghost-btn"
            disabled={zoom >= ZOOM_MAX}
            onClick={() => changeZoom(zoom + ZOOM_STEP)}
            data-testid={`${testId}-zoom-in`}
          >
            放大
          </button>
        </span>
      </div>

      {error ? (
        <p className="msg error" role="alert">
          {error}
        </p>
      ) : null}

      <div className="preview-image-wrap">
        <div
          className="preview-image-frame"
          style={{ width: `${zoom * 100}%` }}
          data-testid={`${testId}-zoom-frame`}
          data-zoom={String(zoom)}
        >
          <img
            key={`${materialId}-${page}`}
            src={pageImageUrl(materialId, page)}
            alt={`${filename} 第 ${page} 页`}
            className="preview-image"
          />
          {highlightStyle ? (
            <div className="evidence-highlight" style={highlightStyle} data-testid="evidence-highlight" />
          ) : null}
        </div>
      </div>

      {compact ? null : (
        <div className="preview-text">
          <div className="preview-text-head">本页原生文本</div>
          {loading ? <p className="muted">正在读取文本…</p> : null}
          {!loading && text != null ? <pre>{text || '（本页无原生文本）'}</pre> : null}
        </div>
      )}
    </div>
  )
}
