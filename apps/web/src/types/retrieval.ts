export interface Evidence {
  document_id: string
  version_id: string
  chunk_id: string
  source_sha256: string
  page: number
  section: string | null
  text: string
}

export type RetrievalResult =
  | { status: 'unavailable' | 'abstained'; evidence: [] }
  | { status: 'evidence_found'; evidence: Evidence[] }
