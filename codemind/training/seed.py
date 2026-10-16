"""Curated starter dataset for the tool-selection adapter.

CodeMind can improve its tool routing by learning from verified tasks, but a fresh install has
no history. This module ships a hand-curated set of instruction → tool pairs so a first training
run is meaningful, plus a longer list used as extra training material.

It is deliberately *not* the evaluation benchmark: :mod:`codemind.training.eval` holds a separate
held-out list, and the two share no instruction text, so a score on the benchmark still means
something. Every example here says what a competent developer would do with the tool inventory
this application actually exposes.
"""

from __future__ import annotations

from typing import Iterable

# (instruction, tool) — grouped by tool so coverage is easy to audit.
SEED_TOOL_EXAMPLES: list[tuple[str, str]] = [
    # -- orientation ------------------------------------------------------
    ("Give me an overview of this codebase", "analyze_repository"),
    ("What kind of project is this and how big is it", "analyze_repository"),
    ("Tell me the primary language and how the code is organised", "analyze_repository"),
    ("Summarise this repository before we touch anything", "analyze_repository"),
    ("How many lines of code are in this project", "analyze_repository"),
    ("What test framework does this repository use", "analyze_repository"),
    ("Which directories hold the source code", "analyze_repository"),
    ("Is this a monorepo or a single package", "analyze_repository"),
    ("Describe the layout of this project to me", "analyze_repository"),
    ("What does this codebase actually do", "analyze_repository"),
    # -- listing and reading ---------------------------------------------
    ("What files are in the src folder", "list_files"),
    ("List the files in this directory", "list_files"),
    ("Show me everything under tests", "list_files"),
    ("Which files exist in the configuration package", "list_files"),
    ("List the python files at the top level", "list_files"),
    ("What is inside the migrations folder", "list_files"),
    ("Show me the contents of codemind/config.py", "read_file"),
    ("Open src/main.py and show me what it does", "read_file"),
    ("Read the README so we know how to run this", "read_file"),
    ("Print the first lines of pyproject.toml", "read_file"),
    ("Let me see the file tests/test_api.py", "read_file"),
    ("What is written in the entry point module", "read_file"),
    ("Show me how the settings file looks", "read_file"),
    ("Display the contents of codemind/agent/executor.py", "read_file"),
    # -- search -----------------------------------------------------------
    ("Where is the database connection created", "search_code"),
    ("Find where parse_config is defined", "search_code"),
    ("Search the code for uses of requests", "search_code"),
    ("Which file defines the TaskRunner class", "search_code"),
    ("Locate the handling of KeyboardInterrupt", "search_code"),
    ("Find every call to subprocess.run", "search_code"),
    ("Where do we read the environment variable HOME", "search_code"),
    ("Show me the definition of the retry decorator", "search_code"),
    ("Find the code that writes the settings file", "search_code"),
    ("Grep for TODO comments in the source", "search_code"),
    # -- project knowledge ------------------------------------------------
    ("What do we know about how the parser works in this project", "knowledge_search"),
    ("Explain the architecture of this project from what you recorded earlier", "knowledge_search"),
    ("What did you learn about the test setup here", "knowledge_search"),
    ("How is this project structured according to your notes", "knowledge_search"),
    ("What symbols were indexed for the storage module", "knowledge_search"),
    ("What do you remember about this repository's conventions", "knowledge_search"),
    ("Which knowledge entries mention permissions", "knowledge_search"),
    ("Tell me what you already know about this codebase", "knowledge_search"),
    # -- memory -----------------------------------------------------------
    ("What did we decide about the logging format", "memory_search"),
    ("Have we seen this error before", "memory_search"),
    ("What did I tell you about my preferred commit style", "memory_search"),
    ("Search your memory for notes about python versions", "memory_search"),
    ("What lessons did you record from past failures", "memory_search"),
    ("Do you remember why we avoided global state last time", "memory_search"),
    ("Remember that this project formats with ruff", "memory_save"),
    ("Please note that I prefer short commit subjects", "memory_save"),
    ("Store that the staging database is read-only", "memory_save"),
    ("Keep in mind that tests must run offline here", "memory_save"),
    ("Write down that the API returns ISO timestamps", "memory_save"),
    ("Record this decision: we use pathlib everywhere", "memory_save"),
    # -- outline ----------------------------------------------------------
    ("List the classes and functions in this project", "project_outline"),
    ("Give me the outline of the codemind package", "project_outline"),
    ("What symbols are exported from the storage module", "project_outline"),
    ("Show the structure of the agent package", "project_outline"),
    ("Which functions live in src/utils.py", "project_outline"),
    ("Summarise the public API of this repository", "project_outline"),
    # -- file changes -----------------------------------------------------
    ("Create a new module for retry helpers", "write_file"),
    ("Write a CHANGELOG entry for this release", "write_file"),
    ("Add a file codemind/utils/validation.py with a validator", "write_file"),
    ("Generate a requirements.txt from the imports", "write_file"),
    ("Create an empty __init__.py in the new package", "write_file"),
    ("Write a small script that prints the version", "write_file"),
    ("Change the default timeout to sixty seconds", "edit_file"),
    ("Rename parse_args to build_parser everywhere", "edit_file"),
    ("Add a docstring to the evaluate method", "edit_file"),
    ("Fix the typo in the log message", "edit_file"),
    ("Update the function signature to accept a path", "edit_file"),
    ("Remove the unused import of json", "edit_file"),
    ("Refactor this long function into two smaller ones", "edit_file"),
    ("Make the error message mention the file name", "edit_file"),
    # -- execution --------------------------------------------------------
    ("Run the linter", "run_command"),
    ("Build the project", "run_command"),
    ("Start the server and check it responds", "run_command"),
    ("Run mypy on the codemind package", "run_command"),
    ("List the installed python packages", "run_command"),
    ("Check that the generated file is valid json", "run_command"),
    ("Compile all python files to catch syntax errors", "run_command"),
    ("Run pytest", "run_tests"),
    ("Do the tests still pass", "run_tests"),
    ("Execute the test suite and show me the summary", "run_tests"),
    ("Run the unit tests for the storage module", "run_tests"),
    ("Check the tests before we go further", "run_tests"),
    ("Verify nothing regressed by running the suite", "run_tests"),
    ("Run this python expression to check the version tuple", "python_exec"),
    ("Evaluate 2 ** 16 for me", "python_exec"),
    ("Use python to compute the checksum of this string", "python_exec"),
    ("Quickly test how pathlib handles this path in python", "python_exec"),
    ("Add the requests library as a dependency", "install_package"),
    ("Install pytest into the environment", "install_package"),
    ("We need the rich package for pretty output", "install_package"),
    ("Add ruff as a development dependency", "install_package"),
    # -- environment ------------------------------------------------------
    ("Which python version is installed", "environment_info"),
    ("What tools are available on this machine", "environment_info"),
    ("Is git installed and which version", "environment_info"),
    ("Show me the current working directory and user", "environment_info"),
    ("What is the timezone of this system", "environment_info"),
    ("Give me a full report about this machine", "environment_report"),
    ("How much RAM and VRAM does the system have", "environment_report"),
    ("Which CUDA devices are visible", "environment_report"),
    ("Report the disk space and hardware details", "environment_report"),
    ("Summarise the environment so I can file a bug report", "environment_report"),
    # -- git --------------------------------------------------------------
    ("What is the status of the working tree", "git_status"),
    ("Which branch am I on and is anything modified", "git_status"),
    ("Are there untracked files", "git_status"),
    ("Show me the diff of my changes", "git_diff"),
    ("What exactly did we modify in this file", "git_diff"),
    ("Commit the current work with a descriptive subject", "git_commit"),
    ("Save the current work as a commit", "git_commit"),
    ("Create a branch called feature/retry", "git_branch"),
    ("Switch to the main branch", "git_branch"),
    ("Push this branch to the remote", "git_push"),
    ("Push my commits upstream", "git_push"),
    ("Open a pull request for this branch", "create_pull_request"),
    ("Create a PR describing the storage fix", "create_pull_request"),
    ("Submit this work for review", "create_pull_request"),
    ("Clone the repository acme/rocket into my projects folder", "repo_clone"),
    ("Get a copy of github.com/pallets/flask so we can read it", "repo_clone"),
    # -- external research ------------------------------------------------
    ("Fetch the pytest documentation about fixtures", "fetch_docs"),
    ("Look up the python docs for pathlib.Path.walk", "fetch_docs"),
    ("Get the page that explains the fastapi dependency injection", "fetch_docs"),
    ("Read the upstream changelog for this library", "fetch_docs"),
    ("Download a three billion parameter coding model for offline use", "download_model"),
    ("Pull a small coding model for local use", "download_model"),
    ("Get the llama 3.2 3b instruct weights", "download_model"),
    # -- no-ops -----------------------------------------------------------
    ("Nothing to do here, just confirm the plan", "noop"),
    ("Skip this step because it is already done", "noop"),
    ("Wait for the user to decide before continuing", "noop"),
    ("Record the current plan without acting", "noop"),
]

