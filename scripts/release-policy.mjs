import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

export const REPOSITORY = 'SimpleUnmark/confidential';
export const WORKFLOW = `${REPOSITORY}/.github/workflows/release-confidential.yml`;
export const HUB_IMAGE = 'docker.io/simpleunmark/simpleunmark-confidential';
export const GCP_IMAGE = 'europe-west1-docker.pkg.dev/simple-unmark-prod/workloads/simpleunmark-confidential';
const LEGACY_DIGEST = 'sha256:d2470f946ea4856d30d6de9923112ea7da5a6110aedb8e9b526216e9fe9855b9';
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const POLICY_PATH = resolve(ROOT, 'releases/approved-workloads.json');
const sha256 = value => createHash('sha256').update(value).digest('hex');
const sha = /^[a-f0-9]{40}$/;
const digest = /^sha256:[a-f0-9]{64}$/;

export function validatePolicy(policy) {
  assert.equal(policy.schemaVersion, 1, 'Unsupported policy schema');
  assert.equal(policy.repository, REPOSITORY, 'Untrusted source repository');
  assert.equal(policy.protocolVersion, 4, 'Client/workload protocol compatibility must be reviewed');
  assert.match(policy.deploymentDigest, digest);
  assert.match(policy.client.sha256, /^[a-f0-9]{64}$/);
  assert.match(policy.client.sourceCommit, sha);
  assert.equal(policy.client.sourceRef, 'refs/heads/main');
  assert.ok(Array.isArray(policy.workloads) && policy.workloads.length, 'No workloads');
  const seen = new Set();
  for (const release of policy.workloads) {
    assert.match(release.digest, digest);
    assert.ok(!seen.has(release.digest), 'Duplicate digest');
    seen.add(release.digest);
    assert.match(release.sourceCommit, sha);
    assert.equal(release.sourceRef, 'refs/heads/main');
    assert.ok(['active', 'retiring', 'revoked'].includes(release.status), 'Invalid release status');
    assert.match(release.approvedAt, /^\d{4}-\d{2}-\d{2}$/);
    assert.equal(new Date(release.approvedAt).toISOString().slice(0, 10), release.approvedAt);
    const requiredImages = release.digest === LEGACY_DIGEST ? [HUB_IMAGE] : [HUB_IMAGE, GCP_IMAGE];
    assert.deepEqual([...release.provenanceImages].sort(), requiredImages.sort(), 'Both registry attestations required');
    if (release.status === 'revoked') assert.ok(release.reason?.trim(), 'Record the revocation reason');
  }
  assert.ok(policy.workloads.some(r => r.digest === policy.deploymentDigest && r.status === 'active'),
    'Deployment target must be an active approved release');
  return policy;
}

export function validateTransition(previous, next) {
  validatePolicy(previous);
  validatePolicy(next);
  for (const old of previous.workloads) {
    const current = next.workloads.find(r => r.digest === old.digest);
    assert.ok(current, 'Keep historical entries; revoke instead of deleting');
    for (const key of ['sourceCommit', 'sourceRef', 'approvedAt', 'provenanceImages']) {
      assert.deepEqual(current[key], old[key], `Do not rewrite historical ${key}`);
    }
    if (old.status === 'revoked') assert.equal(current.status, 'revoked', 'A revoked image cannot be reapproved');
  }
}

export function verificationArgs(target, source, workflow = WORKFLOW) {
  return ['attestation', 'verify', target, '--repo', REPOSITORY,
    '--signer-workflow', workflow, '--signer-digest', source.sourceCommit,
    '--source-digest', source.sourceCommit, '--source-ref', source.sourceRef,
    '--cert-oidc-issuer', 'https://token.actions.githubusercontent.com',
    '--deny-self-hosted-runners', '--format', 'json'];
}

export function verifyArtifact(target, source, run = execFileSync, workflow = WORKFLOW) {
  const results = JSON.parse(run('gh', verificationArgs(target, source, workflow), {
    encoding: 'utf8', maxBuffer: 16 * 1024 * 1024,
  }));
  assert.ok(results.some(({ verificationResult: result }) =>
    result?.signature?.certificate?.sourceRepositoryIdentifier === '1358635275' &&
    result?.signature?.certificate?.sourceRepositoryOwnerIdentifier === '325450359' &&
    result?.verifiedTimestamps?.some(t => t.type === 'Tlog' && t.uri === 'https://rekor.sigstore.dev')
  ), 'Expected numeric GitHub identities and verified public Rekor inclusion');
}

