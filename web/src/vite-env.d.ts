/// <reference types="vite/client" />
declare const __TILES_URL__: string;

interface ImportMetaEnv {
  readonly VITE_FEATURES_OFF?: string;
  readonly VITE_FEATURES_ON?: string;
  /** Base URL of the feedback worker (comments feature); not set in this project yet. */
  readonly VITE_FEEDBACK_API?: string;
  /** Turnstile site key for the comments card. */
  readonly VITE_TURNSTILE_SITEKEY?: string;
  /** Sentry DSN for error reports (public by design); unset = no reports. */
  readonly VITE_SENTRY_DSN?: string;
  readonly VITE_SENTRY_ENV?: string;
  /** Cloudflare Web Analytics token and Google Analytics ID; unset = no statistics. */
  readonly VITE_CF_BEACON?: string;
  readonly VITE_GA_ID?: string;
}
