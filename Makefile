PYTHON ?= python3
PIP := $(PYTHON) -m pip
PACKAGE_NAME := hyperliquid-cli-python
BASHRC ?= $(HOME)/.bashrc
INSTALL_BIN ?= $(HOME)/.local/bin
VENV ?= .venv
VENV_PYTHON := $(VENV)/bin/python
BINARY_PATH := dist/hl-linux-x86_64
COMPLETION_LINE := eval "$$(hl completion bash)" \# hl-cli-completion

.PHONY: help install uninstall completion test binary venv clean

help:
	@printf '%s\n' \
		'make install    Build/install the single binary and add bash completion to ~/.bashrc' \
		'make uninstall  Remove installed binary/package and bash completion from ~/.bashrc' \
		'make completion Add bash completion to ~/.bashrc if missing' \
		'make binary     Build the Nuitka onefile binary at dist/hl-linux-x86_64' \
		'make clean      Remove build outputs and the local virtualenv' \
		'make test       Run test suite'

install: binary
	mkdir -p "$(INSTALL_BIN)"
	install -m 0755 "$(BINARY_PATH)" "$(INSTALL_BIN)/hl"
	$(MAKE) completion
	hash -r

completion:
	touch "$(BASHRC)"
	grep -Fqx '$(COMPLETION_LINE)' "$(BASHRC)" || printf '%s\n' '$(COMPLETION_LINE)' >> "$(BASHRC)"

uninstall:
	-rm -f "$(INSTALL_BIN)/hl"
	-$(PIP) uninstall -y $(PACKAGE_NAME)
	touch "$(BASHRC)"
	sed -i '\|^eval "$$(hl completion bash)" # hl-cli-completion$$|d' "$(BASHRC)"
	hash -r

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -p 'test_*.py' -v

venv:
	$(PYTHON) -m venv "$(VENV)"
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -e . nuitka zstandard

binary: venv
	PYTHONPATH=src $(VENV_PYTHON) -m nuitka \
		--onefile \
		--standalone \
		--output-filename=hl \
		--nofollow-import-to=parsimonious.tests \
		--include-package=hl_cli \
		--include-package=hyperliquid \
		--include-package=eth_account \
		--include-package=eth_abi \
		--include-package=eth_utils \
		--include-package=eth_typing \
		--include-package=parsimonious \
		--include-package=regex \
		--include-package=rich \
		hl_nuitka_entry.py
	mkdir -p dist
	cp hl "$(BINARY_PATH)"

clean:
	rm -rf "$(VENV)" build dist *.build *.dist *.onefile-build hl hl.exe hl.bin
