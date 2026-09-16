import type { EvidenceBBox } from './types'

export type HighlightPercentStyle = {
  left: string
  top: string
  width: string
  height: string
}

/** Page-space bbox → CSS percentages of the displayed page. Independent of zoom. */
export function bboxToPercentStyle(bbox: EvidenceBBox): HighlightPercentStyle | null {
  if (bbox.page_width <= 0 || bbox.page_height <= 0) {
    return null
  }
  return {
    left: `${(bbox.x0 / bbox.page_width) * 100}%`,
    top: `${(bbox.y0 / bbox.page_height) * 100}%`,
    width: `${((bbox.x1 - bbox.x0) / bbox.page_width) * 100}%`,
    height: `${((bbox.y1 - bbox.y0) / bbox.page_height) * 100}%`,
  }
}
