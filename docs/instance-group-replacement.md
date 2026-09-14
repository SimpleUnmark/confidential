# Single-VM release replacement

The September 14 rollout replaced the VM under the same name. GCP removed the
deleted VM from its unmanaged instance group, but the name-based self link was
identical after replacement. The provider made no membership update. A second
refresh/apply restored membership. The empty group caused HTTP 503 responses.

VM names now include the `terraform_data.workload_image` generation ID. Image
or secret-version changes replace that generation, giving the new VM a different
self link and an explicit instance-group membership diff. The group, backend,
public IP, DNS, certificate and service account keep their existing identities.
No scripts, imperative gcloud writes, extra servers or permissions are added.

## Adopting this fix

The operator reviews and applies the runtime Terraform plan after PR approval.
Expect one VM replacement (name change), one in-place group membership update,
and a new `workload_instance_name` output. The approved image stays unchanged.
There is still a brief outage: this is not a zero-downtime or multi-VM design.
Use the new output for VM diagnostics rather than hardcoding the old name.

After apply, verify the backend is HEALTHY, health/info return 200, Confidential
cleaning succeeds, and a fresh plan reports no changes. Mock tests cover distinct
generation names; the real GCP replacement/membership behavior must be checked
by this operator-run apply.

This fix covers image/secret release rotations. For a forced rebuild of the same
release, replace `terraform_data.workload_image` as well, rather than replacing
only the VM; otherwise its name can be reused. Other infrastructure changes
that force VM replacement require reviewing that same generation requirement.
Do not revoke the previous image until rollback is no longer needed.
