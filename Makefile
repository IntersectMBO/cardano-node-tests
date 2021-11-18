# install cardano_node_tests and its dependencies
install:
	python3 -m pip install --upgrade pip==21.2.4  # TODO: remove once pip 21.3 works
	python3 -m pip install --upgrade wheel
	python3 -m pip install --upgrade -r requirements-dev.txt

# run linters
lint:
	pre-commit run -a

# generate sphinx documentation
doc:
	mkdir -p docs/build
	$(MAKE) -C docs clean
	$(MAKE) -C docs html


ARTIFACTS_DIR ?= .artifacts/
COVERAGE_DIR ?= .cli_coverage/
ALLURE_DIR ?= .reports/

ifneq ($(MARKEXPR),)
	MARKEXPR := -m "$(MARKEXPR)"
endif

.dirs:
	mkdir -p $(ARTIFACTS_DIR) $(COVERAGE_DIR) $(ALLURE_DIR)

.run_tests:
# First just skip all tests so Allure has a list of runable tests. Run only if no pytest args were specified.
ifndef PYTEST_ARGS
	rm -f $(ALLURE_DIR)/{*-attachment.txt,*-result.json,*-container.json}
	pytest -s cardano_node_tests $(MARKEXPR) --skipall --alluredir=$(ALLURE_DIR) >/dev/null
endif
# run tests for real and produce Allure results
	pytest cardano_node_tests $(PYTEST_ARGS) $(CI_ARGS) $(MARKEXPR) -n $(TEST_THREADS) --artifacts-base-dir=$(ARTIFACTS_DIR) --cli-coverage-dir=$(COVERAGE_DIR) --alluredir=$(ALLURE_DIR)


# run all tests, generate allure report
tests: export DbSyncAbortOnPanic=1
tests: TEST_THREADS := $(or $(TEST_THREADS),15)
tests: .dirs .run_tests


# run all enabled tests on testnet, generate allure report
testnets: export DbSyncAbortOnPanic=1
testnets: export NOPOOLS=1
testnets: export CLUSTERS_COUNT=1
testnets: export FORBID_RESTART=1
testnets: TEST_THREADS := $(or $(TEST_THREADS),20)
testnets: MARKEXPR := $(or $(MARKEXPR),-m "testnets")
testnets: .dirs .run_tests
