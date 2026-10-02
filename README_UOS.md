# PDF 转 TXT · 统信 UOS 版使用说明

> 这个程序原本是在 Windows 上写成 `.exe` 的。但 **`.exe` 只能在 Windows 上跑，  
> Linux / 统信 UOS 跑不了**。所以 UOS 版的做法是：在 UOS（ARM 芯片）上用同一份代码跑起来。

> ✅ **已验证支持你的架构（aarch64 / ARM，华为鲲鹏那类）**。  
> 现在有三种用法，**第一种最简单，推荐直接用第一种**：

| 方法 | 麻烦程度 | 说明 |
| --- | --- | --- |
| **方法零：下现成文件** | ⭐ 最省事 | 作者发版时已经替你在云端把「单个文件」打好了，下载就能用 |
| 方法一：跑源码 | ⭐⭐ | 把源码拷过去，装一次依赖，敲命令运行 |
| 方法二：自己打包 | ⭐⭐⭐ | 在 UOS 上 `bash build_uos.sh` 本地打成单个文件（和作者打的一样） |

> ⚠️ 唯一要注意的：OCR（扫描件识别）用的 `onnxruntime` 需要系统 **glibc ≥ 2.28**。  
> 统信 UOS 20 / 1050 / 1070 这套都是 glibc 2.28 以上，没问题；  
> 如果你的 UOS 特别老（glibc 低于 2.28），OCR 装不上，但**纯文字 PDF 提取照样能用**。

---

## 方法零：下载现成的单个文件（最简单，强烈推荐）

作者每次发新版，都会自动在云端（GitHub Actions，用真 ARM 机器）把 UOS 能直接跑的  
**单个文件 `PDFtoTXT_linux_aarch64`** 打出来、挂到发布页、并写进更新清单。你**不用自己装任何东西、不用打包**。

### 第 1 步：去发布页下载

打开（用浏览器）：

```
https://github.com/wang1357441/pdf2txt-dist/releases
```

找到最新的版本（比如 `v1.0.7`），在「Assets（附件）」里下载这一项：

```
PDFtoTXT_linux_aarch64
```

（如果下载慢，发布页里也提供了国内镜像源，点程序里「检查更新」时它会自动挑最快的；  
手动下载也可以试试把上面的地址里的 `github.com` 前面加 `https://ghproxy.net/` 这种加速前缀。）

### 第 2 步：放到 UOS 上，给执行权限，运行

把下下来的 `PDFtoTXT_linux_aarch64` 拷到 UOS 任意地方（比如「文档」里）。  
打开 UOS 终端，cd 到它所在目录，然后：

```bash
chmod +x PDFtoTXT_linux_aarch64
./PDFtoTXT_linux_aarch64
```

图形界面就出来了，和 Windows 版一模一样：选 PDF → 转 → 出 TXT。

> 以后想用，双击它（或终端里 `./PDFtoTXT_linux_aarch64`）就行。  
> 它把所有依赖都打进自己这一个文件里了，拷到任何同架构（aarch64）的 UOS 上都能直接跑，**不用再装任何东西**。

> ⚠️ 如果双击没反应 / 提示「无法执行」，多半是少了执行权限——重跑一遍上面的 `chmod +x` 即可。

---

## 方法一：直接跑源码（想看/改代码、或下不到现成文件时）

适合你只是自己用、不想折腾打包的情况。**一次装好依赖，以后双击/敲命令就能用。**

> ⚠️ 这一步需要你 UOS 上的 Python 是 **3.10 或 3.11**（`requirements.txt` 里锁的 OCR 依赖  
> `rapidocr_onnxruntime==1.3.4` 暂时不支持 3.12）。如果你的 UOS 自带 3.12，  
> 最简单是直接用上面的「方法零」下现成文件，或者自己装个 3.10/3.11 的虚拟环境。

### 第 1 步：把文件夹搬过去

把整个 `pdf2txt` 文件夹（里面要有 `pdf2txt.py`、`requirements.txt`、`app.png`、`app.ico`、`make_icon.py`）  
复制到你的 UOS 电脑上任意地方，比如「文档」里。

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
>
> ```bash
> cd ~/文档/pdf2txt
> source venv_uos/bin/activate
> python3 pdf2txt.py
> ```
>
> 就行，不用再装依赖了。

---

## 方法二：打包成一个单独启动文件（自己本地打，等价于方法零的现成文件）

适合你想弄得跟 Windows 一样「双击就开」、又不想依赖作者发的现成文件。  
**和方法零得到的是同一个东西**——只是这次在你自己 UOS 上打。

> ⚠️ 同样需要本机 Python 是 3.10 / 3.11（原因同方法一）。  
> ⚠️ **必须在你的 UOS 本机上打包**，不要想在 Windows 上打出能跑在 ARM 上的文件——  
> PyInstaller 不能跨架构编译。

在终端里 cd 到文件夹后，直接：

```bash
bash build_uos.sh
```

