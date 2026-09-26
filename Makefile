PYTHON ?= python3
PYINSTALLER ?= pyinstaller

.PHONY: help install test lint fmt samples zipapp portable build clean ci

help:
	@echo "make install   安装到当前环境（含 dev 依赖）"
	@echo "make test      运行单元测试"
	@echo "make lint      ruff 检查"
	@echo "make fmt       自动格式化"
	@echo "make samples   生成合成镜像到 samples/"
	@echo "make zipapp    打成单文件 veritpath.pyz（任何 python3 可跑）"
	@echo "make portable  产出 Android/Linux 通用包 dist/veritpath-portable/"
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

zipapp:
	$(PYTHON) scripts/make_zipapp.py dist/veritpath.pyz

portable:
	bash scripts/build-android.sh

build:
	$(PYTHON) -m pip install pyinstaller
	bash scripts/build.sh dist

ci: lint test

clean:
	rm -rf build dist samples *.egg-info .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
