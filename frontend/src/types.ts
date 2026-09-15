export type Project = {
  id: string
  name: string
  created_at: string
}

export type ApiErrorBody = {
  detail?: string | Array<{ loc?: unknown[]; msg?: string; type?: string }>
}
