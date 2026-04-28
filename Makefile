.DEFAULT_GOAL := help

TESTNET_VARIANT ?= conway_fast
VENV := .venv

.PHONY: .check-venv-exists
.check-venv-exists:
	@if [ ! -d "$(VENV)" ]; then \
		echo "Error: Virtual environment not found. Please run 'make install' first." >&2; \
		exit 1; \
	fi

.PHONY: .check-venv-activated
.check-venv-activated:
	@venv_abs="$$(readlink -f -- "$(CURDIR)/$(VENV)" 2>/dev/null || echo "$(CURDIR)/$(VENV)")"; \
		actual="$$(readlink -f -- "$${VIRTUAL_ENV:-}" 2>/dev/null || echo "$${VIRTUAL_ENV:-}")"; \
		if [ -z "$${VIRTUAL_ENV:-}" ]; then \
			echo "Error: Virtual environment not activated. Please run 'source $(VENV)/bin/activate' first." >&2; \
			exit 1; \
		elif [ "$$actual" != "$$venv_abs" ]; then \
			echo "Error: Wrong virtual environment is activated. Please run 'source $(VENV)/bin/activate'." >&2; \
			echo "Expected: $$venv_abs" >&2; \
			echo "Actual:   $$actual" >&2; \
			exit 1; \
		fi

.PHONY: .check-venv-not-activated
.check-venv-not-activated:
	@venv_abs="$$(readlink -f -- "$(CURDIR)/$(VENV)" 2>/dev/null || echo "$(CURDIR)/$(VENV)")"; \
		actual="$$(readlink -f -- "$${VIRTUAL_ENV:-}" 2>/dev/null || echo "$${VIRTUAL_ENV:-}")"; \
		if [ -n "$${VIRTUAL_ENV:-}" ] && [ "$$actual" = "$$venv_abs" ]; then \
			echo "Error: Project virtual environment is currently activated. Please deactivate it first." >&2; \
			exit 1; \
		fi

## ---------------------------------------------------------------------------
## Setup
## ---------------------------------------------------------------------------

.PHONY: install
install: ## Install cardano_node_tests and its dependencies into a virtual environment
	./scripts/setup_dev_venv.sh

.PHONY: check-dev-env
check-dev-env: ## Check if development environment is set up correctly
	@./scripts/check_dev_env.sh

.PHONY: update-node-bins
update-node-bins: ## Update cardano-node binaries from a given git repository (usage: make update-node-bins repo=/path/to/cardano-node-repo)
	@if [ -z "$(repo)" ]; then \
		echo "Usage: make update-node-bins repo=/path/to/cardano-node-repo" >&2; \
		exit 1; \
	fi
	@./scripts/update_node_bins.sh "$(repo)"

.PHONY: reinstall-editable
reinstall-editable: ## Reinstall python package in editable mode from a given git repository (usage: make reinstall-editable repo=/path/to/package_root)
	@if [ -z "$(repo)" ]; then \
		echo "Usage: make reinstall-editable repo=/path/to/package_root" >&2; \
		exit 1; \
	fi
	@./scripts/reinstall_editable.sh "$(repo)"

.PHONY: test-env
test-env: ## Set up test environment (variant: TESTNET_VARIANT=conway_fast)
	@./scripts/setup_test_env.sh $(TESTNET_VARIANT:%_fast=%)

# prepare cluster scripts for the given variant
.PHONY: cluster-scripts
cluster-scripts: .check-venv-activated ## Prepare local testnet cluster scripts (variant: TESTNET_VARIANT=conway_fast)
	prepare-cluster-scripts -c -d dev_workdir/$(TESTNET_VARIANT) -t $(TESTNET_VARIANT)

# start the local testnet cluster
.PHONY: start-cluster
start-cluster: .check-venv-activated ## Start local testnet cluster (variant: TESTNET_VARIANT=conway_fast)
	@if [ ! -x "dev_workdir/$(TESTNET_VARIANT)/start-cluster" ]; then \
		echo "Error: dev_workdir/$(TESTNET_VARIANT)/start-cluster not found." >&2; \
		echo "Run 'make cluster-scripts' first." >&2; \
		exit 1; \
	fi
	./dev_workdir/$(TESTNET_VARIANT)/start-cluster

