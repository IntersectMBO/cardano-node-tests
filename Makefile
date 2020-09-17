install:
	python3 -m pip install --upgrade pip
	python3 -m pip install --upgrade wheel
	python3 -m pip install -r requirements-dev.txt

.dirs:
	mkdir -p .artifacts .cli_coverage .reports

# run all tests, generate allure report
tests: .dirs
	pytest cardano_node_tests -n 8 --dist loadscope --artifacts-base-dir=.artifacts/ --cli-coverage-dir=.cli_coverage/ --alluredir=.reports/

lint:
	pre-commit run -a
