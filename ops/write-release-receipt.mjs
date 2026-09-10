#!/usr/bin/env node
// Write a release receipt at the repository root.
//
// Everything that reads "is production current" infers it from Git: which
// revision Forge says it deployed, how many commits followed, whether the
// input trees still hash the same. That is archaeology about a fact nobody
// recorded. The deploy is the only moment that knows what shipped, so it
// writes it down here.
//
// Usage, at any point after a build, from anywhere in the repository:
//   node scripts/write-release-receipt.mjs [artifact-path ...]
//
// Artifact paths are repository-relative. Several builds may run for one
// release (an API bundle, a worker bundle and a web app), so a receipt for
// the same revision is merged rather than replaced; a different revision
// starts a new one. A missing artifact is recorded as absent rather than
// dropped: a receipt that quietly omits what it could not find is the same
// silent pass this exists to remove.
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";

const RECEIPT = ".release-receipt.json";

function repositoryRoot() {
  try {
    return execFileSync("git", ["rev-parse", "--show-toplevel"], {
      encoding: "utf8",
    }).trim();
  } catch {
    return null;
  }
}

function revision(root) {
  for (const key of ["FORGE_DEPLOY_SHA", "FORGE_COMMIT_HASH", "GIT_COMMIT"]) {
    const value = process.env[key]?.trim();
    if (value && /^[0-9a-f]{40}$/u.test(value)) return value;
  }
  try {
    return execFileSync("git", ["rev-parse", "HEAD"], {
      cwd: root,
      encoding: "utf8",
    }).trim();
  } catch {
    return null;
  }
}

function digest(absolute) {
  try {
    const stat = statSync(absolute);
    if (!stat.isFile()) return { present: false, reason: "not-a-file" };
    return {
      present: true,
      sha256: createHash("sha256").update(readFileSync(absolute)).digest("hex"),
      bytes: stat.size,
    };
  } catch {
    return { present: false, reason: "missing" };
  }
}

const root = repositoryRoot();
if (!root) {
  console.error("write-release-receipt: not inside a Git repository");
  process.exit(1);
}
const rev = revision(root);
if (!rev) {
  console.error(
    "write-release-receipt: no revision available; refusing to write a receipt that cannot be compared",
  );
  process.exit(1);
}

const path = join(root, RECEIPT);
let receipt = { schemaVersion: 1, revision: rev, artifacts: {} };
if (existsSync(path)) {
  try {
    const existing = JSON.parse(readFileSync(path, "utf8"));
    // Same release: keep what earlier builds in this deploy recorded.
    if (existing?.revision === rev && existing?.artifacts) {
      receipt = { ...existing, artifacts: { ...existing.artifacts } };
    }
  } catch {
    // A corrupt receipt is replaced, not trusted.
  }
}

for (const argument of process.argv.slice(2)) {
  const absolute = resolve(root, argument);
  const key = relative(root, absolute).split("\\").join("/");
  receipt.artifacts[key] = digest(absolute);
}

receipt.schemaVersion = 1;
receipt.revision = rev;
receipt.builtAt = new Date().toISOString();
receipt.node = process.version;

writeFileSync(path, `${JSON.stringify(receipt, null, 2)}\n`, { mode: 0o644 });
console.log(
  `release receipt ${rev.slice(0, 12)} ${Object.keys(receipt.artifacts).length} artifact(s) -> ${RECEIPT}`,
);
