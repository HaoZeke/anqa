/** fzf-style fuzzy match (ported from groket.ui.fuzzy). */

const SCORE_MATCH = 16;
const BONUS_BOUNDARY = 8;
const BONUS_BOUNDARY_WHITE = 10;
const BONUS_CAMEL = 10;
const BONUS_CONSECUTIVE = 4;
const BONUS_FIRST_MULT = 2;
const PENALTY_GAP_START = -3;
const PENALTY_GAP_EXTEND = -1;
const BOUNDARY = new Set(["/", "-", "_", ".", " ", ",", ";", ":", "\\"]);

function charBonus(prev, curr) {
  if (prev == null) return BONUS_BOUNDARY_WHITE;
  if (BOUNDARY.has(prev)) return prev === " " || prev === "\t" ? BONUS_BOUNDARY_WHITE : BONUS_BOUNDARY;
  if (prev === prev.toLowerCase() && curr === curr.toUpperCase() && /[a-z]/i.test(prev + curr)) {
    return BONUS_CAMEL;
  }
  return 0;
}

/**
 * @param {string} query
 * @param {string} candidate
 * @returns {{ score: number, positions: number[] }}
 */
export function fzfScore(query, candidate) {
  const ql = query.toLowerCase();
  const cl = candidate.toLowerCase();
  const n = cl.length;
  const m = ql.length;
  if (m === 0) return { score: 0, positions: [] };
  if (m > n) return { score: 0, positions: [] };
  const positions = [];
  let j = 0;
  for (let i = 0; i < n; i++) {
    if (j < m && cl[i] === ql[j]) {
      positions.push(i);
      j++;
    }
  }
  if (j < m) return { score: 0, positions: [] };
  let score = 0;
  let consecutive = 0;
  for (let k = 0; k < positions.length; k++) {
    const pos = positions[k];
    const prev = pos > 0 ? candidate[pos - 1] : null;
    const bonus = charBonus(prev, candidate[pos]);
    let charScore = SCORE_MATCH + bonus;
    if (k === 0 && bonus > 0) charScore += bonus * (BONUS_FIRST_MULT - 1);
    if (consecutive > 0) charScore += BONUS_CONSECUTIVE;
    else if (k > 0) {
      const gap = pos - (positions[k - 1] + 1);
      if (gap > 0) charScore += PENALTY_GAP_START + PENALTY_GAP_EXTEND * (gap - 1);
    }
    if (query[k] === candidate[pos]) charScore += 1;
    score += Math.max(0, charScore);
    if (k > 0 && pos === positions[k - 1] + 1) consecutive += 1;
    else consecutive = 0;
  }
  return { score, positions };
}

/**
 * @template T
 * @param {string} query
 * @param {T[]} items
 * @param {(item: T) => string} textFn
 * @returns {T[]}
 */
export function fuzzyFilter(query, items, textFn) {
  const q = (query || "").trim();
  if (!q) return items.slice();
  const scored = [];
  for (const item of items) {
    const text = textFn(item) || "";
    const { score } = fzfScore(q, text);
    if (score > 0) scored.push({ item, score });
  }
  scored.sort((a, b) => b.score - a.score);
  return scored.map((s) => s.item);
}
