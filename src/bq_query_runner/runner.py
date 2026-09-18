"""
bq_query_runner - run SQL scripts on Google BigQuery from files.

This is the single entry-point module: it holds the helper functions, the
:class:`RunQuery` and :class:`Logger` classes and the :func:`main` function
wired to the ``bq-query-runner`` console script.

Arguments are read EITHER from the command line OR from a JSON config file
(mutually exclusive). Working folders (sql, log, placeholder, output) and
relative paths are resolved against the current working directory (see
:func:`base_dir`), so the tool behaves like a standard CLI once installed.
"""

# libraries
import os
import sys
import json
import argparse
import datetime
import traceback
import math
import importlib
import re

# handle required libraries
required_libraries = {
    'pandas': 'pd',
    'pyarrow': 'pa',
    'pyarrow.parquet': 'pq',
    'google.cloud.bigquery': 'bigquery',
    'sqlparse': 'sqlp'
}
"""Dictionary of required libraries mapped to their in-module alias."""

# import required libraries with defined aliases
for lib, alias in required_libraries.items():
    try:
        globals()[alias] = importlib.import_module(lib)
    except ImportError:
        print(f"E: Missing required library: {lib}")
        sys.exit(1)

# encoding used to read text files (BOM-tolerant: handles UTF-8 with or without BOM)
TEXT_ENCODING = 'utf-8-sig'

# path arguments that support relative paths (resolved against base_dir())
PATH_PARAMS = ['sql_path', 'log_path', 'placeholder_path', 'sa_json_key_path', 'output_path']


def base_dir():
    """Base directory for default working folders and relative-path resolution.

    The current working directory is used (not the package location), so that an
    installed console command creates and looks up its folders where it is run.

    Returns:
        The current working directory.
    """
    return os.getcwd()


def validate_output_format(value):
    """Validate the output format argument (case insensitive) and normalize it.

    Args:
        value: The output format argument to validate.

    Returns:
        The lower-cased, validated output format.
    """
    valid_formats = ['parquet', 'csv', 'json']
    normalized = value.lower()
    if normalized not in valid_formats:
        raise argparse.ArgumentTypeError(
            f"Invalid output format: {value}. Must be one of 'parquet', 'csv', or 'json'.")
    return normalized


def load_config_file(path):
    """Load configuration from a JSON file with error handling.

    Args:
        path: Path to the JSON configuration file.

    Returns:
        Dictionary with the configuration parameters.
    """
    try:
        with open(path, 'r', encoding=TEXT_ENCODING) as f:
            return json.load(f)
    except (IOError, json.JSONDecodeError) as e:
        print(f"E: Error loading configuration file {path}: {str(e)}")
        sys.exit(1)


def is_absolute_path(path):
    """Return True only for genuinely absolute paths.

    On Windows a path is considered absolute only when it carries a drive letter
    or a UNC anchor: a bare leading slash/backslash (e.g. "/sql") is NOT treated
    as absolute and must be resolved against the base directory rather than the
    drive root.
    """
    if os.name == 'nt':
        return bool(os.path.splitdrive(path)[0])
    return os.path.isabs(path)


def resolve_paths(input_param):
    """Resolve relative path parameters against the base directory.

    Drive-less paths that start with a separator (e.g. "/sql" or "\\sql") are
    treated as relative too: their leading separators are stripped so they
    resolve to <base_dir>/sql instead of the drive root (C:/sql).

    Args:
        input_param: dictionary containing all the input arguments.

    Returns:
        The same dictionary with path parameters resolved to absolute paths.
    """
    root = base_dir()
    for param in PATH_PARAMS:
        value = input_param.get(param)
        if not value:
            continue
        if is_absolute_path(value):
            input_param[param] = os.path.normpath(value)
        else:
            input_param[param] = os.path.normpath(os.path.join(root, value.lstrip('/\\')))
    return input_param


