terraform {
  required_version = "= 1.15.8"
  required_providers {
    github = {
      source  = "integrations/github"
      version = "= 6.13.0"
    }
  }
  backend "gcs" {
    bucket = "simple-unmark-prod-tfstate"
    prefix = "confidential/github"
  }
}

# Authenticate with GITHUB_TOKEN in the operator's shell, never a tfvars value.
provider "github" {
  owner = "SimpleUnmark"
}

data "terraform_remote_state" "foundation" {
  backend = "gcs"
  config = {
    bucket = "simple-unmark-prod-tfstate"
    prefix = "confidential/foundation"
  }
}

variable "independent_review_required" {
  description = "Enable after adding a second maintainer. A solo owner cannot approve their own PR."
  type        = bool
  default     = false
}

variable "release_reviewer_ids" {
  description = "Numeric GitHub user IDs allowed to approve publication. Defaults to the existing owner haltakov."
  type        = set(number)
  default     = [300777]
  validation {
    condition     = length(var.release_reviewer_ids) > 0 && length(var.release_reviewer_ids) <= 6
    error_message = "Choose between one and six release reviewers."
  }
}

resource "github_branch_protection" "main" {
  repository_id                   = "confidential"
  pattern                         = "main"
  enforce_admins                  = true
  allows_deletions                = false
  allows_force_pushes             = false
  require_conversation_resolution = true
  required_status_checks {
    strict   = true
    contexts = ["verify", "terraform", "release-policy"]
  }
  required_pull_request_reviews {
    dismiss_stale_reviews           = true
    required_approving_review_count = var.independent_review_required ? 1 : 0
    require_last_push_approval      = var.independent_review_required
  }
}

# Adopt the existing environment without touching its Docker Hub token.
import {
  to = github_repository_environment.production
  id = "confidential:production"
}

resource "github_repository_environment" "production" {
  repository          = "confidential"
  environment         = "production"
  can_admins_bypass   = false
  prevent_self_review = var.independent_review_required
  reviewers {
    users = var.release_reviewer_ids
  }
  deployment_branch_policy {
    protected_branches     = true
    custom_branch_policies = false
  }
  depends_on = [github_branch_protection.main]
}

resource "github_actions_environment_variable" "publishing" {
  for_each      = data.terraform_remote_state.foundation.outputs.github_publishing_variables
  repository    = "confidential"
  environment   = github_repository_environment.production.environment
  variable_name = each.key
  value         = each.value
}

resource "github_repository_environment" "release_approval" {
  repository          = "confidential"
  environment         = "release-approval"
  can_admins_bypass   = false
  prevent_self_review = var.independent_review_required
  reviewers {
    users = var.release_reviewer_ids
  }
  deployment_branch_policy {
    protected_branches     = true
    custom_branch_policies = false
  }
  depends_on = [github_branch_protection.main]
}
