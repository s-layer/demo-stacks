#!/usr/bin/env bash
# Bootstrap a fresh Snowflake trial account for S-LAYER testing.
# Usage:
#   Worksheet (works with any sign-in, incl. Google): print the SQL with the key filled in,
#   then paste it into a Snowsight worksheet and Run All:
#     PUBKEY_FILE=<path to rsa_key.pub> bash setup.sh --worksheet | pbcopy
#   CLI (needs a password login for the admin user):
#     SNOWFLAKE_ACCOUNT=<orgname-accountname> SNOWFLAKE_ADMIN_USER=<signup user> \
#       PUBKEY_FILE=<path to rsa_key.pub> bash setup.sh
# CLI mode signs in as the trial signup user (prompts for its password) and runs setup.sql, which creates
# the key-pair-only dbt user ANENTROPIC. Reads only the PUBLIC key; never touches ~/.dbt/profiles.yml.
# CLI mode needs the Snowflake CLI (`snow`). See README.md.
set -euo pipefail
: "${PUBKEY_FILE:?set PUBKEY_FILE to your Snowflake public key (.pub)}"
here="$(cd "$(dirname "$0")" && pwd)"
pubkey="$(grep -v -- '-----' "$PUBKEY_FILE" | tr -d '\n')"

if [[ "${1:-}" == "--worksheet" ]]; then
  sed "s|<% pubkey %>|$pubkey|" "$here/setup.sql"
  exit 0
fi

: "${SNOWFLAKE_ACCOUNT:?set SNOWFLAKE_ACCOUNT to the new trial account identifier}"
SNOWFLAKE_ADMIN_USER="${SNOWFLAKE_ADMIN_USER:?set SNOWFLAKE_ADMIN_USER to the login you signed up with}"

# Password via env var, not argv, so it stays out of `ps`. If the admin user has MFA,
# set SNOWFLAKE_ADMIN_AUTH=username_password_mfa and approve the push.
read -r -s -p "Password for $SNOWFLAKE_ADMIN_USER: " SNOWFLAKE_PASSWORD; echo
export SNOWFLAKE_PASSWORD

snow sql --temporary-connection \
  --account "$SNOWFLAKE_ACCOUNT" --user "$SNOWFLAKE_ADMIN_USER" --role ACCOUNTADMIN \
  --authenticator "${SNOWFLAKE_ADMIN_AUTH:-snowflake}" \
  -D "pubkey=$pubkey" -f "$here/setup.sql"
unset SNOWFLAKE_PASSWORD

echo
echo "Now set account: $SNOWFLAKE_ACCOUNT in the 'snowflake' target of your dbt profile,"
echo "then check with: dbt debug --profile default --target snowflake"
