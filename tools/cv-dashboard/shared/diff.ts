import type { CvLineChange } from "./types.js";
import { splitLines } from "./match.js";

/**
 * Changed lines between two texts (LCS over lines). Adjacent removals/additions are paired
 * into old → new; unpaired ones keep a null side.
 */
export function lineChanges(before: string, after: string): CvLineChange[] {
  const a = splitLines(before).map((l) => l.replace(/\r+$/, ""));
  const b = splitLines(after).map((l) => l.replace(/\r+$/, ""));
  const n = a.length;
  const m = b.length;
  const lcs: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--) lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);

  const out: CvLineChange[] = [];
  let removed: string[] = [];
  let added: string[] = [];
  const flush = () => {
    const k = Math.max(removed.length, added.length);
    for (let x = 0; x < k; x++) out.push({ old: removed[x] ?? null, new: added[x] ?? null });
    removed = [];
    added = [];
  };
  let i = 0;
  let j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) {
      flush();
      i++;
      j++;
    } else if (j < m && (i === n || lcs[i][j + 1] >= lcs[i + 1][j])) added.push(b[j++]);
    else removed.push(a[i++]);
  }
  flush();
  return out;
}
