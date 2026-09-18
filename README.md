# bq-query-runner

Run SQL scripts on **Google BigQuery** from files, with placeholders, text replacements, dry-run, resume-on-error and optional export of the results.

**bq-query-runner** is a command line tool that reads `.sql` files (single file or folder\subfolders), splits them into individual statements and runs them on BigQuery. Arguments can be given on the command line **or** in a JSON config file.

## Features

- **Run SQL from a file or a folder**
  Point it at a single `.sql` file or a directory; every `.sql` file is picked up and executed in a deterministic order. Each file may contain multiple statements (split with `sqlparse`).
- **Command line OR config file**
  The two sources are mutually exclusive (no merge): with `--config_path` every parameter comes from the JSON file, otherwise from the command line.
- **`${...}` placeholder substitution**
  Point `--placeholder_path` at a JSON file of `name: value` pairs; every `${name}` in the SQL is replaced. Providing the file is what turns substitution on. Referenced-but-undefined placeholders stop the run.
- **Plain text replacements**
  `--replace orig:changed,orig2:changed2` applies literal string substitutions.
- **Dry run**
  `--dry_run` validates the queries and reports the estimated processed bytes without executing them.
- **Resume on error**
  On failure the tool records progress; rerun with `--resume` to skip already-executed statements.
- **Export results**
  `--output_format parquet|csv|json` saves the result of each statement to a file.
- **Flexible authentication**
  Uses Application Default Credentials, `GOOGLE_APPLICATION_CREDENTIALS`, or an explicit service account file via `--sa_json_key_path`.

The `log` and `output` folders are created automatically if they do not exist.

### Options

| Option | Type | Description |
| --- | --- | --- |
| `PROJECT` | string | Target Google Cloud project (required unless given in the config file). |
| `--config_path` | string | JSON config file; if set, all parameters come from it (CLI args ignored). |
| `--sql_path` | string | SQL file or directory to run. |
| `--dry_run` | flag | Dry run (validate and estimate bytes, do not execute). |
| `--resume` | flag | Resume from previous error. |
| `--replace` | string | Literal replacements `orig:changed,orig2:changed2`. |
| `--placeholder_path` | string | Placeholder JSON file; providing it enables `${...}` substitution. |
| `--sa_json_key_path` | string | Service account JSON key file (default: Application Default Credentials). |
| `--output_format` | string | Export results as `parquet`, `csv` or `json`. |
| `--output_path` | string | Export destination for results. |
| `--log_path` | string | Log files destination. |

