install:
	python3 -m pip install --upgrade pip
	python3 -m pip install --upgrade wheel
	python3 -m pip install -r requirements-dev.txt

.dirs:
	mkdir -p .cli_coverage
	mkdir -p .reports

# run all tests
tests: .dirs
	pytest --cli-coverage-dir .cli_coverage/ cardano_node_tests --html=.reports/report.html

lint:
	pre-commit run -a
