import type {
  ApiErrorBody,
  FundingReview,
  Material,
  MaterialCategory,
  PageText,
  PolicyCandidate,
  PolicyCandidateUpdate,
  PolicyDetail,
  PolicyPageText,
  PolicySummary,
  Project,
  Rule,
  RuleUpdate,
} from './types'

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

export async function runFundingReview(projectId: string): Promise<FundingReview> {
  const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/funding-review`, {
    method: 'POST',
  })
  if (!response.ok) {
    throw new Error(await parseError(response))
  }
  return response.json() as Promise<FundingReview>
}

export async function getFundingReview(projectId: string): Promise<FundingReview | null> {
  const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/funding-review`)
  if (response.status === 404) {
    return null
  }
  if (!response.ok) {
    throw new Error(await parseError(response))
  }
  return response.json() as Promise<FundingReview>
}

export async function listFundingReviews(projectId: string): Promise<FundingReview[]> {
  const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/funding-reviews`)
  if (!response.ok) {
    throw new Error(await parseError(response))
  }
  return response.json() as Promise<FundingReview[]>
}

export async function getFundingReviewById(
  projectId: string,
  reviewId: string,
): Promise<FundingReview> {
  const response = await fetch(
    `/api/projects/${encodeURIComponent(projectId)}/funding-reviews/${encodeURIComponent(reviewId)}`,
  )
  if (!response.ok) {
    throw new Error(await parseError(response))
  }
  return response.json() as Promise<FundingReview>
}

export async function listPolicies(): Promise<PolicySummary[]> {
  const response = await fetch('/api/policies')
  if (!response.ok) {
    throw new Error(await parseError(response))
  }
  return response.json() as Promise<PolicySummary[]>
}

export async function uploadPolicy(file: File): Promise<PolicyDetail> {
  const form = new FormData()
  form.append('file', file)
  const response = await fetch('/api/policies', {
    method: 'POST',
    body: form,
  })
  if (!response.ok) {
    throw new Error(await parseError(response))
  }
  return response.json() as Promise<PolicyDetail>
}

export async function getPolicy(policyId: string): Promise<PolicyDetail> {
  const response = await fetch(`/api/policies/${encodeURIComponent(policyId)}`)
  if (!response.ok) {
    throw new Error(await parseError(response))
  }
  return response.json() as Promise<PolicyDetail>
}

export async function getPolicyPageText(policyId: string, pageNumber: number): Promise<PolicyPageText> {
  const response = await fetch(
    `/api/policies/${encodeURIComponent(policyId)}/pages/${pageNumber}/text`,
  )
  if (!response.ok) {
    throw new Error(await parseError(response))
  }
  return response.json() as Promise<PolicyPageText>
}

export function policyPageImageUrl(policyId: string, pageNumber: number): string {
  return `/api/policies/${encodeURIComponent(policyId)}/pages/${pageNumber}/image`
}

export async function updatePolicyCandidate(
  policyId: string,
  candidateId: string,
  patch: PolicyCandidateUpdate,
): Promise<PolicyCandidate> {
  const response = await fetch(
    `/api/policies/${encodeURIComponent(policyId)}/candidates/${encodeURIComponent(candidateId)}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    },
  )
  if (!response.ok) {
    throw new Error(await parseError(response))
  }
  return response.json() as Promise<PolicyCandidate>
}

export async function enableCandidateRule(policyId: string, candidateId: string): Promise<Rule> {
  const response = await fetch(
    `/api/policies/${encodeURIComponent(policyId)}/candidates/${encodeURIComponent(candidateId)}/enable`,
    { method: 'POST' },
  )
  if (!response.ok) {
    throw new Error(await parseError(response))
  }
  return response.json() as Promise<Rule>
}

export async function disableRule(ruleId: string): Promise<Rule> {
  const response = await fetch(`/api/rules/${encodeURIComponent(ruleId)}/disable`, { method: 'POST' })
  if (!response.ok) {
    throw new Error(await parseError(response))
  }
  return response.json() as Promise<Rule>
}

export async function enableRule(ruleId: string): Promise<Rule> {
  const response = await fetch(`/api/rules/${encodeURIComponent(ruleId)}/enable`, { method: 'POST' })
  if (!response.ok) {
    throw new Error(await parseError(response))
  }
  return response.json() as Promise<Rule>
}

export async function updateRule(ruleId: string, patch: RuleUpdate): Promise<Rule> {
  const response = await fetch(`/api/rules/${encodeURIComponent(ruleId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!response.ok) {
    throw new Error(await parseError(response))
  }
  return response.json() as Promise<Rule>
}
