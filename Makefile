PYTHON ?= python3
PYINSTALLER ?= pyinstaller

.PHONY: help install test lint fmt samples build clean ci

help:
	@echo "make install   安装到当前环境（含 dev 依赖）"
	@echo "make test      运行单元测试"
	@echo "make lint      ruff 检查"
	@echo "make fmt       自动格式化"
	@echo "make samples   生成合成镜像到 samples/"
	@echo "make build     PyInstaller 打包单文件二进制到 dist/"
	@echo "make ci        等价于 lint + test"
	@echo "make clean     清理构建产物"

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest tests/ -q

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

fmt:
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m ruff format .

samples:
	$(PYTHON) scripts/make_sample_images.py samples

build:
	$(PYTHON) -m pip install pyinstaller
	bash scripts/build.sh dist

ci: lint test

clean:
	rm -rf build dist samples *.egg-info .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
