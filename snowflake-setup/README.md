# Snowflake trial account setup

Snowflake trials last 30 days. When one ends, these two files rebuild what the dbt profile
target `snowflake` expects on a fresh trial account:

| Object | Name |
|---|---|
| Warehouse | `COMPUTE_WH` (the trial's own; set to XSMALL, auto-suspend after 60 s) |
| Database / schema | `S_LAYER` / `S_LAYER.DBT` (the schema is owned by the role) |
| Role | `DBT_S_LAYER`: can create schemas in `S_LAYER`, cannot create databases, reads `SNOWFLAKE_SAMPLE_DATA` |
| User | `ANENTROPIC`: service user, key-pair login only, no password |

`setup.sql` is idempotent, so it is safe to run again (for example to register a new key).
Only the public key goes into it. Neither file reads or writes `~/.dbt/profiles.yml`.

## Steps

1. Sign up for a new trial. Any sign-in works, Google included, but don't use `ANENTROPIC`
   as the signup login: the script turns that user into a key-only service user.
2. Print the SQL with your public key filled in, and copy it:

   ```bash
   PUBKEY_FILE=~/.ssh/snowflake_rsa_key.pub bash snowflake-setup/setup.sh --worksheet | pbcopy
   ```

3. In Snowsight, open a new SQL worksheet, paste, and choose **Run All**. The last result
   (`DESC USER ANENTROPIC`) should show a value for `RSA_PUBLIC_KEY_FP`.
4. In `~/.dbt/profiles.yml`, set `account:` in the `snowflake` target to the new account
   identifier (`<orgname>-<accountname>`, as in the account URL).
5. Check the connection:

   ```bash
   dbt debug --profile default --target snowflake
   ```

A JWT token error at step 5 means the public key does not match the private key in your
profile. Make a new key pair, update the profile, and run steps 2 and 3 again.

## Using the Snowflake CLI instead of a worksheet

If the admin user signs in with a password, `setup.sh` can run the SQL through the
[Snowflake CLI](https://docs.snowflake.com/en/developer-guide/snowflake-cli/index) (`snow`):

```bash
SNOWFLAKE_ACCOUNT=<orgname-accountname> SNOWFLAKE_ADMIN_USER=<signup login> \
  PUBKEY_FILE=~/.ssh/snowflake_rsa_key.pub bash snowflake-setup/setup.sh
```

It prompts for the password and passes it to `snow` in an environment variable, never as an
argument. If the admin user has MFA, add `SNOWFLAKE_ADMIN_AUTH=username_password_mfa`. A
Google sign-in has no password, so use the worksheet steps above.