def check_arguments(input_param):
    """
    Function to check the input arguments.

    Args:
        input_param: dictionary containing all the input arguments from main function
    """
    try:
        # Validate project
        if not input_param.get('PROJECT'):
            raise Exception("The PROJECT argument is required. Provide it in the command line or in the config file.")

        # Validate input paths: sql_path must exist if provided; placeholder_path
        # is validated whenever it is set (providing it is what enables substitution).
        # sa_json_key_path is intentionally NOT validated here: a missing service account falls back to default auth.
        # Output paths (log/output) may not exist yet, as long as the parent does.
        input_paths = ['sql_path']
        if input_param.get('placeholder_path'):
            input_paths.append('placeholder_path')
        for path_param in input_paths:
            value = input_param.get(path_param)
            if value and not os.path.exists(value):
                raise Exception(f"{path_param} does not exist: {value}")

        for path_param in ['log_path', 'output_path']:
            value = input_param.get(path_param)
            if value and not os.path.exists(value):
                parent = os.path.dirname(value)
                if parent and not os.path.exists(parent):
                    raise Exception(f"{path_param} parent directory does not exist: {parent}")

        # Validate replace string format
        if input_param.get('replace'):
            replacements = input_param.get('replace').split(',')
            for replacement in replacements:
                if ':' not in replacement:
                    raise Exception(
                        f"Invalid replacement format: {replacement}. Expected format: original:replacement")

        # Validate output format (covers the config-file source too)
        output_format = input_param.get('output_format')
        if output_format and output_format.lower() not in ['parquet', 'csv', 'json']:
            raise Exception(f"Invalid output_format: {output_format}. Must be one of: parquet, csv, json")

        # Create default work folders only for the parameters that were not provided
        if not input_param.get('sql_path'):
            check_work_folders('sql')
        if not input_param.get('log_path'):
            check_work_folders('log')
        if not input_param.get('placeholder_path'):
            check_work_folders('placeholder')
        if not input_param.get('output_path'):
            check_work_folders('output')

    except Exception as e:
        print(f"E: Error while validating arguments: {str(e)}")
        sys.exit(1)


def check_work_folders(*args):
    """
    Function checking the presence of required folders. If not found, they are created.

    Args:
        *args: list of folders to be verified
    """
    for fold in args:
        folder_path = os.path.join(base_dir(), fold)
        if not os.path.exists(folder_path):
            try:
                os.makedirs(folder_path)
                print(f"I: Created folder: {folder_path}")
            except Exception as e:
                print(f"E: Error creating folder {folder_path}: {str(e)}")
                sys.exit(1)


