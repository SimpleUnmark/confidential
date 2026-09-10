import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { POLICY_PATH, validatePolicy, validateTransition, verifyArtifact, verificationArgs } from './release-policy.mjs';
// Stable test fixture: routine production rotations must not change test cases.
const fixture = () => ({
  schemaVersion: 1, repository: 'SimpleUnmark/confidential', protocolVersion: 4,
  deploymentDigest: 'sha256:d2470f946ea4856d30d6de9923112ea7da5a6110aedb8e9b526216e9fe9855b9',
  client: { sha256: 'a'.repeat(64), sourceCommit: 'a'.repeat(40), sourceRef: 'refs/heads/main' },
  workloads: [{ digest: 'sha256:d2470f946ea4856d30d6de9923112ea7da5a6110aedb8e9b526216e9fe9855b9',
    sourceCommit: 'a'.repeat(40), sourceRef: 'refs/heads/main', status: 'active',
    approvedAt: '2026-09-10', provenanceImages: ['docker.io/simpleunmark/simpleunmark-confidential'] }],
});

test('checked-in policy is valid', () => validatePolicy(JSON.parse(readFileSync(POLICY_PATH, 'utf8'))));
for (const [name, mutate] of [
  ['unapproved deployment', p => { p.deploymentDigest = `sha256:${'b'.repeat(64)}`; }],
  ['revoked deployment', p => { p.workloads[0].status = 'revoked'; p.workloads[0].reason = 'test'; }],
  ['candidate accepted', p => { p.workloads[0].status = 'candidate'; }],
  ['malformed hash', p => { p.workloads[0].digest = 'latest'; }],
  ['duplicate digest', p => { p.workloads.push(p.workloads[0]); }],
  ['wrong protocol', p => { p.protocolVersion = 5; }],
  ['wrong source', p => { p.workloads[0].sourceRef = 'refs/pull/1/head'; }],
  ['untrusted provenance registry', p => { p.workloads[0].provenanceImages = ['evil.example/image']; }],
  ['new image without GCP provenance', p => { p.deploymentDigest = p.workloads[0].digest = `sha256:${'b'.repeat(64)}`; }],
]) test(`rejects ${name}`, () => {
  const p = fixture(); mutate(p); assert.throws(() => validatePolicy(p));
});
test('historical evidence cannot be rewritten', () => {
  const p = fixture(); p.workloads[0].sourceCommit = 'b'.repeat(40);
  assert.throws(() => validateTransition(fixture(), p));
});
test('revoked images cannot be restored or deleted', () => {
  const previous = fixture();
  previous.workloads.push({ ...previous.workloads[0], digest: `sha256:${'b'.repeat(64)}`,
    provenanceImages: [...previous.workloads[0].provenanceImages,
      'europe-west1-docker.pkg.dev/simple-unmark-prod/workloads/simpleunmark-confidential'],
    status: 'revoked', reason: 'test' });
  const next = structuredClone(previous); next.workloads[1].status = 'active';
  assert.throws(() => validateTransition(previous, next));
  assert.throws(() => validateTransition(previous, fixture()));
});
test('verification pins workflow, signer commit, source commit and hosted runners', () => {
  const args = verificationArgs('test', fixture().workloads[0]);
  for (const flag of ['--signer-workflow', '--signer-digest', '--source-digest', '--source-ref', '--deny-self-hosted-runners']) {
    assert.ok(args.includes(flag));
  }
});
test('rejects missing Rekor inclusion and fails closed on gh errors', () => {
  assert.throws(() => verifyArtifact('test', fixture().workloads[0], () => '[]'));
  assert.throws(() => verifyArtifact('test', fixture().workloads[0], () => { throw Error('offline'); }));
});

test('requires verified transparency and numeric repository identities together', () => {
  const verified = {
    verificationResult: {
      signature: { certificate: { sourceRepositoryIdentifier: '1358635275', sourceRepositoryOwnerIdentifier: '325450359' } },
      verifiedTimestamps: [{ type: 'Tlog', uri: 'https://rekor.sigstore.dev' }],
    },
  };
  const runner = () => JSON.stringify([verified]);
  assert.doesNotThrow(() => verifyArtifact('test', fixture().workloads[0], runner));
  verified.verificationResult.signature.certificate.sourceRepositoryIdentifier = '123';
  assert.throws(() => verifyArtifact('test', fixture().workloads[0], runner));
  verified.verificationResult.signature.certificate.sourceRepositoryIdentifier = '1358635275';
  verified.verificationResult.verifiedTimestamps = [];
  assert.throws(() => verifyArtifact('test', fixture().workloads[0], runner));
});

test('approval verification pins a different signer from image publication', () => {
  const workflow = 'SimpleUnmark/confidential/.github/workflows/approve-release-policy.yml';
  const args = verificationArgs('policy.json', fixture().workloads[0], workflow);
  assert.equal(args[args.indexOf('--signer-workflow') + 1], workflow);
});
