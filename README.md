# bq-query-runner

Run SQL scripts on **Google BigQuery** from files, with placeholders, text replacements, dry-run, resume-on-error and optional export of the results.

**bq-query-runner** is a command line tool that reads `.sql` files (single file or folder\subfolders), splits them into individual statements and runs them on BigQuery. Arguments can be given on the command line **or** in a JSON config file.

## Features

- **Run SQL from a file or a folder**
  Point it at a single `.sql` file or a directory; every `.sql` file is picked up and executed in a deterministic order. Each file may contain multiple statements (split with `sqlparse`).
- **Command line OR config file**
  The two sources are mutually exclusive (no merge): with `--config_path` every parameter comes from the JSON file, otherwise from the command line.
- **`${...}` placeholder substitution**
  Enable with `-p` and provide a JSON file of `name: value` pairs; every `${name}` in the SQL is replaced. Referenced-but-undefined placeholders stop the run.
- **Plain text replacements**
  `--replace orig:changed,orig2:changed2` applies literal string substitutions.
- **Dry run**
  `-d` validates the queries and reports the estimated processed bytes without executing them.
- **Resume on error**
  On failure the tool records progress; rerun with `-r` to skip already-executed statements.
- **Export results**
  `--output_format parquet|csv|json` saves the result of each statement to a file.
- **Flexible authentication**
  Uses Application Default Credentials, `GOOGLE_APPLICATION_CREDENTIALS`, or an explicit service account file via `--json_path`.

The `log` and `output` folders are created automatically if they do not exist.

### Options

| Option | Description |
| --- | --- |
| `PROJECT` | Target Google Cloud project (required unless given in the config file). |
| `--config_path` | JSON config file; if set, all parameters come from it (CLI args ignored). |
| `--sql_path` | SQL file or directory to run. |
| `--dry_run` | Dry run (validate and estimate bytes, do not execute). |
| `--resume` | Resume from previous error. |
| `--replace` | Literal replacements `orig:changed,orig2:changed2`. |
| `-placeholder_enable` | Enable `${...}` placeholder substitution. |
| `--placeholder_path` | Placeholder JSON file. |
| `--json_path` | Service account JSON file (default: Application Default Credentials). |
| `--output_format` | Export results as `parquet`, `csv` or `json`. |
| `--output_path` | Export destination for results. |
| `--log_path` | Log files destination. |

## Config file

With `--config_path` **all** parameters are read from the JSON file. Keys match the
command-line argument names. A complete example:

```json
{
    "PROJECT": "gc-testproject-svil",
    "sql_path": "C:/mypath/sql",
    "dry_run": false,
    "replace": null,
    "placeholder_enable": false,
    "placeholder_path": "C:/mypath/placeholder/placeholder.json",
    "json_path": "C:/mypath/json/service-account.json",
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
| `placeholder_enable` | bool | `true` to turn on `${...}` substitution. |
| `placeholder_path` | string | Placeholder JSON file. |
| `json_path` | string | Service account JSON file. |
| `log_path` | string | Where log files are written. |
| `output_path` | string | Where exported results are written. |
| `output_format` | string | `parquet`, `csv` or `json` (omit to skip export). |
| `resume` | bool | `true` to skip statements already run successfully. |

## Placeholders

Placeholders let you keep a reusable SQL template and inject values at run time.
Enable them with `-p` (or `"placeholder_enable": true`) and provide a JSON file
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
bq-query-runner my-project -p \
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

### Sample files

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

```sh
# Application Default Credentials
gcloud auth application-default login

# or an explicit service account file
bq-query-runner my-project --json_path "C:/mypath/json/service-account.json"
```

If no service account is given (and no `GOOGLE_APPLICATION_CREDENTIALS` is set),
default authentication is used.

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
