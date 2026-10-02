# PDF 转 TXT · 统信 UOS 版使用说明

> 这个程序原本是在 Windows 上写成 `.exe` 的。但 **`.exe` 只能在 Windows 上跑，
> Linux / 统信 UOS 跑不了**。所以 UOS 版的做法是：把同一份源代码原样搬到你的
> UOS 机器上，**在 UOS 上本地运行 / 打包**。代码我已经改成了跨平台（打开文件用
> `xdg-open`，图标用 PNG），在 UOS 上和 Windows 上长得一样、用得一样。

> ✅ **已验证支持你的架构（aarch64 / ARM，华为鲲鹏那类）**：两种方法都能用。
> - 方法一（跑源码）：依赖全部有 aarch64 预编译包，自动装好。
> - 方法二（打包成单个文件）：PyInstaller 6.22.2 自带 aarch64 启动器，
>   能在你的 ARM 机器上打出 `dist/PDFtoTXT`。
> - ⚠️ 唯一要注意的：OCR（扫描件识别）用的 `onnxruntime` 需要系统 **glibc ≥ 2.28**。
>   统信 UOS 20 / 1050 / 1070 这套都是 glibc 2.28 以上，没问题；
>   如果你的 UOS 特别老（glibc 低于 2.28），OCR 装不上，但**纯文字 PDF 提取照样能用**。

---

## 方法一：直接跑源码（最简单，推荐先试这个）

适合你只是自己用、不想折腾打包的情况。**一次装好依赖，以后双击/敲命令就能用。**

### 第 1 步：把文件夹搬过去
把整个 `pdf2txt` 文件夹（里面要有 `pdf2txt.py`、`requirements.txt`、`app.png`、
`app.ico`、`make_icon.py`）复制到你的 UOS 电脑上任意地方，比如「文档」里。

### 第 2 步：装两个系统组件（一次性）
打开 UOS 的终端（开始菜单搜「终端」），敲：

```bash
sudo apt update
sudo apt install python3-tk python3-venv python3-pip
```

- `python3-tk`：图形界面必须的。
- 如果提示 `sudo` 没权限，问问你电脑的管理员（或自己就是管理员就直接敲密码）。

### 第 3 步：建环境 + 装依赖（一次性）
在终端里 **先 cd 到那个文件夹**，比如：

