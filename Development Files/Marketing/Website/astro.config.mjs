import { defineConfig } from 'astro/config';

// https://astro.build/config
export default defineConfig({
  // Static site generation — no SSR needed
  output: 'static',
  // Build to dist/
  outDir: './dist',
  // Site URL for canonical links and OG tags (adjust for deployment)
  site: 'https://getmumble.com',
});
