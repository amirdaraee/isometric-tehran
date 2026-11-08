import { existsSync, rmSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { execSync } from "node:child_process";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { sentryVitePlugin } from "@sentry/vite-plugin";

// Work-in-progress pages that may live in public/ for local use only and must never be published.
const LOCAL_ONLY = ["live", "compare", "admin", "poc"];

/** After a build: drop the local-only folders, add .nojekyll for GitHub Pages and, if CNAME is set, the custom
 * domain file. */
function release(): Plugin {
  return {
    name: "release",
    apply: "build",
    closeBundle() {
      const dist = resolve(__dirname, "dist");
      for (const d of LOCAL_ONLY) rmSync(resolve(dist, d), { recursive: true, force: true });
      if (existsSync(dist)) {
        writeFileSync(resolve(dist, ".nojekyll"), "");
        if (process.env.CNAME) writeFileSync(resolve(dist, "CNAME"), `${process.env.CNAME}\n`);
      }
    },
  };
}

// Error reports: only with SENTRY_AUTH_TOKEN set (a secret, never committed) does a release build make hidden
// source maps, upload them to Sentry and delete them. Nothing of the kind is configured yet.
const SENTRY = !!process.env.SENTRY_AUTH_TOKEN;
const RELEASE = (() => { try { return execSync("git rev-parse --short HEAD", { stdio: ["ignore", "pipe", "ignore"] }).toString().trim(); } catch { return ""; } })();
process.env.VITE_RELEASE = RELEASE;

export default defineConfig({
  // "/" locally and on a custom domain; "/isometric-tehran/" for https://amirdaraee.github.io/isometric-tehran/
  // (the Pages workflow sets VITE_BASE). Every public file is loaded through asset() in src/config.ts, so both work.
  base: process.env.VITE_BASE || "/",
  plugins: [react(), release(), ...(SENTRY ? [sentryVitePlugin({
    org: process.env.SENTRY_ORG,
    project: process.env.SENTRY_PROJECT || "isometric-tehran",
    authToken: process.env.SENTRY_AUTH_TOKEN,
    release: { name: RELEASE },
    sourcemaps: { filesToDeleteAfterUpload: ["./dist/**/*.map"] },
    telemetry: false,
  })] : [])],
  build: { sourcemap: SENTRY ? "hidden" : false },
  // city.json lives at the repository root, shared with the pipeline
  server: { port: 3000, open: false, fs: { allow: [".."] } },
  define: {
    // Empty serves tiles from public/tiles. Set TILES_URL at build time to serve them from another host.
    __TILES_URL__: JSON.stringify(process.env.TILES_URL || ""),
  },
});
