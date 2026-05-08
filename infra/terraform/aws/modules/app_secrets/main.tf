# Secrets Manager secret for application runtime secrets.
# The initial secret value sets auth_token to a placeholder; update it
# out-of-band before enabling services in the environment.

resource "aws_secretsmanager_secret" "app" {
  name                    = "app/${var.naming_suffix}"
  description             = "Runtime secrets for the RAG application (${var.naming_suffix})."
  recovery_window_in_days = var.recovery_window_days
  tags                    = var.tags
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    auth_token           = var.initial_auth_token
    confluence_api_token = var.initial_confluence_api_token
  })

  lifecycle {
    # Prevent Terraform from overwriting a secret that has been set externally.
    ignore_changes = [secret_string]
  }
}
