install:
	python3 -m pip install --upgrade pip
	python3 -m pip install --upgrade wheel
	python3 -m pip install -r requirements-dev.txt

# run all tests
tests:
	pytest --cli-coverage-dir .cli_coverage/ --html=report.html --self-contained-html cardano_node_tests

lint:
	pre-commit run -a
