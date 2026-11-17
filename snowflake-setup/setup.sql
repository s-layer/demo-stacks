-- Bootstrap a fresh Snowflake trial account for S-LAYER testing.
-- Recreates what the dbt profile target `snowflake` expects:
--   user ANENTROPIC (key-pair only), role DBT_S_LAYER, database S_LAYER,
--   schema DBT, warehouse COMPUTE_WH (trials create it), key-pair auth.
-- Idempotent: safe to re-run. Run as the trial's signup (admin) user with ACCOUNTADMIN, e.g.
--   snow sql -f setup.sql -D "pubkey=<public key body, no BEGIN/END lines>"
-- or paste into a worksheet after replacing <% pubkey %> by hand.
-- Contains no secrets: only the PUBLIC key is passed in.

USE ROLE ACCOUNTADMIN;

-- Trials ship COMPUTE_WH already; this is a no-op there. Keep it small and self-pausing.
CREATE WAREHOUSE IF NOT EXISTS COMPUTE_WH
  WAREHOUSE_SIZE = XSMALL AUTO_SUSPEND = 60 AUTO_RESUME = TRUE INITIALLY_SUSPENDED = TRUE;
ALTER WAREHOUSE COMPUTE_WH SET WAREHOUSE_SIZE = XSMALL AUTO_SUSPEND = 60 AUTO_RESUME = TRUE;

CREATE DATABASE IF NOT EXISTS S_LAYER;

-- Role used by dbt, tests and fixtures. Deliberately no CREATE DATABASE.
CREATE ROLE IF NOT EXISTS DBT_S_LAYER;
GRANT ROLE DBT_S_LAYER TO ROLE SYSADMIN;
GRANT USAGE ON WAREHOUSE COMPUTE_WH TO ROLE DBT_S_LAYER;
GRANT USAGE, CREATE SCHEMA ON DATABASE S_LAYER TO ROLE DBT_S_LAYER;
-- TPC-H sample data used by demo-stacks (trials include this share).
GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE_SAMPLE_DATA TO ROLE DBT_S_LAYER;

-- Default schema, owned by the role so it can create tables, views and semantic views.
CREATE SCHEMA IF NOT EXISTS S_LAYER.DBT;
GRANT OWNERSHIP ON SCHEMA S_LAYER.DBT TO ROLE DBT_S_LAYER COPY CURRENT GRANTS;

-- The dbt user: no password, logs in with the key pair only.
CREATE USER IF NOT EXISTS ANENTROPIC
  TYPE = SERVICE
  DEFAULT_ROLE = DBT_S_LAYER
  DEFAULT_WAREHOUSE = COMPUTE_WH
  DEFAULT_NAMESPACE = S_LAYER.DBT;
-- CREATE ... IF NOT EXISTS skips all of the above for a user that already exists; reapply.
ALTER USER ANENTROPIC SET
  TYPE = SERVICE
  DEFAULT_ROLE = DBT_S_LAYER
  DEFAULT_WAREHOUSE = COMPUTE_WH
  DEFAULT_NAMESPACE = S_LAYER.DBT;
ALTER USER ANENTROPIC SET RSA_PUBLIC_KEY = '<% pubkey %>';
GRANT ROLE DBT_S_LAYER TO USER ANENTROPIC;

-- Check: RSA_PUBLIC_KEY_FP should be set.
DESC USER ANENTROPIC;