class RunQuery:
    """
    Class managing the BigQuery run queries.

    The class contains all the methods to complete the query run process.

    Args:
        input_param: dictionary containing all the input arguments from main function
        log_obj: Logger class instance to manage logging and resume functions
    """
    def __init__(self, input_param, log_obj):
        """Function to initialize the RunQuery class"""
        # define instance variables from input parameters
        self.input_param = input_param
        """Dictionary containing all the input arguments from main function"""

        # Dynamically set instance attributes from input parameters
        for key, value in self.input_param.items():
            setattr(self, key.lower(), value)

        # cache for placeholders loaded from the placeholder file (see load_placeholders)
        self._placeholders = None

        # get logging instance object
        self.logger = log_obj
        """Logger class instance to manage logging and resume functions"""

    def print_parameters(self):
        """
        Function to log and print on screen the input arguments.
        """
        # log parameters
        for key, value in self.input_param.items():
            self.logger.log(f"I: {key}: {str(value)}")

    def check_service_acc(self):
        """
        Function to set the service account credentials.

        Either an explicit file (sa_json_key_path) is used, or nothing:
        there is no folder auto-discovery. When sa_json_key_path is not
        provided - or points to a missing file - default authentication is used
        (no fail-fast).
        """
        if self.sa_json_key_path and os.path.isfile(self.sa_json_key_path):
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = self.sa_json_key_path
            self.logger.log(f"I: Using specified service account: {self.sa_json_key_path}")
        else:
            self.logger.log("I: No service account JSON path provided or file not found. "
                            "Using default authentication.")

    def set_bq_client(self, project=None):
        """
        Function to instantiate a BigQuery client.

        Args:
            project: BigQuery project where to set connection

        Returns:
            A BigQuery client instance
        """
        if project is None:
            project = self.project
        try:
            self.client = bigquery.Client(project)
        except Exception as e:
            self.logger.log(f"E: Error creating BigQuery client: {str(e)}")
            sys.exit(1)
        return self.client

    def test_bq_client(self, client):
        """
        Function to test a BigQuery client.

        Runs a lightweight dry-run query to validate project, credentials and the
        ability to submit jobs, without requiring dataset-list permissions (which
        a query-only service account may lack).

        Args:
            client: BigQuery client instance
        """
        try:
            client.query("SELECT 1", job_config=bigquery.QueryJobConfig(dry_run=True))
        except Exception as e:
            self.logger.log(f"E: Invalid project or credentials: {str(e)}")
            sys.exit(1)

    def define_sql_files(self):
        """
        Function to define SQL files to process.

        Returns:
            A sorted list with all the SQL files to work with
        """
        try:
            # Default to the ./sql folder if no sql_path is provided
            if self.sql_path is None:
                self.sql_path = os.path.join(base_dir(), "sql")

            sql_files = []
            if os.path.isdir(self.sql_path):
                for r, _, files in os.walk(self.sql_path):
                    for file in files:
                        if file.upper().endswith('.SQL'):
                            sql_files.append(os.path.join(r, file))
            elif os.path.isfile(self.sql_path):
                if self.sql_path.upper().endswith('.SQL'):
                    sql_files.append(self.sql_path)
            else:
                raise Exception(f"SQL Path not found: {self.sql_path}")

            if not sql_files:
                self.logger.log(f"E: No SQL files found in: {self.sql_path}")
                sys.exit(1)

            # Deterministic execution order
            return sorted(sql_files)

        except Exception as e:
            self.logger.log(f"E: Error getting SQL files: {str(e)}")
            sys.exit(1)

    def load_placeholders(self):
        """
        Function to load placeholder definitions from the placeholder file
        (loaded once and cached). Placeholder substitution is enabled simply by
        providing ``placeholder_path``: when it is not set, no substitution is done.

        Returns:
            Dictionary mapping placeholder names to their substitution values
        """
        # No file provided -> placeholders are disabled.
        if not self.placeholder_path:
            return {}

        # Return the cached placeholders if already loaded
        if self._placeholders is not None:
            return self._placeholders

        # Cache the outcome from here on (even an empty dict) so the lookup runs once.
        self._placeholders = {}

        # Load placeholders from the file
        if not os.path.isfile(self.placeholder_path):
            self.logger.log(f"W: Placeholder file not found at {self.placeholder_path}. "
                            "Continuing without placeholders.")
            return self._placeholders

        try:
            with open(self.placeholder_path, 'r', encoding=TEXT_ENCODING) as f:
                placeholders = json.load(f)
            self.logger.log(f"I: Loaded {len(placeholders)} placeholders from {self.placeholder_path}")
            for name, value in placeholders.items():
                self.logger.log(f"I: Placeholder loaded: {name} = {value}")
            self._placeholders = placeholders
        except Exception as e:
            self.logger.log(f"W: Error loading placeholder file {self.placeholder_path}: {str(e)}. "
                            "Continuing without placeholders.")

        return self._placeholders

    def split_sql_statements(self, sql_text):
        """
        Splits the given SQL text into individual SQL statements.

        Uses the sqlparse library to correctly split the SQL text into separate
        statements, stripping leading/trailing whitespace and filtering out
        empty statements.

        Args:
            sql_text (str): The SQL text to be split into individual statements.

        Returns:
            list: A list of individual SQL statements as strings.
        """
        return [statement.strip() for statement in sqlp.split(sql_text) if statement.strip()]

    def prep_sql_file(self, sql_file):
        """
        Function to prepare the SQL file to work, applying placeholder substitution
        and text replacements if any.

        Args:
            sql_file: SQL file to prepare

        Returns:
            List of SQL statements with placeholders and replacements applied
        """
        try:
            # Read the file with a BOM-tolerant UTF-8 codec (prevents
            # 'Illegal input character' errors on files carrying a UTF-8 BOM).
            with open(sql_file, "r", encoding=TEXT_ENCODING) as f:
                data = f.read()

            # Apply ${...} placeholder substitution when a placeholder file is provided
            if self.placeholder_path:
                placeholders = self.load_placeholders()
                if placeholders:
                    referenced = re.findall(r'\${([^}]+)}', data)

                    # Check that all referenced placeholders are defined
                    missing = [name for name in referenced if name not in placeholders]
                    if missing:
                        self.logger.log(f"E: Missing placeholders: {', '.join(missing)}")
                        sys.exit(1)

                    for name, value in placeholders.items():
                        token = "${" + name + "}"
                        if token in data:
                            data = data.replace(token, str(value))
                            self.logger.log(f"I: Placeholder replaced in SQL File - {token}:{value}")

            # Apply replace in the SQL file, if present
            if self.replace is not None:
                string_couples = self.replace.split(',')
                for rep in string_couples:
                    words_rep = rep.split(':')
                    if len(words_rep) == 2:
                        data = data.replace(words_rep[0], words_rep[1])
                        self.logger.log(f"I: Replace in SQL File - {words_rep[0]}:{words_rep[1]}")
                    else:
                        self.logger.log(f"W: Skipping invalid replacement format: {rep}")

            return self.split_sql_statements(data)

        except Exception as e:
            self.logger.log(f"E: Error working SQL file {sql_file}: {str(e)}")
            sys.exit(1)

    def get_size(self, size_bytes):
        """
        Function to get readable size of query data processed.

        Args:
            size_bytes: total bytes processed

        Returns:
            Readable size of data processed
        """
        if not size_bytes:
            return "0B"
        size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s}{size_name[i]}"

    def start_process(self, client, sql_files, res_content):
        """
        Function starting the process.

        For each SQL file, prepare the string and run the queries.

        Args:
            client: BigQuery client instance
            sql_files: List of all the SQL files to work
            res_content: Content from resume file (previous run log)
        """
        for sql_file in sql_files:
            self.logger.log(f"I: Working file: {sql_file}")
            instructions = self.prep_sql_file(sql_file)

            # run queries
            for i, instruction in enumerate(instructions):
                # Check if it is a resume and the query has already been processed
                query_id = f"{sql_file}:{i+1}"
                if query_id in res_content:
                    self.logger.log(f"I: Skipping query {i+1} (already processed)")
                    continue

                # Log the query we're about to run (first 100 chars)
                preview = instruction.replace("\n", " ")[:100] + "..."
                self.logger.log(f"I: Running query {i+1} - {preview}")

                # Configure and execute the query job
                job_config = bigquery.QueryJobConfig(dry_run=self.dry_run)

                try:
                    job = client.query(instruction, job_config=job_config)

                    # Wait for the job to complete and check results
                    result = job.result()

                    # Get data processed and execution time
                    size = self.get_size(job.total_bytes_processed)
                    time_info = ""
                    if not self.dry_run and job.ended and job.started:
                        time_info = f". Execution time: {job.ended - job.started}"
                    self.logger.log(f"I: Query completed. Processed: {size}{time_info}")

                    # Save results if requested and there are records to save
                    if self.output_format and not self.dry_run:
                        total_rows = getattr(result, 'total_rows', None) or 0
                        if total_rows > 0:
                            df = result.to_dataframe()
                            self.save_results(df, sql_file, i + 1)
                        else:
                            self.logger.log(f"W: No records to save for query {i+1}")

                    # Record successful execution for resume functionality
                    self.logger.log_resume(query_id)

                except Exception:
                    self.logger.log(f"E: {traceback.format_exc()}")
                    sys.exit(1)

    def save_results(self, df, sql_file, stmt_index):
        """
        Function to save query results to a file.

        Args:
            df: DataFrame containing query results
            sql_file: The SQL file path
            stmt_index: 1-based index of the statement within the SQL file
        """
        try:
            # Default to the ./output folder if no output_path is provided
            if self.output_path is None:
                self.output_path = os.path.join(base_dir(), "output")
            if not os.path.exists(self.output_path):
                os.makedirs(self.output_path)

            # Build a filesystem-safe output filename (no path/colon in the name)
            sql_file_name = os.path.splitext(os.path.basename(sql_file))[0]
            output_format = self.output_format.lower()
            file_name = f"{self.logger.timestamp}_{sql_file_name}_stmt{stmt_index}.{output_format}"
            file_path = os.path.join(self.output_path, file_name)

            # Save based on format
            if output_format == 'csv':
                df.to_csv(file_path, index=False)
            elif output_format == 'json':
                df.to_json(file_path, orient='records', lines=True)
            elif output_format == 'parquet':
                table = pa.Table.from_pandas(df)
                pq.write_table(table, file_path)
            else:
                self.logger.log(f"E: Unsupported output format '{output_format}'. Results not saved.")
                return

            self.logger.log(f"I: Results saved to {file_path}")

        except Exception as e:
            self.logger.log(f"E: Error saving results: {str(e)}")
            sys.exit(1)


