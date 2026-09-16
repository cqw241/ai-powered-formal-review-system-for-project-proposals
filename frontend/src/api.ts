import type { ApiErrorBody, Material, MaterialCategory, PageText, Project } from './types'

async function parseError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as ApiErrorBody
    if (typeof body.detail === 'string') {
      return body.detail
    }
    if (Array.isArray(body.detail) && body.detail.length > 0) {
      const first = body.detail[0]
      if (first?.msg) {
        return first.msg
      }
    }
  } catch {
    // fall through
  }
  return `请求失败（HTTP ${response.status}）`
}

export async function listProjects(): Promise<Project[]> {
  const response = await fetch('/api/projects')
  if (!response.ok) {
    throw new Error(await parseError(response))
  }
  return response.json() as Promise<Project[]>
}

export async function createProject(name: string): Promise<Project> {
  const response = await fetch('/api/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  if (!response.ok) {
    throw new Error(await parseError(response))
  }
  return response.json() as Promise<Project>
}

export async function getProject(id: string): Promise<Project> {
  const response = await fetch(`/api/projects/${encodeURIComponent(id)}`)
  if (!response.ok) {
    throw new Error(await parseError(response))
  }
  return response.json() as Promise<Project>
}

export async function listMaterials(projectId: string): Promise<Material[]> {
  const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/materials`)
  if (!response.ok) {
    throw new Error(await parseError(response))
  }
  return response.json() as Promise<Material[]>
}

export async function uploadMaterial(
  projectId: string,
  file: File,
  category: MaterialCategory,
): Promise<Material> {
  const form = new FormData()
  form.append('category', category)
  form.append('file', file)
  const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/materials`, {
    method: 'POST',
    body: form,
  })
  if (!response.ok) {
    throw new Error(await parseError(response))
  }
  return response.json() as Promise<Material>
}

export async function getPageText(materialId: string, pageNumber: number): Promise<PageText> {
  const response = await fetch(
    `/api/materials/${encodeURIComponent(materialId)}/pages/${pageNumber}/text`,
  )
  if (!response.ok) {
    throw new Error(await parseError(response))
  }
  return response.json() as Promise<PageText>
}

export function pageImageUrl(materialId: string, pageNumber: number): string {
  return `/api/materials/${encodeURIComponent(materialId)}/pages/${pageNumber}/image`
}
