install:
	python3 -m pip install --upgrade pip
	python3 -m pip install --upgrade wheel
	python3 -m pip install --upgrade -r requirements-dev.txt

ARTIFACTS_DIR ?= .artifacts/
COVERAGE_DIR ?= .cli_coverage/
ALLURE_DIR ?= .reports/
# set even when empty value is passed
ifeq ($(TEST_THREADS),)
	TEST_THREADS := 15
endif

.dirs:
	mkdir -p $(ARTIFACTS_DIR) $(COVERAGE_DIR) $(ALLURE_DIR)

.docs_build_dir:
	mkdir -p docs/build

# run all tests, generate allure report
tests: .dirs
# First just skip all tests so Allure has a list of runable tests. Run only if no pytest args were specified.
ifndef PYTEST_ARGS
	rm -f $(ALLURE_DIR)/{*-attachment.txt,*-result.json,*-container.json}
	pytest -s cardano_node_tests --skipall --alluredir=$(ALLURE_DIR) >/dev/null
endif
# run tests for real and produce Allure results
	pytest cardano_node_tests $(PYTEST_ARGS) $(CI_ARGS) -n $(TEST_THREADS) --artifacts-base-dir=$(ARTIFACTS_DIR) --cli-coverage-dir=$(COVERAGE_DIR) --alluredir=$(ALLURE_DIR)

# run all enabled tests on testnet, generate allure report
# testnets: export NOPOOLS=1
testnets: export CLUSTERS_COUNT=1
testnets: export FORBID_RESTART=1
testnets: .dirs
# First just skip all tests so Allure has a list of runable tests. Run only if no pytest args were specified.
ifndef PYTEST_ARGS
	rm -f $(ALLURE_DIR)/{*-attachment.txt,*-result.json,*-container.json}
	pytest -s cardano_node_tests -m testnets --skipall --alluredir=$(ALLURE_DIR) >/dev/null
endif
# run tests for real and produce Allure results
	pytest cardano_node_tests $(PYTEST_ARGS) $(CI_ARGS) -n $(TEST_THREADS) -m "testnets and not rewards" --artifacts-base-dir=$(ARTIFACTS_DIR) --cli-coverage-dir=$(COVERAGE_DIR) --alluredir=$(ALLURE_DIR)

# run linters
lint:
	pre-commit run -a

# generate sphinx documentation
doc: .docs_build_dir
	$(MAKE) -C docs clean
	$(MAKE) -C docs html