class Logger:
    """
    Class managing logging.

    Logging info and Resume info are written to files.

    Args:
        file_name: Base name for the log file
        log_path: Path where log files are stored. If None, defaults to ./log folder
    """
    def __init__(self, file_name, log_path=None):
        """Function to initialize the Logger class"""
        if log_path is None:
            self.log_path = os.path.join(base_dir(), "log")
        elif is_absolute_path(log_path):
            self.log_path = os.path.normpath(log_path)
        else:
            # drive-less paths (incl. leading-slash like "/log") -> relative to base_dir
            self.log_path = os.path.normpath(os.path.join(base_dir(), log_path.lstrip('/\\')))
        """Path where log files are stored. If None, defaults to ./log folder"""

        # Ensure log directory exists
        if not os.path.exists(self.log_path):
            os.makedirs(self.log_path)

        self.timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        """Timestamp used for log file name"""
        self.log_name = f"{self.timestamp}_{file_name}.log"
        """Base name for the log file"""
        self.log_file = os.path.join(self.log_path, self.log_name)
        """Full path of the log file"""
        self.resume_name = 'resume.txt'
        """Base name for the resume file"""
        self.resume_file = os.path.join(self.log_path, self.resume_name)
        """Full path of the resume file"""

    def check_resume(self, resume):
        """
        Function checking the resume argument and the resume file.

        Args:
            resume: input argument resume

        Returns:
            List of already processed queries from resume file
        """
        res_content = []
        if resume:
            if os.path.isfile(self.resume_file):
                try:
                    with open(self.resume_file, encoding=TEXT_ENCODING) as f:
                        res_content = f.read().splitlines()
                except Exception as e:
                    print(f"W: Could not read resume file: {str(e)}")
            else:
                print("W: Resume flag set but no resume file found")
        else:
            if os.path.isfile(self.resume_file):
                try:
                    os.remove(self.resume_file)
                except Exception as e:
                    print(f"W: Could not remove existing resume file: {str(e)}")

        return res_content

    def log(self, text):
        """
        Function writing text in the log file.

        Args:
            text: text to write in the log file
        """
        tstmp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        print(f"{tstmp}: {text}")
        try:
            with open(self.log_file, "a+", encoding='utf-8') as f:
                f.write(f"{tstmp}: {text}\n")
        except Exception as e:
            print(f"W: Could not write to log file: {str(e)}")

    def log_resume(self, text):
        """
        Function writing text in the resume file.

        Args:
            text: text to write in the resume file
        """
        try:
            with open(self.resume_file, "a+", encoding='utf-8') as f:
                f.write(f"{text}\n")
        except Exception as e:
            print(f"W: Could not write to resume file: {str(e)}")

    def remove_resume_file(self):
        """
        Function to remove the resume file at the end of execution with no errors.
        """
        if os.path.isfile(self.resume_file):
            try:
                os.remove(self.resume_file)
            except Exception as e:
                print(f"W: Could not remove resume file: {str(e)}")

    def set_res_content(self, resume):
        """
        Function reading the status of the previous run from resume file, if any.

        Args:
            resume: Boolean indicating if restart from previous run

        Returns:
            Content from resume file (previous run log)
        """
        self.res_content = self.check_resume(resume)
        return self.res_content