脚本会自动：建环境 → 装依赖 → 用 PyInstaller 打成一个文件 `dist/PDFtoTXT`。  
打完之后，双击 `dist/PDFtoTXT` 就能用（也可以终端里 `./dist/PDFtoTXT`）。

---

## 常见问题

**Q：我的 UOS 是 ARM（鲲鹏、华为芯片）还是 x86_64？**  
A：绝大部分个人电脑（Intel / AMD）是 **x86_64**；部分华为鲲鹏机型是 **aarch64（ARM）**。  
如果是 x86_64 的 UOS，请用方法一/方法二（源码或本地打包），或者告诉我，我可以再补一个 x86_64 的现成文件。  
aarch64 机器用「方法零」的 `PDFtoTXT_linux_aarch64` 即可。  
`pip install -r requirements.txt` 会自动下载对应你机器架构的预编译包。已实测确认：本项目用到的依赖里——

- `pypdfium2`、`opencv-python-headless`、`Pillow`、`numpy` 都有 aarch64 包（glibc 2.17 起就能装）；
- `onnxruntime`（OCR 用的）aarch64 包要求 glibc 2.28，UOS 20 及以上都满足；
- `rapidocr_onnxruntime` 是纯 Python 包，跟架构无关，任何机器都能装。  

**Q：转出来的 TXT 跟 Windows 版一样干净吗？**  
A：一样。OCR 逻辑、文字层提取逻辑、去垃圾行逻辑都是同一份代码，跨平台通用。

**Q：文件夹批量模式在 UOS 上能用吗？**  
A：能。选「文件夹」模式，丢一个装 PDF 的文件夹进去，会生成 `<原文件夹名> TXT 版`  
这个输出文件夹，里面的子目录结构和原来一模一样。

**Q：打开结果文件时会不会弹不出？**  
A：Windows 用 `startfile`，UOS 自动改用 `xdg-open`（就是你系统默认打开 txt 的程序）。  
只要 UOS 里给 .txt 关联了文本编辑器，点「打开 TXT / 打开文件夹」就能开。

**Q：想删掉这个程序？**  
A：方法零/方法二下的单个文件，直接删那个文件就行；方法一下的文件夹，直接删 `pdf2txt` 文件夹即可。  
它只在工作目录里建了 `venv_uos` 和 `dist`，没有往系统里塞任何开机启动、没有偷偷联网、没有动你别的文件。

**Q：UOS 上怎么更新到新版？**  
A：程序窗口右上角有「检查更新」按钮。点一下会去读更新清单；有新版会提示你，  
点「下载新版」就会**自动从镜像站挑最快的源把新版本拉下来**（和 Windows 版一样，带 sha256 校验，  
防篡改），下完点「安装并更新」就会自己关掉、替换、重新打开——不会卡死，也不会偷偷动你别的文件。

- **如果你是用「方法零（现成单个文件）」装的**：更新时程序会下载作者发版时附好的预编译 Linux 二进制  
  （`PDFtoTXT_linux_aarch64`，每个版本作者都会自动打并附上），整包替换自己后重启。  
  **从 1.0.7 起，这个二进制是每次发版自动生成的，一定会附上**，所以不会出现「作者没附」的情况。
- **如果你是用「方法一（直接跑源码）」装的**：更新时程序会下载一个源码包（pdf2txt_source.tar.gz），  
  自动解压覆盖当前目录里的 `pdf2txt.py` 等文件，然后用原来的方式（`python3 pdf2txt.py`）重启。  
  一句话：点两下就更新好了，不用你再手动敲命令。

> ⚠️ 第一次把程序弄到 UOS 上时，请拿 **1.0.7 或更新** 的版本。只有 1.0.7 及以上的代码  
> 才认得「UOS 自动更新」这件事；更早的版本点了只会打开发布页（那次得手动）。  
> 清单格式和发布步骤见 `UPDATE.md`。

---

## 文件清单

发布页（Assets）里你会看到：

| 文件                      | 干嘛的                                             |
| ----------------------- | ------------------------------------------------ |
| `PDFtoTXT.exe`          | Windows 版（x86_64）                                 |
| `PDFtoTXT_linux_aarch64`| **UOS / ARM 现成单个文件（方法零用这个）**              |
| `pdf2txt_source.tar.gz` | UOS 源码包（方法一用这个，或在 Windows/Linux 上自己跑）   |

源码文件夹（GitHub 仓库里）里还有：

| 文件                    | 干嘛的                                       |
| --------------------- | ----------------------------------------- |
| `pdf2txt.py`          | 主程序（跨平台，Windows / UOS 通用）                 |
| `requirements.txt`    | 依赖清单（用的是「下限」写法，ARM 上 pip 会自动选 aarch64 轮子） |
| `build_uos.sh`        | UOS 上一键打包脚本（方法二用）                          |
| `app.png` / `app.ico` | 程序图标（Linux 用 png，Windows 用 ico）           |
| `make_icon.py`        | 重新生成图标的脚本（一般不用动）                          |
| `README_UOS.md`       | 就是本说明                                     |
| `build.py`            | Windows 上打包 `.exe` 用的（UOS 用不到）            |
