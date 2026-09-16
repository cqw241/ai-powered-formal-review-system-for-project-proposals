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
  status: ReviewStatus
  reason: string
  difference_yuan: number | null
  left: FundingSide | null
  right: FundingSide | null
  compared_at: string | null
}

export type BoundRule = {
  rule_id: string
  version_id: string
  version_number: number
  rule_code: string
  name: string
  category: string | null
  compare_field: string
  comparator: string | null
  amount_yuan: number | null
  amount_raw: string | null
  source_clause: string | null
  source_page: number | null
  source_quote: string | null
  policy_id: string | null
  application_amount_yuan: number | null
  display_note: string | null
}

export type FundingReview = {
  id: string
  project_id: string
  rule_id: string
  status: ReviewStatus
  created_at: string
  finding: FundingFinding
  bound_rules?: BoundRule[]
}

export type RuleVersion = {
  id: string
  rule_id: string
  version_number: number
  name: string
  category: string | null
  compare_field: string
  comparator: string | null
  amount_yuan: number | null
  amount_raw: string | null
  source_clause: string | null
  source_page: number | null
  source_quote: string | null
  policy_id: string | null
  created_at: string
}

export type Rule = {
  id: string
  rule_code: string
  name: string
  enabled: boolean
  source_candidate_id: string | null
  policy_id: string | null
  current_version_number: number
  created_at: string
  versions: RuleVersion[]
  current_version: RuleVersion | null
}

export type RuleUpdate = {
  name?: string | null
  category?: string | null
  comparator?: string | null
  amount_raw?: string | null
  source_clause?: string | null
  source_page?: number | null
  source_quote?: string | null
}

export type PolicyStatus = 'PROCESSING' | 'READY' | 'FAILED'

export type PolicyCandidate = {
  id: string
  policy_id: string
  kind: string
  title: string
  category: string | null
  amount_raw: string | null
  amount_yuan: number | null
  amount_unit: string | null
  comparator: string | null
  source_clause: string | null
  source_page: number | null
  source_quote: string | null
  sort_order: number
  created_at: string
  updated_at: string
  rule?: Rule | null
}

export type PolicyCandidateUpdate = {
  title?: string | null
  category?: string | null
  amount_raw?: string | null
  comparator?: string | null
  source_clause?: string | null
  source_page?: number | null
  source_quote?: string | null
}

export type PolicySummary = {
  id: string
  original_filename: string
  title: string | null
  page_count: number | null
  status: PolicyStatus
  error_summary: string | null
  created_at: string
  candidate_count: number
}

export type PolicyDetail = {
  id: string
  original_filename: string
  title: string | null
  page_count: number | null
  status: PolicyStatus
  error_summary: string | null
  created_at: string
  candidates: PolicyCandidate[]
}

export type PolicyPageText = {
  policy_id: string
  page_number: number
  page_count: number
  text: string
}