def main():
    """
    Main entry point that orchestrates the process.

    Arguments are read EITHER from a config file (--config_path) OR from the
    command line: the two sources are mutually exclusive (no merge). When
    --config_path is given, every parameter comes from the config file.

    Run examples:
        bq-query-runner my-project --sql_path C:/mypath/sql
        bq-query-runner --config_path C:/mypath/config/config.json
    """
    # build the CLI parser
    parser = argparse.ArgumentParser(
        prog="bq-query-runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Run SQL scripts on Google BigQuery from files.\n"
            "\n"
            "Arguments are read either from a config file (--config_path) OR from the\n"
            "command line.\n"
            "Each .sql file may contain several statements (split on ';').\n"
            "Dry-Run to test queries can be executed. \n"
            "Placeholders and/or text replacement can be applied. \n"
            "Results can optionally be exported as parquet, csv or json."
        ),
        epilog=(
            "examples:\n"
            "  # take every parameter from a config file\n"
            "  bq-query-runner --config_path C:/mypath/config/config.json\n"
            "\n"
            "  # run all .sql in a folder and subfolders\n"
            "  bq-query-runner my-project --sql_path C:/mypath/sql\n"
            "\n"
            "  # dry-run a single file (validate + estimate bytes, no execution)\n"
            "  bq-query-runner my-project --dry_run --sql_path C:/mypath/sql/query.sql\n"
            "\n"
            "  # substitute ${...} placeholders from a JSON file\n"
            "  bq-query-runner my-project --placeholder_path C:/mypath/placeholder/placeholder.json --sql_path C:/mypath/sql"
        ),
    )

    # positional
    parser.add_argument('PROJECT', type=str, nargs='?', default=None,
                        help='Target Google Cloud project (omit if provided in the config file).')

    # input
    g_input = parser.add_argument_group('input')
    g_input.add_argument('--config_path', type=str,
                         help='JSON config file. If set, all parameters are read from it (CLI args ignored).')
    g_input.add_argument('--sql_path', type=str,
                         help='SQL file or directory to run (default: ./sql).')

    # placeholders & replacements
    g_subst = parser.add_argument_group('placeholders & replacements')
    g_subst.add_argument('--placeholder_path', type=str,
                         help='Placeholder JSON file; providing it enables ${...} substitution.')
    g_subst.add_argument('--replace', type=str,
                         help='Literal replacements, e.g. orig:changed,orig2:changed2.')

    # output
    g_output = parser.add_argument_group('output')
    g_output.add_argument('--output_format', type=validate_output_format,
                          help='Export results as parquet, csv or json (omit to skip export).')
    g_output.add_argument('--output_path', type=str,
                          help='Export destination folder (default: ./output).')

    # execution & authentication
    g_run = parser.add_argument_group('execution & authentication')
    g_run.add_argument('--dry_run', dest='dry_run', action='store_true',
                       help='Dry run: validate and estimate bytes, do not execute.')
    g_run.add_argument('--resume', dest='resume', action='store_true',
                       help='Resume from a previous error (skip already-run statements).')
    g_run.add_argument('--sa_json_key_path', type=str,
                       help='Service account JSON key file (default: Application Default Credentials).')
    g_run.add_argument('--log_path', type=str,
                       help='Log files destination (default: ./log).')

    # define arguments
    args = parser.parse_args()

    # Default values for arguments
    DEFAULTS = {
        'PROJECT': None,
        'resume': False,
        'dry_run': False,
        'sql_path': None,
        'log_path': None,
        'placeholder_path': None,
        'sa_json_key_path': None,
        'replace': None,
        'output_format': None,
        'output_path': None,
    }

    # Select the source of parameters: config file OR command line (mutually exclusive).
    if args.config_path:
        source = load_config_file(args.config_path)
    else:
        source = {k: v for k, v in vars(args).items() if v is not None}
    input_param = {**DEFAULTS, **source}

    # resolve relative paths, then validate
    input_param = resolve_paths(input_param)
    check_arguments(input_param)

    # create logging object
    log_obj = Logger('run_query', input_param.get('log_path'))

    # create custom RunQuery object
    rq_session = RunQuery(input_param, log_obj)

    # log input parameters
    rq_session.print_parameters()

    # check service account
    rq_session.check_service_acc()

    # create bq client
    bq_client = rq_session.set_bq_client()

    # test bq client
    rq_session.test_bq_client(bq_client)

    # set resume content if needed
    res_content = log_obj.set_res_content(rq_session.resume)

    # set sql files
    sql_files = rq_session.define_sql_files()

    # start process
    rq_session.start_process(bq_client, sql_files, res_content)

    # remove log_resume file
    log_obj.remove_resume_file()


if __name__ == "__main__":
    main()
