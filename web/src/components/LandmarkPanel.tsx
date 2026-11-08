import { DIR, nameIn, t, translate, useLang, type Key, type Lang } from "../i18n";

/** A landmark card, as written by pipeline/build_places.py (web/public/data/landmarks.json). */
export interface Landmark {
  slug: string;
  qid: string;
  kind: string;
  category: string;
  name: { en: string | null; fa: string | null };
  x: number;
  y: number;
  lon: number;
  lat: number;
  description: { en: string | null; fa: string | null };
  wikipedia: Partial<Record<Lang, { title: string; url: string; extract: string }>>;
  note?: string | null;
  sources: { title: string; url: string; licence: string }[];
}

/** The card: both names, the Wikidata description and the Wikipedia summary in the interface language (or the
 * other one if it has none), and the sources with their licences. */
export function LandmarkPanel({ lm, onClose }: { lm: Landmark; onClose: () => void }) {
  const lang = useLang();
  const other: Lang = lang === "fa" ? "en" : "fa";
  const title = nameIn(lm.name, lang);
  const sub = nameIn(lm.name, other);
  const desc = lm.description[lang] ?? lm.description[other];
  const wl: Lang | null = lm.wikipedia[lang] ? lang : lm.wikipedia[other] ? other : null;
  const wiki = wl ? lm.wikipedia[wl] : null;
  return (
    <aside className="landmark glass" role="dialog" aria-label={title}>
      <button type="button" className="ibtn close" aria-label={t("close")} onClick={onClose}>×</button>
      <header>
        <p className="lm-kind">{translate(lang, `cat.${lm.category}` as Key)}</p>
        <h2 dir="auto">{title}</h2>
        {sub && sub !== title && <p className="lm-local" lang={other} dir={DIR[other]}>{sub}</p>}
      </header>
      <div className="lm-body">
        {desc && <p className="lm-desc" dir="auto">{desc}</p>}
        {wiki && wl && (
          <blockquote className="lm-wiki" lang={wl} dir={DIR[wl]}>
            <p>{wiki.extract}</p>
            <footer>
              {t("cardFrom")}: <a href={wiki.url} target="_blank" rel="noopener">{wiki.title}</a> (CC BY-SA 4.0)
            </footer>
          </blockquote>
        )}
        <h3>{t("cardSources")}</h3>
        <ul className="lm-sources">
          {lm.sources.map((s) => (
            <li key={s.url}><a href={s.url} target="_blank" rel="noopener">{s.title}</a> <small>({s.licence})</small></li>
          ))}
        </ul>
      </div>
    </aside>
  );
}