# stop the local testnet cluster
.PHONY: stop-cluster
stop-cluster: .check-venv-activated ## Stop local testnet cluster (variant: TESTNET_VARIANT=conway_fast)
	@if [ ! -x "dev_workdir/$(TESTNET_VARIANT)/stop-cluster" ]; then \
		echo "Error: dev_workdir/$(TESTNET_VARIANT)/stop-cluster not found." >&2; \
		echo "Run 'make cluster-scripts' first." >&2; \
		exit 1; \
	fi
	./dev_workdir/$(TESTNET_VARIANT)/stop-cluster

## ---------------------------------------------------------------------------
## Linting
## ---------------------------------------------------------------------------

.PHONY: init-lint
init-lint: .check-venv-exists ## Initialize linters
	$(VENV)/bin/pre-commit clean
	$(VENV)/bin/pre-commit gc
	find . -path '*/.mypy_cache/*' -delete
	$(VENV)/bin/pre-commit uninstall
	$(VENV)/bin/pre-commit install --install-hooks

.PHONY: lint
lint: .check-venv-exists ## Run linters
	$(VENV)/bin/pre-commit run -a --show-diff-on-failure --color=always

.PHONY: fmt
fmt: .check-venv-exists ## Format code with ruff
	$(VENV)/bin/pre-commit run ruff-check -a
	$(VENV)/bin/pre-commit run ruff-format -a

## ---------------------------------------------------------------------------
## Documentation
## ---------------------------------------------------------------------------

.PHONY: build-doc
build-doc: .check-venv-activated ## Build sphinx documentation
	mkdir -p src_docs/build
	$(MAKE) -C src_docs clean
	$(MAKE) -C src_docs html

.PHONY: doc
doc: ## Build and deploy sphinx documentation
	./scripts/deploy_doc.sh

## ---------------------------------------------------------------------------
## Maintenance
## ---------------------------------------------------------------------------

# update flake.lock
.PHONY: update-flake-lock
update-flake-lock: ## Update flake.lock
	nix flake update --accept-flake-config

.PHONY: update-uv-lock
update-uv-lock: ## Update uv lockfile
	@exit_code=0; \
	./scripts/uv_update_lock.sh || exit_code=$$?; \
	if [ $$exit_code -ne 0 ] && [ $$exit_code -ne 10 ]; then \
		echo "uv lockfile update failed. Retrying without cache..." >&2; \
		./scripts/uv_update_lock.sh --refresh; \
	else \
		exit $$exit_code; \
	fi

.PHONY: clean
clean: ## Clean build artifacts and caches
	find . -type d -name __pycache__ -not -path './$(VENV)/*' -exec rm -rf {} +
	find . -type d -name .pytest_cache -not -path './$(VENV)/*' -exec rm -rf {} +
	find . -type d -name .mypy_cache -not -path './$(VENV)/*' -exec rm -rf {} +
	find . -type d -name '*.egg-info' -not -path './$(VENV)/*' -exec rm -rf {} +
	find . -name '*.pyc' -not -path './$(VENV)/*' -delete

.PHONY: clean-all
clean-all: .check-venv-not-activated clean ## Clean all build artifacts, caches, and virtual environment
	@echo "Removing virtual environment: $(VENV)"
	rm -rf -- "$(VENV)"

## ---------------------------------------------------------------------------
## Help
## ---------------------------------------------------------------------------

.PHONY: help
help: ## Show this help message
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} \
		/^## [A-Z][a-zA-Z]*$$/ { section = substr($$0, 4); next } \
		/^[a-zA-Z_-]+:.*##/ { \
			if (section != last_section) { \
				printf "\n\033[1m%s\033[0m\n", section; \
				last_section = section; \
			} \
			printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2; \
		}' \
		$(MAKEFILE_LIST)
