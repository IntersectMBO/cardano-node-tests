#!/bin/sh

set -e

if [ ! -e .cli_coverage ]; then mkdir .cli_coverage; fi

PYTEST_RETVAL=0

# Record highest pytest return value.
# exit code 0:	All tests were collected and passed successfully
# exit code 1:	Tests were collected and run but some of the tests failed
# exit code 2:	Test execution was interrupted by the user
# exit code 3:	Internal error happened while executing tests
# exit code 4:	pytest command line usage error
set_pytest_retval() {
  if [ "$1" -gt "$PYTEST_RETVAL" ]; then
    PYTEST_RETVAL="$1"
  fi
}

pytest -m "not clean_cluster" --cli-coverage-dir .cli_coverage/ cardano_node_tests || set_pytest_retval "$?"
pytest -m "clean_cluster" --cli-coverage-dir .cli_coverage/ cardano_node_tests || set_pytest_retval "$?"

exit "$PYTEST_RETVAL"
