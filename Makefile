# install cardano_node_tests and its dependencies into a virtual environment
.PHONY: install
install:
	./scripts/setup_dev_venv.sh

# check if development environment is set up correctly
.PHONY: check_dev_env
check_dev_env:
	@./scripts/check_dev_env.sh

# reinstall cardano-clusterlib-py in editable mode from a given git repository
.PHONY: reinstall-editable
reinstall-editable:
	@if [ -z "$(repo)" ]; then \
		echo "Usage: make reinstall-editable repo=/path/to/cardano-clusterlib-py" >&2; \
		exit 1; \
	fi
	@./scripts/clusterlib_reinstall_editable.sh "$(repo)"

# initialize linters
.PHONY: init_lint
init_lint:
	pre-commit clean
	pre-commit gc
	find . -path '*/.mypy_cache/*' -delete
	pre-commit uninstall
	pre-commit install --install-hooks

# run linters
.PHONY: lint
lint:
	pre-commit run -a --show-diff-on-failure --color=always

# build sphinx documentation
.PHONY: build_doc
build_doc:
	mkdir -p src_docs/build
	$(MAKE) -C src_docs clean
	$(MAKE) -C src_docs html

# build and deploy sphinx documentation
.PHONY: doc
doc:
	./scripts/deploy_doc.sh