export function verifyPolicy(policy) {
  validatePolicy(policy);
  // Revoked entries are audit history, never usable. Revocation must remain
  // possible even if their old images or attestations have been deleted.
  for (const release of policy.workloads.filter(r => r.status !== 'revoked')) {
    for (const image of release.provenanceImages) {
      verifyArtifact(`oci://${image}@${release.digest}`, release);
    }
  }
}

function main() {
  const [command = 'validate', argument, approvalCommit] = process.argv.slice(2);
  if (command === 'candidate') {
    const files = readdirSync(resolve(ROOT, 'artifacts')).filter(f => f.endsWith('.tgz'));
    assert.equal(files.length, 1, 'Expected exactly one packaged client');
    const env = process.env;
    assert.match(env.RELEASE_DIGEST, digest);
    assert.match(env.GITHUB_SHA, sha);
    assert.equal(env.GITHUB_REPOSITORY, REPOSITORY);
    assert.equal(env.GITHUB_REF, 'refs/heads/main');
    const candidate = {
      schemaVersion: 1, repository: REPOSITORY, protocolVersion: 4,
      status: 'candidate', digest: env.RELEASE_DIGEST,
      sourceCommit: env.GITHUB_SHA, sourceRef: env.GITHUB_REF,
      provenanceImages: [HUB_IMAGE, GCP_IMAGE],
      run: `https://github.com/${REPOSITORY}/actions/runs/${env.GITHUB_RUN_ID}/attempts/${env.GITHUB_RUN_ATTEMPT}`,
      client: { filename: files[0], sha256: sha256(readFileSync(resolve(ROOT, 'artifacts', files[0]))),
        sourceCommit: env.GITHUB_SHA, sourceRef: env.GITHUB_REF },
    };
    writeFileSync(resolve(ROOT, 'artifacts/release-candidate.json'), JSON.stringify(candidate, null, 2) + '\n');
    return;
  }
  const raw = readFileSync(POLICY_PATH, 'utf8');
  const policy = validatePolicy(JSON.parse(raw));
  if (command === 'transition') {
    assert.ok(argument, 'Provide previous policy file');
    validateTransition(JSON.parse(readFileSync(argument, 'utf8')), policy);
  } else if (command === 'verify') {
    verifyPolicy(policy);
  } else if (command === 'export-web') {
    assert.ok(argument, 'Provide path to website repository');
    const changed = execFileSync('git', ['status', '--porcelain', '--', 'releases/approved-workloads.json'], { cwd: ROOT, encoding: 'utf8' });
    assert.equal(changed.trim(), '', 'Commit and approve the policy before exporting');
    const commit = approvalCommit ?? execFileSync('git', ['log', '-1', '--format=%H', '--', 'releases/approved-workloads.json'], { cwd: ROOT, encoding: 'utf8' }).trim();
    assert.match(commit, sha);
    assert.equal(execFileSync('git', ['show', `${commit}:releases/approved-workloads.json`], { cwd: ROOT, encoding: 'utf8' }), raw,
      'Approval commit must contain this exact policy');
    verifyArtifact(POLICY_PATH, { sourceCommit: commit, sourceRef: 'refs/heads/main' }, execFileSync,
      `${REPOSITORY}/.github/workflows/approve-release-policy.yml`);
    verifyPolicy(policy);
    const website = resolve(argument);
    const packageJson = JSON.parse(readFileSync(resolve(website, 'apps/web/package.json'), 'utf8'));
    const clientPath = resolve(website, 'apps/web', packageJson.dependencies['@simpleunmark/confidential-client'].replace(/^file:/, ''));
    assert.equal(sha256(readFileSync(clientPath)), policy.client.sha256, 'Website client does not match approved package');
    verifyArtifact(clientPath, policy.client);
    writeFileSync(resolve(website, 'apps/web/src/lib/confidential-release-policy.json'), raw);
  } else {
    assert.equal(command, 'validate', 'Use validate, verify, transition, candidate, or export-web');
  }
  console.log(`Release policy ${command}: OK`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
