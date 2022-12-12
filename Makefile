# install cardano_node_tests and its dependencies
install:
	python3 -m pip install --upgrade pip
	python3 -m pip install --upgrade wheel
	python3 -m pip install --upgrade --upgrade-strategy eager -r requirements-dev.txt
	virtualenv --upgrade-embed-wheels


# install dependencies that are needed for building documentation
install_doc:
	python3 -m pip install --upgrade --upgrade-strategy eager -r requirements-doc.txt


# initialize linters
init_lint:
	pre-commit clean
	pre-commit gc
	find . -path '*/.mypy_cache/*' -delete

# run linters
lint:
	pre-commit run -a
	if type pytype >/dev/null 2>&1; then pytype cardano_node_tests; fi


# generate sphinx documentation
.PHONY: doc
doc:
	mkdir -p src_docs/build
	$(MAKE) -C src_docs clean
	$(MAKE) -C src_docs html


# run tests

ARTIFACTS_DIR ?= .artifacts
COVERAGE_DIR ?= .cli_coverage
REPORTS_DIR ?= .reports

ifneq ($(MARKEXPR),)
	MARKEXPR := -m "$(MARKEXPR)"
endif

# it may not be always necessary to save artifacts, e.g. when running tests on local cluster
# on local machine
ifndef NO_ARTIFACTS
	ARTIFACTS_ARGS := --artifacts-base-dir=$(ARTIFACTS_DIR)
endif

ifndef PYTEST_ARGS
	TESTRUN_REPORT_ARGS := --html=$(REPORTS_DIR)/testrun-report.html --self-contained-html --junitxml=$(REPORTS_DIR)/testrun-report.xml
endif

ifdef DESELECT_FROM_FILE
	DESELECT_FROM_FILE_ARGS := --deselect-from-file=$(DESELECT_FROM_FILE)
endif

CLEANUP := yes
RUN_SKIPS := yes

ifdef PYTEST_ARGS
	CLEANUP := no
	RUN_SKIPS := no
endif

ifdef DESELECT_FROM_FILE
	RUN_SKIPS := no
endif

.dirs:
	mkdir -p $(ARTIFACTS_DIR) $(COVERAGE_DIR) $(REPORTS_DIR)

.run_tests:
# delete artifacts from previous runs
ifeq ($(CLEANUP),yes)
	rm -f $(REPORTS_DIR)/{*-attachment.txt,*-result.json,*-container.json,testrun-report.*}
	rm -f $(COVERAGE_DIR)/cli_coverage_*
endif

# first just skip all tests so Allure has a list of all tests that were supposed to run
ifeq ($(RUN_SKIPS),yes)
	pytest -s cardano_node_tests $(MARKEXPR) --skipall --alluredir=$(REPORTS_DIR) >/dev/null
endif

# run tests for real and produce Allure results
	pytest cardano_node_tests $(PYTEST_ARGS) $(CI_ARGS) $(MARKEXPR) $(DESELECT_FROM_FILE_ARGS) -n $(TEST_THREADS) --dist loadgroup $(ARTIFACTS_ARGS) --cli-coverage-dir=$(COVERAGE_DIR) --alluredir=$(REPORTS_DIR) $(TESTRUN_REPORT_ARGS)


# run all tests, generate allure report
tests: export DbSyncAbortOnPanic=1
tests: TEST_THREADS := $(or $(TEST_THREADS),15)
tests: .dirs .run_tests


# run all enabled tests on testnet, generate allure report
testnets: export NOPOOLS=1
testnets: export CLUSTERS_COUNT=1
testnets: export FORBID_RESTART=1
testnets: TEST_THREADS := $(or $(TEST_THREADS),20)
testnets: MARKEXPR := $(or $(MARKEXPR),-m "testnets")
testnets: .dirs .run_tests
