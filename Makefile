PYTHON ?= python3
PIP := $(PYTHON) -m pip
PACKAGE_NAME := hyperliquid-cli-python
BASHRC ?= $(HOME)/.bashrc
COMPLETION_LINE := eval "$$(hl completion bash)" \# hl-cli-completion

.PHONY: help install uninstall completion test

help:
	@printf '%s\n' \
		'make install    Install the package and add bash completion to ~/.bashrc' \
		'make uninstall  Uninstall the package and remove bash completion from ~/.bashrc' \
		'make completion Add bash completion to ~/.bashrc if missing' \
		'make test       Run test suite'

install:
	$(PIP) install .
	$(MAKE) completion
	hash -r

completion:
	touch "$(BASHRC)"
	grep -Fqx '$(COMPLETION_LINE)' "$(BASHRC)" || printf '%s\n' '$(COMPLETION_LINE)' >> "$(BASHRC)"

uninstall:
	-$(PIP) uninstall -y $(PACKAGE_NAME)
	touch "$(BASHRC)"
	sed -i '\|^eval "$$(hl completion bash)" # hl-cli-completion$$|d' "$(BASHRC)"
	hash -r

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -p 'test_*.py' -v
