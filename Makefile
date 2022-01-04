# install cardano_node_tests and its dependencies
install: export SETUPTOOLS_USE_DISTUTILS=stdlib  # TODO: workaround for https://github.com/asottile/reorder_python_imports/issues/210
install:
	python3 -m pip install --upgrade pip
	python3 -m pip install --upgrade wheel
	python3 -m pip install --upgrade --upgrade-strategy eager -r requirements-dev.txt


# install dependencies that are needed for building documentation
install_doc: export SETUPTOOLS_USE_DISTUTILS=stdlib  # TODO: workaround for https://github.com/asottile/reorder_python_imports/issues/210
install_doc:
	python3 -m pip install --upgrade --upgrade-strategy eager -r requirements-doc.txt


# run linters
lint: export SETUPTOOLS_USE_DISTUTILS=stdlib  # TODO: workaround for https://github.com/asottile/reorder_python_imports/issues/210
lint:
	pre-commit run -a


# generate sphinx documentation
.PHONY: doc
doc:
	mkdir -p src_docs/build
	$(MAKE) -C src_docs clean
	$(MAKE) -C src_docs html


# run tests

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