```bash
cd ~/文档/pdf2txt
python3 -m venv venv_uos
source venv_uos/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

装完依赖，命令行前面会出现 `(venv_uos)` 字样，说明进到环境里了。

### 第 4 步：运行
还在那个环境里，敲：

```bash
python3 pdf2txt.py
```

界面就出来了，和 Windows 版一模一样：选 PDF → 转 → 出 TXT。

> 以后每次想用，只要：
> ```bash
> cd ~/文档/pdf2txt
> source venv_uos/bin/activate
> python3 pdf2txt.py
> ```
> 就行，不用再装依赖了。

---

## 方法二：打包成一个单独启动文件（双击就能开，像 Windows 的 .exe）

适合你想弄得跟 Windows 一样「双击就开」、不高兴每次敲命令。

在终端里 cd 到文件夹后，直接：

```bash
bash build_uos.sh
```

脚本会自动：建环境 → 装依赖 → 用 PyInstaller 打成一个文件 `dist/PDFtoTXT`。
打完之后，双击 `dist/PDFtoTXT` 就能用（也可以终端里 `./dist/PDFtoTXT`）。

> ⚠️ 打包前请确保第 2 步的 `python3-tk` / `python3-venv` 已经装好，否则脚本会提示你。
> ⚠️ **必须在你的 UOS 本机上打包**，不要想在 Windows 上打出能跑在 ARM 上的文件——
> PyInstaller 不能跨架构编译。你复制到 UOS 的那份源码，在 UOS 终端里 `bash build_uos.sh` 即可。

---

## 常见问题

**Q：我的 UOS 是 32 位 / ARM（鲲鹏、华为芯片）还是 x86_64？**
A：绝大部分个人电脑（Intel / AMD）是 **x86_64**；部分华为鲲鹏机型是 **aarch64（ARM）**。
不用你手动选——`pip install -r requirements.txt` 会自动下载对应你机器架构的预编译包。
已实测确认：本项目用到的依赖里——
- `pypdfium2`、`opencv-python-headless`、`Pillow`、`numpy` 都有 aarch64 包（glibc 2.17 起就能装）；
- `onnxruntime`（OCR 用的）aarch64 包要求 glibc 2.28，UOS 20 及以上都满足；
- `rapidocr_onnxruntime` 是纯 Python 包，跟架构无关，任何机器都能装。
所以你的 aarch64 机器两种方法都能用。如果装的时候报「找不到匹配的包」，把报错发我，我帮你换对应架构的版本。

**Q：转出来的 TXT 跟 Windows 版一样干净吗？**
A：一样。OCR 逻辑、文字层提取逻辑、去垃圾行逻辑都是同一份代码，跨平台通用。

**Q：文件夹批量模式在 UOS 上能用吗？**
A：能。选「文件夹」模式，丢一个装 PDF 的文件夹进去，会生成 `<原文件夹名> TXT 版`
这个输出文件夹，里面的子目录结构和原来一模一样。

**Q：打开结果文件时会不会弹不出？**
A：Windows 用 `startfile`，UOS 自动改用 `xdg-open`（就是你系统默认打开 txt 的程序）。
只要 UOS 里给 .txt 关联了文本编辑器，点「打开 TXT / 打开文件夹」就能开。

**Q：想删掉这个程序？**
A：直接删 `pdf2txt` 文件夹就行。它只在自己文件夹里建了 `venv_uos` 和 `dist`，
没有往系统里塞任何开机启动、没有偷偷联网、没有动你别的文件。

**Q：UOS 上怎么更新到新版？**
A：程序窗口右上角有「检查更新」按钮。点一下会去读更新清单；有新版会提示你，
点「下载新版」就会**自动从镜像站挑最快的源把新版本拉下来**（和 Windows 版一样，带 sha256 校验，
防篡改），下完点「安装并更新」就会自己关掉、替换、重新打开——不会卡死，也不会偷偷动你别的文件。

- **如果你是用「方法一（直接跑源码）」装的**：更新时程序会下载一个源码包（pdf2txt_source.tar.gz），
  自动解压覆盖当前目录里的 `pdf2txt.py` 等文件，然后用原来的方式（`python3 pdf2txt.py`）重启。
  一句话：点两下就更新好了，不用你再手动敲命令。
- **如果你是用「方法二（打包成单个文件）」装的**：更新时程序会下载预编译的 Linux 二进制
  （PDFtoTXT_linux_aarch64，作者发版时附上才有），整包替换自己后重启。
  如果作者这次没附预编译二进制，它会退回去打开发布页，你照旧 `bash build_uos.sh` 重打一次即可。

> ⚠️ 第一次把程序弄到 UOS 上时，请拿 **1.0.7 或更新** 的源码（发布页里的
> `pdf2txt_source.tar.gz` 就是现成的源码包，下载解压即可）。只有 1.0.7 及以上的代码
> 才认得「UOS 自动更新」这件事；更早的版本点了只会打开发布页（那次得手动）。
> 清单格式和发布步骤见 `UPDATE.md`。

---

## 文件清单（这个文件夹里应该有的）

| 文件 | 干嘛的 |
|------|--------|
| `pdf2txt.py` | 主程序（跨平台，Windows / UOS 通用） |
| `requirements.txt` | 依赖清单（用的是「下限」写法，ARM 上 pip 会自动选 aarch64 轮子） |
| `build_uos.sh` | UOS 上一键打包脚本 |
| `app.png` / `app.ico` | 程序图标（Linux 用 png，Windows 用 ico） |
| `make_icon.py` | 重新生成图标的脚本（一般不用动） |
| `README_UOS.md` | 就是本说明 |
| `build.py` | Windows 上打包 `.exe` 用的（UOS 用不到） |
