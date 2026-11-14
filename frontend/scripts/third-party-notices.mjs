// Writes dist/THIRD-PARTY-NOTICES.txt and dist/LICENSE.txt after the build.
// The notices list every npm package that ships with the built app (the lockfile's packages not
// marked "dev"), each with its version, declared license and license text from node_modules.
// Usage: node scripts/third-party-notices.mjs [outDir]   (default: dist)
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  writeFileSync,
} from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const outDir = resolve(root, process.argv[2] ?? 'dist');
const product = 'Tabrion Studio';

const lock = JSON.parse(readFileSync(join(root, 'package-lock.json'), 'utf8'));
const shipped = new Map();
for (const [path, meta] of Object.entries(lock.packages ?? {})) {
  if (!path.startsWith('node_modules/') || meta.dev || meta.link) continue;
  const name = path.slice(path.lastIndexOf('node_modules/') + 'node_modules/'.length);
  shipped.set(`${name}@${meta.version}`, { name, version: meta.version, path });
}

const declared = (pkg) =>
  typeof pkg.license === 'string'
    ? pkg.license
    : pkg.license?.type || (pkg.licenses ?? []).map((l) => l.type).join(' OR ') || 'see package';

const out = [
  `${product} – Third-party software notices`,
  '',
  `${product} includes the following open-source components. Each is used under the license shown.`,
  '',
];
const missing = [];
for (const { name, version, path } of [...shipped.values()].sort((a, b) =>
  a.name.localeCompare(b.name),
)) {
  const dir = join(root, path);
  if (!existsSync(join(dir, 'package.json'))) {
    // An optional package for another platform is in the lockfile but not installed here.
    missing.push(`${name}@${version}`);
    continue;
  }
  const pkg = JSON.parse(readFileSync(join(dir, 'package.json'), 'utf8'));
  const file = readdirSync(dir).find((f) => /^(licen[cs]e|copying|notice)(\.|-|$)/i.test(f));
  const home =
    pkg.homepage ||
    (typeof pkg.repository === 'string' ? pkg.repository : pkg.repository?.url) ||
    '';
  out.push('='.repeat(78), `${name} ${version} — ${declared(pkg)}`, home, '');
  out.push(
    file ? readFileSync(join(dir, file), 'utf8').trim() : '(The package ships no license file.)',
    '',
  );
}

mkdirSync(outDir, { recursive: true });
writeFileSync(join(outDir, 'THIRD-PARTY-NOTICES.txt'), `${out.join('\n')}\n`);
copyFileSync(join(root, '..', 'LICENSE'), join(outDir, 'LICENSE.txt'));
console.log(
  `THIRD-PARTY-NOTICES.txt: ${shipped.size - missing.length} packages` +
    (missing.length ? ` (not installed here, skipped: ${missing.join(', ')})` : ''),
);
