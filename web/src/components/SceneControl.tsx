import type { SceneState } from "../scene";
import { formatTime, t, useLang } from "../i18n";

interface Props {
  scene: SceneState;
  live: boolean;
  onChange: (s: Partial<SceneState>) => void;
  onLive: () => void;
  sun: [number, number];
}

/** The time-of-day slider and the "Live" button (Tehran's real clock). */
export function SceneControl({ scene, live, onChange, onLive, sun }: Props) {
  const lang = useLang();
  const icon = scene.hour < sun[0] - 0.5 || scene.hour > sun[1] + 0.5 ? "🌙" : Math.abs(scene.hour - (sun[1] - 0.6)) < 1 ? "🌇" : "☀️";
  return (
    <div className="scene glass" role="group" aria-label={t("timeOfDay")}>
      <div className="scene-row">
        <span aria-hidden="true">{icon}</span>
        <input type="range" min={0} max={23.99} step={0.05} value={scene.hour} aria-label={t("timeOfDay")}
          aria-valuetext={formatTime(lang, scene.hour)} onChange={(e) => onChange({ hour: +e.target.value })} dir="ltr" />
        <output className="scene-time">{formatTime(lang, scene.hour)}</output>
        <button type="button" className={live ? "on" : ""} aria-pressed={live} onClick={onLive} title={t("liveTitle")}>{t("live")}</button>
      </div>
    </div>
  );
}
