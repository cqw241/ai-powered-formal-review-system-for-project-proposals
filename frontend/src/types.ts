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
