export type Project = {
  id: string
  name: string
  created_at: string
}

export type MaterialCategory = 'APPLICATION' | 'BUDGET' | 'COMMITMENT' | 'OTHER'

export type MaterialStatus = 'PROCESSING' | 'READY' | 'FAILED'

export type Material = {
  id: string
  project_id: string
  original_filename: string
  category: MaterialCategory
  page_count: number | null
  status: MaterialStatus
  error_summary: string | null
  created_at: string
}

export type PageText = {
  material_id: string
  page_number: number
  page_count: number
  text: string
}

export type ApiErrorBody = {
  detail?: string | Array<{ loc?: unknown[]; msg?: string; type?: string }>
}

export type ReviewStatus = 'PASS' | 'FAIL' | 'NEED_HUMAN_REVIEW' | 'SYSTEM_ERROR'

export type EvidenceBBox = {
  x0: number
  y0: number
  x1: number
  y1: number
  page_width: number
  page_height: number
}

export type FundingSide = {
  material_id: string | null
  category: string | null
  original_filename: string | null
  field_kind: string | null
  field_label: string | null
  raw_value: string | null
  raw_unit: string | null
  amount_yuan: number | null
  normalized_amount_yuan: number | null
  normalized_unit: string | null
  display_unit: string | null
  page_number: number | null
  quote: string | null
  bbox: EvidenceBBox | null
  reliable: boolean | null
  reason: string | null
  source: string | null
}

export type FundingFinding = {
  rule_id: string
  check_field: string
  status: ReviewStatus | string
  reason: string
  difference_yuan: number | null
  left: FundingSide | null
  right: FundingSide | null
  compared_at: string | null
}

export type FundingReview = {
  id: string
  project_id: string
  rule_id: string
  status: ReviewStatus | string
  created_at: string
  finding: FundingFinding
}
