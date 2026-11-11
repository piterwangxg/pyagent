# 桌面端打包

这套流程只干一件事：把 `wy-agent-desktop` 这个 Electron 壳子打成 Windows 安装包。后端那套 Python 服务不在里面，原因和处理方式放到最后说。

## 前置

打包工具是 electron-builder，已经写进 `devDependencies`，克隆下来 `npm install` 就有。Node 用的 v26，能跑；Electron 45 的二进制在装依赖时就下过了，`node_modules/electron/dist` 里有东西就说明齐了。

## 怎么打

在 `wy-agent-desktop` 目录下执行。代码块里那两行镜像环境变量必须设，不然 electron-builder 去 GitHub 拉 NSIS 和 winCodeSign 会一直卡着，最后超时报错：

```powershell
$env:ELECTRON_MIRROR='https://npmmirror.com/mirrors/electron/'
$env:ELECTRON_BUILDER_BINARIES_MIRROR='https://npmmirror.com/mirrors/electron-builder-binaries/'
npm.cmd run dist
```

第一次跑要下载 Electron 的 zip 和 NSIS 资源，几分钟。之后走缓存，几十秒就完。

平时调界面用 `npm.cmd run pack`，只出解包目录不生成安装程序，跑得快，改完直接开 `dist\win-unpacked\WY Agent.exe` 看效果。

PowerShell 里 npm 得写 `npm.cmd`。直接敲 `npm` 有概率被 `.ps1` 那层拦下来，报“无法加载文件 npm.ps1，因为在此系统上禁止运行脚本”，这是执行策略的事，跟 npm 本身没关系。

构建过程里如果冒出红色的 `NativeCommandError`、命令返回码是 1，先别急着找错。PowerShell 会把 npm 和 git 写到 stderr 的进度信息当成错误，实际构建可能已经成功了，看最后有没有 `building block map` 那一行更准。

## 产物

`dist` 下面有三个东西。`WY Agent Setup 1.0.0.exe` 是给别人的安装包，NSIS 向导，能选安装目录，装完会在桌面和开始菜单建 `WY Agent` 快捷方式。`win-unpacked` 是解包后的免安装版本，整个目录拷走就能跑，我一般拿它验包。剩下 `builder-debug.yml` 和 `.blockmap` 是构建中间件，发给别人时不用带。

dist 已经加进 `.gitignore` 了，一百多兆的东西不该进仓库。

## 常改的几处

要动的基本都在 `package.json` 的 `build` 字段里。`appId` 和 `productName` 决定安装后的程序名和系统里的标识，装过之后再改会变成两个程序，动手前想清楚。`files` 现在列了 5 个前端文件加 2 个图标，新加文件记得补进去，漏了的话运行起来就是找不到模块。

`nsis.oneClick` 现在是 false，意思是给用户看安装向导、允许换目录。想做成双击直接装完，改成 true。

版本号在顶层的 `version`，安装包文件名跟着它走，发新版本前记得调。

## 图标、托盘和签名

图标分了两个位置。`build/icon.ico` 给安装包和 exe 用，electron-builder 默认从这儿读，不用写进 files。`assets/tray.ico` 是右下角托盘那个小图标，主进程 `new Tray(...)` 直接加载，所以它必须列在 `package.json` 的 files 里，漏了的话程序一启动就崩，报找不到图标。两个都是 `assets/icon.png`（512×512，主色 `#5b8cff` 圆角块加白 W）导出来的，换样式改这三处就行。

托盘现在的行为：关窗口不算退出，只是收起来；左键点图标显示或隐藏；右键菜单里才有真正的“退出”。另外加了单实例锁，重复双击快捷方式只会把已有窗口拉出来，不会多开一个图标。

代码签名还没配，所以安装时 SmartScreen 会拦一下，提示“未知发布者”，点“更多信息”再点“仍要运行”能过去。自己机器上无所谓，要发给别人就得准备证书。

## 后端为什么不在这包里

这一包只有前端。桌面端连的是 `127.0.0.1:8000` 的 FastAPI 服务，装到没跑后端的机器上，界面一开就是“后端未连接”，模型配置和对话全用不了。

两条路。让对方自己装 Python、`pip install -r requirements.txt`、起 uvicorn，适合只有自己用。或者把后端也打进去：PyInstaller 把 `main.py` 打成 exe，放进 `extraResources`，主进程启动时 spawn 起来、退出时杀掉。

后一种包会大二三十兆，但用户装完是真能用的。真要做的话有两个坑要提前处理，PyInstaller 打 uvicorn 得显式收集 `uvicorn` 的 import，不然跑起来缺模块；SQLite 的 `data/app.db` 不能留在 asar 里，asar 是只读的，得挪到 `app.getPath('userData')` 下面去。
