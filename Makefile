# install cardano_node_tests and its dependencies into a virtual environment
.PHONY: install
install:
	./setup_venv.sh

# initialize linters
.PHONY: init_linters
init_lint:
	pre-commit clean
	pre-commit gc
	find . -path '*/.mypy_cache/*' -delete

# run linters
.PHONY: lint
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

.PHONY: .dirs
.dirs:
	mkdir -p $(ARTIFACTS_DIR) $(COVERAGE_DIR) $(REPORTS_DIR)

.PHONY: .run_tests
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
	pytest cardano_node_tests $(PYTEST_ARGS) $(CI_ARGS) $(MARKEXPR) $(DESELECT_FROM_FILE_ARGS) -n $(TEST_THREADS) $(ARTIFACTS_ARGS) --cli-coverage-dir=$(COVERAGE_DIR) --alluredir=$(REPORTS_DIR) $(TESTRUN_REPORT_ARGS)


# run all tests
.PHONY: tests
tests: export DbSyncAbortOnPanic=1
tests: TEST_THREADS := $(or $(TEST_THREADS),20)
tests: .dirs .run_tests


# run tests that are supposed to run on PR level
.PHONY: testpr
testpr: export TESTPR=1
testpr: export SCRIPTS_DIRNAME := $(or $(SCRIPTS_DIRNAME),babbage_fast)
testpr: export CLUSTERS_COUNT := $(or $(CLUSTERS_COUNT),5)
testpr: TEST_THREADS := $(or $(TEST_THREADS),20)
testpr: MARKEXPR := $(or $(MARKEXPR),-m "smoke")
testpr: .dirs .run_tests


# run all tests that can run on testnets
.PHONY: testnets
testnets: export NOPOOLS=1
testnets: export CLUSTERS_COUNT=1
testnets: export FORBID_RESTART=1
testnets: TEST_THREADS := $(or $(TEST_THREADS),20)
testnets: MARKEXPR := $(or $(MARKEXPR),-m "testnets")
testnets: .dirs .run_tests