# Extra paraphrases used only as training material (not scored anywhere).
SEED_PARAPHRASES: list[tuple[str, str]] = [
    ("Remind yourself what this repo contains", "analyze_repository"),
    ("Give me the lay of the land in this codebase", "analyze_repository"),
    ("How is this software put together", "analyze_repository"),
    ("Inventory the top level of this project", "list_files"),
    ("Which entries live in the lib directory", "list_files"),
    ("Paste the contents of the config module", "read_file"),
    ("Show the source of the main function", "read_file"),
    ("Hunt down where the cache is cleared", "search_code"),
    ("Track down the origin of that warning string", "search_code"),
    ("What have you got on file about this project's parser", "knowledge_search"),
    ("Recall the indexed symbols for the api package", "knowledge_search"),
    ("Did we already settle how errors are logged", "memory_search"),
    ("Bring up any notes about flaky tests", "memory_search"),
    ("Note for later: this project pins numpy to 1.26", "memory_save"),
    ("Keep a record that deploys happen on Fridays", "memory_save"),
    ("Sketch out the module map", "project_outline"),
    ("What functions does this service expose", "project_outline"),
    ("Author a script that tidies the cache", "write_file"),
    ("Put together a fixture file for the tests", "write_file"),
    ("Adjust the retry count to three", "edit_file"),
    ("Tidy up the naming in this module", "edit_file"),
    ("Kick off the formatter", "run_command"),
    ("Run the container for a smoke test", "run_command"),
    ("Let the suite run and tell me the verdict", "run_tests"),
    ("Green tests or not", "run_tests"),
    ("Work out 17 * 23 with python", "python_exec"),
    ("Dependency bump: we need tabulate", "install_package"),
    ("Tell me about this machine's toolchain", "environment_info"),
    ("Full hardware and runtime report please", "environment_report"),
    ("Any uncommitted work sitting around", "git_status"),
    ("Show the patch for the last edits", "git_diff"),
    ("Land these edits as a commit", "git_commit"),
    ("Branch off for the experiment", "git_branch"),
    ("Send the commits to origin", "git_push"),
    ("Raise a PR for review", "create_pull_request"),
    ("Grab a copy of that open source repo", "repo_clone"),
    ("Check the documentation for this API", "fetch_docs"),
    ("Bring down the coding model weights", "download_model"),
    ("Do nothing for now", "noop"),
]


def overlaps_benchmark() -> set[str]:
    """Instructions shared with the evaluation benchmark (must stay empty)."""
    from codemind.training.eval import TOOL_USE_BENCHMARK

    benchmark = {instruction.strip().lower() for instruction, _tool in TOOL_USE_BENCHMARK}
    return benchmark & {instruction.strip().lower() for instruction, _tool in SEED_TOOL_EXAMPLES + SEED_PARAPHRASES}


def seed_examples() -> list[tuple[str, str]]:
    """All starter pairs, de-duplicated on instruction."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for instruction, tool in list(SEED_TOOL_EXAMPLES) + list(SEED_PARAPHRASES):
        key = instruction.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((instruction.strip(), tool))
    return out


def seed_tools() -> list[str]:
    return sorted({tool for _instruction, tool in seed_examples()})


def render_examples(examples: Iterable[tuple[str, str]]) -> str:
    return "\n".join(f"{instruction}\t{tool}" for instruction, tool in examples)
