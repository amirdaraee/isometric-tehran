// Search over places and streets in English and Persian (pure, tested in search.test.ts).
import { fold } from "./i18n/core.ts";

export interface Searchable { n: string; en?: string; fa?: string; alt?: string[]; c: string; f?: 1; q?: 1; s?: string }

export interface Indexed<T extends Searchable> { item: T; key: string; keys: string[] }

/** The folded names of an entry, each searched separately and all together. */
export function index<T extends Searchable>(items: T[]): Indexed<T>[] {
  return items.map((item) => {
    const keys = [item.en, item.fa, item.n, ...(item.alt ?? [])].filter((x): x is string => !!x).map(fold);
    return { item, keys, key: keys.join(" | ") };
  });
}

const RANK: Record<string, number> = { street: 2 };

/** The best matches for a query: every word must appear; landmarks and featured places first, then names that
 * start with the first word, then the rest. */
export function search<T extends Searchable>(idx: Indexed<T>[], query: string, limit = 8): T[] {
  const terms = fold(query).split(" ").filter(Boolean);
  if (!terms.length) return [];
  const hits: [number, T][] = [];
  for (const e of idx) {
    if (!terms.every((t) => e.key.includes(t))) continue;
    const rank = RANK[e.item.c] ?? (e.item.s || e.item.f ? 0 : e.item.q ? 1 : 1.5);
    const starts = e.keys.some((k) => k.startsWith(terms[0]) || k.includes(` ${terms[0]}`)) ? 0 : 5;
    hits.push([rank * 10 + starts, e.item]);
  }
  return hits.sort((a, b) => a[0] - b[0]).slice(0, limit).map((h) => h[1]);
}
