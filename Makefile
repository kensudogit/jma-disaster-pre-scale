.PHONY: test test-stdlib fmt tf-init tf-plan tf-apply package clean

# pytest がある環境
test:
	pytest

# pytest が無い環境(標準ライブラリのみ)
test-stdlib:
	python3 tests/run_tests.py

fmt:
	terraform -chdir=terraform fmt -recursive

tf-init:
	terraform -chdir=terraform init

tf-plan:
	terraform -chdir=terraform plan

tf-apply:
	terraform -chdir=terraform apply

package:
	cd src && zip -r ../.build/jma_pre_scale.zip . -x '*__pycache__*' '*.pyc'

clean:
	rm -rf .build terraform/.build
	find . -name __pycache__ -type d -exec rm -rf {} +
