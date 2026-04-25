# YUI Render 命令使用说明

本文档说明 `src/main.py` 的渲染命令（render command）与常用开关（flags），用于团队协作时统一执行标准。

## 前置条件

- 已安装 Python（python）与依赖（dependencies）
- 已安装 Blender（blender）
- 项目目录为 `YUI_Render_System/`
- 资产目录（assets）中包含可审计的 `.3dm` 文件

## 统一入口

所有命令都通过以下入口执行：

`python src/main.py render ...`

该入口会先执行审计（audit），通过后才启动 Blender 自动流水线（pipeline）。

## 四条常用命令与含义

### 1) 标准流程

```bash
python src/main.py render --phase auto
```

- 用途：默认渲染流程（default workflow）
- 行为：
  - 先审计（audit）
  - 通过后启动 Blender 自动流程
  - 自动识别协议阶段（Phase2 / Phase3）

### 2) 保留灯光/相机流程

```bash
python src/main.py render --phase auto --keep
```

- 用途：调材质、调构图时减少重复布光（lighting）
- 行为：
  - 清理场景时保留灯光（light）和相机（camera）
  - 仅更新几何体（geometry）

### 3) 严格制造源校验流程

```bash
python src/main.py render --phase auto --strict-stp
```

- 用途：出图前确保制造源文件到位（manufacturing source）
- 行为：
  - 必须检测到同名 `.stp` 文件
  - 若缺失则直接失败并终止

### 4) 保留 + 严格双开流程

```bash
python src/main.py render --phase auto --keep --strict-stp
```

- 用途：在保留布光前提下执行严格交付检查（delivery check）
- 行为：
  - 保留灯光/相机
  - 同时强制 `.stp` 同名校验

## 参数补充说明

- `--phase auto`：按文件名自动选择 Phase2/Phase3 审计协议（protocol）
- `--keep`：保留灯光与相机，仅更新几何体
- `--strict-stp`：强制要求同名 `.stp` 存在
- `--blender "D:\Blender\blender.exe"`：手动指定 Blender 路径（path），仅在自动识别失败时使用

## 推荐使用策略

- 日常调试（debug）：`--keep`
- 正式交付（release）：`--strict-stp`
- 交付前最终检查：`--keep --strict-stp`

## 常见问题（FAQ）

### Q1：为什么 render 没有直接启动？

A：`render` 会先做审计（audit）。若审计失败，流程会在 Blender 启动前终止。

### Q2：为什么提示找不到 Blender？

A：可先确认本机路径是否为 `D:\Blender\blender.exe`。若自动识别异常，临时加：

```bash
python src/main.py render --phase auto --blender "D:\Blender\blender.exe"
```

### Q3：为什么开启 `--strict-stp` 后失败？

A：说明 `assets/` 中没有与目标 `.3dm` 同名的 `.stp` 文件。补齐后重试即可。

