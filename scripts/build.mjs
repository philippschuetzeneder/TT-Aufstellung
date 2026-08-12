import { access, mkdir, copyFile, rm } from 'node:fs/promises';

const required = [
  'public/index.html',
  'src/ui/app.mjs',
  'src/ui/styles.css',
  'src/engine/predictionEngine.mjs',
  'src/data/demoData.mjs',
  'src/data/DemoDataProvider.mjs'
];

await rm('dist', { recursive: true, force: true });
for (const file of required) await access(file);

// Build a self-contained static-site directory suitable for Render Static Site.
// The published root is dist/, so index.html and /src/... must live at the same level.
await mkdir('dist/src/ui', { recursive: true });
await mkdir('dist/src/engine', { recursive: true });
await mkdir('dist/src/data', { recursive: true });

await copyFile('public/index.html', 'dist/index.html');
await copyFile('src/ui/app.mjs', 'dist/src/ui/app.mjs');
await copyFile('src/ui/styles.css', 'dist/src/ui/styles.css');
await copyFile('src/engine/predictionEngine.mjs', 'dist/src/engine/predictionEngine.mjs');
await copyFile('src/data/demoData.mjs', 'dist/src/data/demoData.mjs');
await copyFile('src/data/DemoDataProvider.mjs', 'dist/src/data/DemoDataProvider.mjs');

console.log('Build OK: static MVP written to dist/.');