> **`flag` vs `string`.** A `flag` is a command-line switch: just add it to turn the
> feature **on** — it takes **no value** (e.g. `--dry_run`, not `--dry_run true`).
> A `string` option expects a value after it. In the [config file](#config-file) the
> same `flag` options become plain booleans (`"dry_run": true`).

## Config file

With `--config_path` **all** parameters are read from the JSON file. Keys match the
command-line argument names. A complete example:

```json
{
    "PROJECT": "gc-testproject-svil",
    "sql_path": "C:/mypath/sql",
    "dry_run": false,
    "replace": null,
    "placeholder_path": "C:/mypath/placeholder/placeholder.json",
    "sa_json_key_path": "C:/mypath/sa_json_key/service-account.json",
    "log_path": "C:/mypath/log",
    "output_path": "C:/mypath/output",
    "output_format": "csv",
    "resume": false
}
```

> Replace `C:/mypath` with your actual path.

This example is also shipped as [`config/config.json.sample`](config/config.json.sample).

| Key | Type | Meaning |
| --- | --- | --- |
| `PROJECT` | string | Target Google Cloud project (**required**). |
| `sql_path` | string | SQL file or folder to run. |
| `dry_run` | bool | `true` to validate without executing. |
| `replace` | string | Literal `orig:changed,...` substitutions. |
| `placeholder_path` | string | Placeholder JSON file; set it to turn on `${...}` substitution. |
| `sa_json_key_path` | string | Service account JSON key file. |
| `log_path` | string | Where log files are written. |
| `output_path` | string | Where exported results are written. |
| `output_format` | string | `parquet`, `csv` or `json` (omit to skip export). |
| `resume` | bool | `true` to skip statements already run successfully. |

## Placeholders

Placeholders let you keep a reusable SQL template and inject values at run time.
Provide a JSON file with `--placeholder_path` (or `"placeholder_path": "..."` in the
config file) — supplying the file is what turns substitution on
(a ready-made one is shipped as [`placeholder/placeholder.json.sample`](placeholder/placeholder.json.sample)).

`C:/mypath/placeholder/placeholder.json`:

```json
{
    "project_id": "my-gcp-project",
    "dataset": "my_dataset",
    "table_prefix": "my_table_",
    "start_date": "2025-01-01",
    "end_date": "2025-02-25",
    "max_results": 1000,
    "filter_status": "completed",
    "region": "US"
}
```

`C:/mypath/sql/queries.sql` (shipped as [`sql/queries.sql.sample`](sql/queries.sql.sample)) — a single `.sql` file can hold several statements, split on `;`:

```sql
-- Statement 1: filtered extract
SELECT *
FROM `${project_id}.${dataset}.${table_prefix}orders`
WHERE order_date BETWEEN '${start_date}' AND '${end_date}'
  AND status = '${filter_status}'
LIMIT ${max_results};

-- Statement 2: simple aggregation
SELECT COUNT(*) AS orders_count
FROM `${project_id}.${dataset}.${table_prefix}orders`
WHERE status = '${filter_status}';
```

Run:

```sh
bq-query-runner my-project \
    --sql_path "C:/mypath/sql" \
    --placeholder_path "C:/mypath/placeholder/placeholder.json"
```

The executed statements become:

```sql
-- Statement 1: filtered extract
SELECT *
FROM `my-gcp-project.my_dataset.my_table_orders`
WHERE order_date BETWEEN '2025-01-01' AND '2025-02-25'
  AND status = 'completed'
LIMIT 1000;

-- Statement 2: simple aggregation
SELECT COUNT(*) AS orders_count
FROM `my-gcp-project.my_dataset.my_table_orders`
WHERE status = 'completed';
```

Placeholders defined but not referenced (like `region` above) are simply unused;
only a `${name}` referenced in the SQL but missing from the file stops the run,
with a clear error listing the missing names.

> Placeholders are textual substitution (not parameterized queries), so use them
> with trusted templates and values.

## Text replacements

Replacements are simple literal find-and-replace rules applied to the SQL text.
They are handy for quick, one-off edits that do not deserve a placeholder file —
for example switching an environment prefix or bumping a `LIMIT` before a run.

Pass them with `--replace` (or `"replace": "..."` in the config file) as a
comma-separated list of `original:changed` couples:

```sh
bq-query-runner my-project \
    --sql_path "C:/mypath/sql" \
    --replace "dev_:prod_,LIMIT 1000:LIMIT 100000"
```

Given this SQL:

```sql
SELECT *
FROM `my-gcp-project.dev_sales.dev_orders`
WHERE status = 'completed'
LIMIT 1000;
```

the executed statement becomes:

```sql
SELECT *
FROM `my-gcp-project.prod_sales.prod_orders`
WHERE status = 'completed'
LIMIT 100000;
```

Each couple must contain a single `:` separating the original text from its
replacement; a malformed couple (missing `:`) stops the run with a clear error.
Replacements are applied **after** placeholder substitution, so a `${...}` value
injected earlier can itself be rewritten by a replacement.

> Like placeholders, replacements are plain textual substitution: every literal
> match anywhere in the file is changed, so keep the search strings specific.

## Sample files

Ready-to-copy samples are shipped inside the repo. The `.sample` suffix keeps
them from being picked up as real input — **copy each one and drop the suffix**:

| Sample | Copy to |
| --- | --- |
| `config/config.json.sample` | `config/config.json` |
| `placeholder/placeholder.json.sample` | `placeholder/placeholder.json` |
| `sql/queries.sql.sample` | `sql/<your-query>.sql` |

## Requirements

- **Python 3.10+**
- Python packages — installed automatically by `pip install .`:
  - `google-cloud-bigquery` (≥ 3.0) — BigQuery client
  - `pandas` (≥ 2.0) — result handling and export
  - `pyarrow` (≥ 14.0) — Parquet export
  - `sqlparse` (≥ 0.4) — split each file into individual statements
  - `db-dtypes` (≥ 1.2) — BigQuery ↔ pandas type support
- **BigQuery access** (a Google Cloud project + credentials) — see [Authentication](#authentication).

Exact tested versions are pinned in [`requirements.txt`](requirements.txt).

## Installation

Install the package:

```sh
pip install .
```

Or install in editable mode for development:

```sh
pip install -e .
```

## Authentication

The tool connects through the Google Cloud client library.  
There are three ways to authenticate, listed in order of priority:

**1. Explicit service account file** — `--sa_json_key_path`

```sh
bq-query-runner my-project --sa_json_key_path "C:/mypath/sa_json_key/service-account.json"
```

This is the tool's first-class option: internally it just points
`GOOGLE_APPLICATION_CREDENTIALS` at that file for the run, and it takes priority
over everything below.

**2. `GOOGLE_APPLICATION_CREDENTIALS` environment variable**

`GOOGLE_APPLICATION_CREDENTIALS` is Google's standard variable holding the **path
to a service account key file**. Set it once and every run picks it up without
repeating `--sa_json_key_path`.

**3. User credentials** — gcloud login

```sh
gcloud auth application-default login
```

> When running **inside Google Cloud** (Compute Engine, Cloud Run, Composer, …) the attached service account can be used, so none of the above is needed.
> If no credentials can be found, the run stops at the credentials check with a clear error.

## Usage

Run using a config file:

```sh
bq-query-runner --config_path "C:/mypath/config/config.json"
```

Or run with the console entry point:

```sh
bq-query-runner my-project --sql_path "C:/mypath/sql"
```

> Replace `C:/mypath` with your actual path.

Full help:

```sh
bq-query-runner -h
```

## Documentation

Browsable API documentation is available under [`docs/`](docs) (open `docs/index.html`).

## License

This project is licensed under the GNU GPLv3 License. See the LICENSE file for details.
