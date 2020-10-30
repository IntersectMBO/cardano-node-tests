install:
	python3 -m pip install --upgrade pip
	python3 -m pip install --upgrade wheel
	python3 -m pip install -r requirements-dev.txt

ARTIFACTS_DIR ?= .artifacts/
COVERAGE_DIR ?= .cli_coverage/
ALLURE_DIR ?= .reports/

.dirs:
	mkdir -p $(ARTIFACTS_DIR) $(COVERAGE_DIR) $(ALLURE_DIR)

# run all tests, generate allure report
TEST_THREADS ?= 8
tests: .dirs
	pytest cardano_node_tests $(PYTEST_ARGS) -n $(TEST_THREADS) --artifacts-base-dir=$(ARTIFACTS_DIR) --cli-coverage-dir=$(COVERAGE_DIR) --alluredir=$(ALLURE_DIR)

lint:
	pre-commit run -a
