PYTHON ?= python3
EMACS ?= emacs

.PHONY: check deb clean

check:
	$(PYTHON) -m unittest discover -s tests -v
	$(EMACS) --batch -Q -L emacs -l tests/chatgpt-web-tests.el -f ert-run-tests-batch-and-exit

deb:
	$(PYTHON) tools/build-deb.py

clean:
	$(PYTHON) -c "from pathlib import Path; import shutil; shutil.rmtree('dist', ignore_errors=True); [p.unlink() for p in Path('emacs').glob('*.elc')]; [shutil.rmtree(p) for p in Path('.').rglob('__pycache__')]"
