install:
	python3 -m pip install --upgrade pip setuptools wheel
	python3 -m pip install -r requirements-dev.txt

# run all tests
tests:
	pytest -m "not clean_cluster"
	pytest -m "clean_cluster"

lint:
	pre-commit run -a
