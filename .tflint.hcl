plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

# Root modules own the Terraform/provider version constraints.
rule "terraform_required_version" {
  enabled = false
}

rule "terraform_required_providers" {
  enabled = false
}
