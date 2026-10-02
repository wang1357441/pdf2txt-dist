#!/usr/bin/env bash
# ============================================================================
# 统信 UOS / Linux 原生打包脚本（一键出「单个可执行文件」）
# ----------------------------------------------------------------------------
# 这个脚本会在「你的 UOS（或任何 aarch64 Linux）机器上」把 pdf2txt 打包成
# 单独一个文件 dist/PDFtoTXT。这就是 UOS 版的「单个可执行文件」——双击就开，
# 所有依赖都打进去了，拷到别的同架构 UOS 上也不用再装任何东西。
#
# 为什么不在 Windows 这边打？因为 Windows 打的 .exe 是 x86 专用，ARM/鲲鹏跑不了；
# 反过来在 UOS 上打出来的，才能在 UOS 上跑（打包工具不能跨 CPU 架构编译）。
#
# 用法（在 UOS 的终端里，先 cd 到这个文件夹）：
#     bash build_uos.sh
# 就这么一行。脚本会自动：装系统零件 → 建环境 → 装依赖 → 打包出 dist/PDFtoTXT。
#
# ⚠️ 离线机器怎么办？
#    如果你的 UOS 电脑没网（政府内网常见），先在有网的 aarch64 Linux 上跑一次本脚本，
#    把生成的 dist/PDFtoTXT 这个「单个文件」拷到离线机器即可——它自带全部依赖，
#    离线也能直接跑，完全不用装 pip / venv 那些。
# ============================================================================

set -e

# 在 CI 容器（默认 root）或本机（需要 sudo）都能用：有 sudo 且不是 root 才加 sudo
if command -v sudo >/dev/null 2>&1 && [ "$(id -u)" -ne 0 ]; then
  SUDO="sudo"
else
  SUDO=""
fi

echo "==> 检查并安装系统组件（python3-tk / python3-venv / python3-pip，一次性）"
if ! python3 -c "import tkinter" 2>/dev/null || ! python3 -m venv --help >/dev/null 2>&1; then
  echo "    需要安装系统组件，可能要输入密码…"
  $SUDO apt update
  $SUDO apt install -y python3-tk python3-venv python3-pip
  echo "    系统组件装好了。"
else
  echo "    系统组件已就绪，跳过。"
fi

echo "==> 检查 Python 版本（需要 >= 3.9）"
PYVER=$(python3 -c "import sys; print('%d.%d' % sys.version_info[:2])")
echo "    当前 Python: $PYVER"
python3 - <<'PY'
import sys
if sys.version_info < (3, 9):
    sys.exit("你的 Python 太旧了，请先升级到 3.9 以上。")
PY

echo "==> 建一个干净的虚拟环境（不污染系统）"
if [ ! -d "venv_uos" ]; then
    python3 -m venv venv_uos
fi
# shellcheck disable=SC1091
source venv_uos/bin/activate

echo "==> 升级 pip 并安装依赖"
pip install --upgrade pip
pip install -r requirements.txt

echo "==> 用 PyInstaller 打包成单个文件 dist/PDFtoTXT"
pyinstaller --noconfirm --clean \
    --onefile --windowed \
    --name PDFtoTXT \
    --collect-data rapidocr_onnxruntime \
    --collect-data pypdfium2 \
    --hidden-import rapidocr_onnxruntime \
    --hidden-import cv2 \
    --hidden-import pypdfium2 \
    --add-data "app.png:." \
    --add-data "app.ico:." \
    pdf2txt.py

echo ""
echo "✅ 打包完成！单个可执行文件在： $(pwd)/dist/PDFtoTXT"
echo "   这就是「统信 UOS 版单个文件」——双击它，或在终端里跑： ./dist/PDFtoTXT"
echo "   它把所有依赖都打进去了，拷到任何同架构（aarch64）的 UOS 上都能直接跑，不用再装任何东西。"
echo "   想卸载？直接删掉 dist/PDFtoTXT 和 venv_uos 文件夹就行。"
