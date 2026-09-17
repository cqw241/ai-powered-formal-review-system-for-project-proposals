import { useMemo, useState } from 'react'
import PagePreview from './PagePreview'
import {
  MATERIAL_CATEGORY_LABEL,
  type CompareSide,
  type EvidenceBBox,
  type EvidenceCompareView,
  type LabeledFundingField,
  type ReviewItem,
} from './types'

const FIELD_KIND_LABEL: Record<string, string> = {
  application_funding: '申请经费',
  total_funding: '项目总经费',
  matching_funding: '配套经费',
}

type EvidenceTarget = {
  materialId: string
  pageNumber: number
  bbox: EvidenceBBox | null
}

type Props = {
  item: ReviewItem
  onOpenEvidence?: (target: EvidenceTarget) => void
}

function formatNormalized(value: string | number | boolean | null | undefined): string {
  if (value == null || value === '') {
    return '—'
  }
  if (typeof value === 'number') {
    return Number.isInteger(value) ? value.toLocaleString('zh-CN') : String(value)
  }
  return String(value)
}

function sideTitle(side: CompareSide, index: number): string {
  const category = side.category
    ? MATERIAL_CATEGORY_LABEL[side.category as keyof typeof MATERIAL_CATEGORY_LABEL] || side.category
    : ''
  if (category && side.field_name) {
    return `${category} · ${side.field_name}`
  }
  return side.field_name || side.original_filename || `来源 ${index + 1}`
}

function openableSides(compare: EvidenceCompareView): CompareSide[] {
  return compare.sides.filter((side) => side.openable && side.material_id && side.page_number)
}

function kindClass(kind: string | null | undefined): string {
  if (kind === 'total_funding') {
    return 'funding-kind total'
  }
  if (kind === 'application_funding') {
    return 'funding-kind application'
  }
  return 'funding-kind'
}

export default function EvidenceCompare({ item, onOpenEvidence }: Props) {
  const compare = item.compare
  const previewable = useMemo(() => (compare ? openableSides(compare) : []), [compare])
  const [focused, setFocused] = useState<[number, number]>([0, 1])

  if (!compare || compare.sides.length === 0) {
    return null
  }

  const left = previewable[focused[0]] ?? previewable[0] ?? null
  const right = previewable.length > 1 ? previewable[focused[1]] ?? previewable[1] ?? null : null
  const panes = [left, right].filter((side, index, list): side is CompareSide => {
    if (!side) {
      return false
    }
    return list.findIndex((itemSide) => itemSide === side) === index
  })

  function openSide(side: CompareSide) {
    if (!side.material_id || !side.page_number || !onOpenEvidence) {
      return
    }
    onOpenEvidence({
      materialId: side.material_id,
      pageNumber: side.page_number,
      bbox: side.bbox,
    })
  }

  function focusPreview(side: CompareSide) {
    const index = previewable.indexOf(side)
    if (index < 0) {
      openSide(side)
      return
    }
    setFocused(([first, second]) => {
      if (index === first) {
        return [first, second]
      }
      if (previewable.length < 2) {
        return [index, index]
      }
      return [first, index]
    })
    openSide(side)
  }

  return (
    <article className="compare-panel" data-testid={`evidence-compare-${item.rule_code}`}>
      <header className="compare-head">
        <div>
          <p className="eyebrow">{item.rule_code} · 问题详情</p>
          <h4>{item.name}</h4>
        </div>
        <span className="muted">核对字段：{compare.check_field}</span>
      </header>

      <dl className="issue-grid">
        <div>
          <dt>字段名</dt>
          <dd>{compare.check_field}</dd>
        </div>
        <div>
          <dt>差异</dt>
          <dd data-testid={`compare-difference-${item.rule_code}`}>
            {compare.difference || (compare.difference_yuan != null ? `${compare.difference_yuan} 元` : '无（一致或待确认）')}
          </dd>
        </div>
      </dl>

      <div className="issue-sides compare-sides">
        {compare.sides.map((side, index) => {
          const clickable = side.openable
          return (
            <div key={`${item.id}-side-${index}`} className="issue-side" data-testid={`compare-side-${index}`}>
              <h5>{sideTitle(side, index)}</h5>
              <p className="issue-side-meta">
                {side.original_filename ?? '未提取'}
                {side.page_number ? ` · 第 ${side.page_number} 页` : ''}
              </p>
              <dl>
                <div>
                  <dt>字段名</dt>
                  <dd>
                    {side.field_name}
                    {side.field_kind && FIELD_KIND_LABEL[side.field_kind] ? (
                      <span className={kindClass(side.field_kind)}>{FIELD_KIND_LABEL[side.field_kind]}</span>
                    ) : null}
                  </dd>
                </div>
                <div>
                  <dt>原值</dt>
                  <dd>
                    {clickable ? (
                      <button
                        type="button"
                        className="evidence-link"
                        onClick={() => focusPreview(side)}
                        title="打开对应文件与页码并高亮"
                      >
                        {side.raw_value || side.quote || '无法读取'}
                      </button>
                    ) : (
                      <span>{side.raw_value || side.quote || side.reason || '无法读取'}</span>
                    )}
                  </dd>
                </div>
                <div>
                  <dt>单位</dt>
                  <dd>{side.unit || '—'}</dd>
                </div>
                <div>
                  <dt>规范化值</dt>
                  <dd>{formatNormalized(side.normalized_value)}</dd>
                </div>
              </dl>
            </div>
          )
        })}
      </div>

      {compare.funding_fields.length > 0 ? (
        <div className="funding-field-list" data-testid="funding-field-labels">
          <p className="muted">金额字段（申请经费与总经费分别标明）</p>
          <ul>
            {compare.funding_fields.map((field: LabeledFundingField, index) => (
              <li key={`${field.field_name}-${field.raw_value}-${index}`}>
                <span className={kindClass(field.field_kind)}>{FIELD_KIND_LABEL[field.field_kind] || field.field_name}</span>
                <strong>{field.field_name}</strong>
                <span>
                  {field.raw_value || '—'}
                  {field.unit ? ` · ${field.unit}` : ''}
                </span>
                {field.original_filename ? <span className="muted">{field.original_filename}</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {panes.length > 0 ? (
        <div className={`compare-pages count-${panes.length}`} data-testid="compare-pages">
          {panes.map((side, index) => (
            <PagePreview
              key={`${side.material_id}-${side.page_number}-${side.field_name}-${index}`}
              materialId={side.material_id as string}
              filename={side.original_filename || side.field_name}
              pageNumber={side.page_number as number}
              bbox={side.bbox}
              compact
              testId={`compare-page-${index}`}
            />
          ))}
        </div>
      ) : null}
    </article>
  )
}
